"""Shared CLI utilities for kvault commands."""

import json
import sys
from pathlib import Path
from typing import Any, Optional

import click


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
        return root
    detected = find_kb_root()
    if detected is None:
        raise click.ClickException(
            "Could not find a kvault KB. Use --kb-root or run from inside a KB directory."
        )
    return detected


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
