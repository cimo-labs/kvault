"""Shared CLI utilities for kvault commands."""

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import click

from kvault.core import notes as nt
from kvault.core import operations as ops
from kvault.core.oplog import OpLog, oplog_disabled


def find_kb_root() -> Optional[Path]:
    """Walk up from cwd looking for _summary.md + .kvault/."""
    current = Path.cwd()
    while current != current.parent:
        if (current / "_summary.md").exists() and (current / ".kvault").exists():
            return current
        current = current.parent
    return None


def resolve_kb_root(ctx: click.Context, explicit: Optional[Path] = None) -> Path:
    """Use explicit ``--kb-root``, context object, or auto-detect. Raise on failure."""
    root = explicit or ctx.obj.get("kb_root")
    if root is not None:
        root = Path(root).resolve()
        if not root.exists():
            raise click.ClickException(f"KB root does not exist: {root}")
        allowed_error = ops.validate_allowed_root(root)
        if allowed_error:
            raise click.ClickException(allowed_error)
        return root
    detected = find_kb_root()
    if detected is None:
        raise click.ClickException(
            "Could not find a kvault KB. Use --kb-root or run from inside a KB directory."
        )
    allowed_error = ops.validate_allowed_root(detected)
    if allowed_error:
        raise click.ClickException(allowed_error)
    return detected


def apply_common_options(
    ctx: click.Context,
    kb_root: Optional[Path] = None,
    as_json: bool = False,
) -> None:
    """Apply command-level common option overrides to the group context."""
    ctx.ensure_object(dict)
    if kb_root is not None:
        ctx.obj["kb_root"] = kb_root
    if as_json:
        ctx.obj["as_json"] = True


def common_options(func: Any) -> Any:
    """Add common command-level options accepted after subcommands."""
    func = click.option("--json", "as_json", is_flag=True, help="Output as JSON")(func)
    func = click.option(
        "--kb-root",
        type=click.Path(path_type=Path),
        default=None,
        help="Knowledge base root (auto-detected if not specified)",
    )(func)
    return func


def verbosity_options(func: Any) -> Any:
    """Add the output-tier options accepted after subcommands.

    Mirrors the dual registration of ``--json``/``--kb-root``: the same flags
    exist on the group, so both ``kvault --explain write …`` and
    ``kvault write … --explain`` work.
    """
    func = click.option(
        "--strict", is_flag=True, help="Exit 3 if any warning-class note was emitted"
    )(func)
    func = click.option(
        "--trace", is_flag=True, help="Add cost/mechanics detail (implies --explain)"
    )(func)
    func = click.option("--explain", is_flag=True, help="Add reasoning and next steps")(func)
    func = click.option("-q", "--quiet", is_flag=True, help="Receipt and warnings only")(func)
    return func


def apply_verbosity_options(
    ctx: click.Context,
    quiet: bool = False,
    explain: bool = False,
    trace: bool = False,
    strict: bool = False,
) -> None:
    """Merge command-level verbosity flags into the group context.

    The conflicting-flags check runs HERE, at merge time, not at render time:
    rendering happens after the mutation, and a flag error must fail the
    command before it touches the KB. This also catches cross-level combos
    like ``kvault -q write --explain``.
    """
    ctx.ensure_object(dict)
    if quiet:
        ctx.obj["quiet"] = True
    if explain:
        ctx.obj["explain"] = True
    if trace:
        ctx.obj["trace"] = True
    if strict:
        ctx.obj["strict"] = True
    if ctx.obj.get("quiet") and (ctx.obj.get("explain") or ctx.obj.get("trace")):
        raise click.UsageError("--quiet cannot be combined with --explain or --trace")


def get_tier(ctx: click.Context) -> int:
    """Resolve the output tier for this invocation."""
    from kvault.cli.render import resolve_tier

    return resolve_tier(
        quiet=bool(ctx.obj.get("quiet")),
        explain=bool(ctx.obj.get("explain")),
        trace=bool(ctx.obj.get("trace")),
    )


def record_op(
    kb_root: Path,
    op: str,
    result: Dict[str, Any],
    started: Optional[float] = None,
) -> None:
    """Append a successful operation to the durable ops log.

    Runs BEFORE rendering/serialization so that a failed append can surface
    as a ``skipped`` note in the same output. A logging failure never fails
    the command — the KB mutation has already committed.
    """
    if not isinstance(result, dict) or not result.get("success"):
        return
    ms = None if started is None else (time.monotonic() - started) * 1000.0
    ok = OpLog(kb_root).append(op=op, result=result, ms=ms, surface="cli")
    if not ok and not oplog_disabled():
        nt.attach_note(
            result,
            nt.note(
                "skipped",
                "operation log unavailable (.kvault/logs.db unwritable or corrupt) — "
                "the KB operation itself succeeded",
                next_step="inspect .kvault/logs.db; deleting it lets kvault recreate it",
            ),
        )


def finish_op(ctx: click.Context, result: Dict[str, Any]) -> None:
    """Apply --strict at the end of a command (both output modes)."""
    from kvault.cli.render import strict_exit_code

    code = strict_exit_code(result, bool(ctx.obj.get("strict")))
    if code is not None:
        ctx.exit(code)


def read_stdin() -> str:
    """Read stdin content. Error if interactive TTY with no piped input."""
    if sys.stdin.isatty():
        raise click.ClickException("No input on stdin. Pipe content or use a heredoc.")
    return sys.stdin.read()


def read_stdin_json() -> Any:
    """Read and parse JSON from stdin."""
    raw = read_stdin()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON on stdin: {e}")


def output_json(data: Any) -> None:
    """Print JSON to stdout."""
    click.echo(json.dumps(data, indent=2, default=str))
