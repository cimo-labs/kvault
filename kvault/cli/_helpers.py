"""Shared CLI utilities for kvault commands."""

import json
import sys
from pathlib import Path
from typing import Any, Optional

import click

from kvault.core import operations as ops


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
