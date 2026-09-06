"""``kvault doctor`` — a runtime/environment report that never fails.

The 0.14.0 audit found a deployed skill document describing 0.13.0 features
against a 0.12.1 runtime, with no way for an agent to notice: no version
flag, no version field in any output, no single command that says "this is
the kvault you are talking to, pointed at this KB, with these env knobs".

``doctor`` is what you run when things are broken, so it must not itself
break: every block is computed independently, a missing KB is a *finding*
(``kb.root: null``), and the exit code is always 0. KB-content diagnostics
(propagation, summary quality, pending events) stay in ``kvault check``.
"""

import os
import platform
import sys
from importlib import metadata
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

import click

import kvault
from kvault._version import __version__
from kvault.cli._helpers import apply_common_options, common_options, find_kb_root, output_json
from kvault.core import events as ev
from kvault.core import operations as ops

ENV_KEYS = (
    "KVAULT_KB_ROOT",
    "KVAULT_ALLOWED_ROOTS",
    "KVAULT_VERBOSITY",
    "KVAULT_SESSION",
    "KVAULT_OPS_LOG",
)

_OUTCOME_RETRACTED = "retracted"


def _resolve_root(ctx: click.Context, kb_root: Optional[Path]) -> Tuple[Optional[Path], str]:
    """Mirror ``resolve_kb_root`` without raising: (root, how it was found)."""
    explicit = kb_root or ctx.obj.get("kb_root")
    if explicit is not None:
        return Path(explicit).resolve(), "explicit"
    env_root = os.environ.get("KVAULT_KB_ROOT")
    if env_root:
        return Path(env_root).resolve(), "env"
    detected = find_kb_root()
    if detected is not None:
        return detected, "auto"
    return None, "none"


def _safe(block: Any) -> Any:
    """Run one report block; a failure becomes ``{"error": ...}`` instead of a traceback."""
    try:
        return block()
    except Exception as exc:  # doctor must never raise
        return {"error": f"{type(exc).__name__}: {exc}"}


def _python_block() -> Dict[str, Any]:
    return {"version": platform.python_version(), "executable": sys.executable}


def _install_block() -> Dict[str, Any]:
    try:
        click_version: Optional[str] = metadata.version("click")
    except metadata.PackageNotFoundError:
        click_version = None
    return {
        "location": str(Path(kvault.__file__).resolve().parent),
        "click": click_version,
        "mcp_extra": find_spec("mcp") is not None,
    }


def _events_block(root: Path) -> Dict[str, int]:
    counts = {"pending": 0, "resolved": 0, _OUTCOME_RETRACTED: 0}
    for event in ev.list_events(root)["events"]:
        outcome = (event.get("resolution") or {}).get("outcome")
        if outcome == _OUTCOME_RETRACTED:
            counts[_OUTCOME_RETRACTED] += 1
        elif event.get("status") == ev.STATUS_PENDING:
            counts["pending"] += 1
        else:
            counts["resolved"] += 1
    return counts


def _kb_block(root: Optional[Path], resolved_from: str) -> Dict[str, Any]:
    block: Dict[str, Any] = {
        "root": None if root is None else str(root),
        "resolved_from": resolved_from,
    }
    if root is None:
        block["is_kb"] = False
        return block
    root_summary = root / "_summary.md"
    kvault_dir = root / ".kvault"
    ops_log = kvault_dir / "logs.db"
    block["is_kb"] = root_summary.is_file() and kvault_dir.is_dir()
    block["root_summary_exists"] = root_summary.is_file()
    block["kvault_dir_exists"] = kvault_dir.is_dir()
    block["root_summary_chars"] = (
        len(root_summary.read_text(encoding="utf-8", errors="replace"))
        if root_summary.is_file()
        else 0
    )
    block["allowed_root_error"] = ops.validate_allowed_root(root)
    block["ops_log"] = {
        "path": str(ops_log),
        "exists": ops_log.is_file(),
        # os.access only: doctor performs no writes.
        "writable": (
            os.access(ops_log, os.W_OK) if ops_log.is_file() else os.access(kvault_dir, os.W_OK)
        ),
    }
    block["events"] = _safe(lambda: _events_block(root)) if block["is_kb"] else None
    return block


def doctor_report(ctx: click.Context, kb_root: Optional[Path]) -> Dict[str, Any]:
    """Build the full report. Never raises."""
    root, resolved_from = _safe(lambda: _resolve_root(ctx, kb_root)) or (None, "error")
    if isinstance(root, dict):  # _resolve_root itself failed
        root, resolved_from = None, "error"
    return {
        "version": __version__,
        "python": _safe(_python_block),
        "install": _safe(_install_block),
        "kb": _safe(lambda: _kb_block(root, resolved_from)),
        "env": {key: os.environ.get(key) for key in ENV_KEYS},
    }


def _flatten(prefix: str, value: Any) -> Iterator[Tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _flatten(f"{prefix}.{key}" if prefix else str(key), item)
    else:
        yield prefix, value


@click.command("doctor")
@common_options
@click.pass_context
def doctor(ctx: click.Context, kb_root: Optional[Path], as_json: bool) -> None:
    """Report the runtime: version, python, install, KB binding, env. Never fails."""
    apply_common_options(ctx, kb_root=kb_root, as_json=as_json)
    report = doctor_report(ctx, kb_root)
    if ctx.obj.get("as_json"):
        output_json(report)
        return
    for key, value in _flatten("", report):
        click.echo(f"{key}: {value}")


__all__ = ["doctor", "doctor_report"]
