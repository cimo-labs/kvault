"""Human rendering of result notes and verbosity resolution.

This module is the ONLY place notes become printed text, and it is imported
ONLY by ``kvault.cli.*``. Nothing under ``kvault/core/`` or ``kvault/mcp/``
may write to a stream: over MCP, stdout is the JSON-RPC transport and a
single stray line corrupts the framing. Keeping every ``click.echo`` for
notes in this one CLI-side module makes that invariant structural.

JSON mode never routes through here — the notes ride inside the JSON
document itself, complete with ``why``/``next`` at every tier, and consumers
filter by ``level`` themselves.
"""

import os
from typing import Any, Dict, Optional

import click

from kvault.core import notes as nt

VERBOSITY_ENV = "KVAULT_VERBOSITY"

#: Note codes that mean "something needs human attention", used by --strict.
#: partial = a half-failure needing repair; skipped = data was silently
#: excluded from a result. A broken lock is warn-worthy too, but is detected
#: from its detail payload (see _is_warning) rather than its code.
WARN_CODES = ("partial", "skipped")


def resolve_tier(
    quiet: bool = False,
    explain: bool = False,
    trace: bool = False,
) -> int:
    """Flags > $KVAULT_VERBOSITY > normal.

    An unparseable env value silently means ``normal``: this variable may be
    set machine-wide (launchd, hooks), and a typo there must never change
    behaviour or produce noise.
    """
    if quiet and (explain or trace):
        raise click.UsageError("--quiet cannot be combined with --explain or --trace")
    if trace:
        return nt.TRACE
    if explain:
        return nt.EXPLAIN
    if quiet:
        return nt.QUIET
    env_tier = nt.tier_from_name(os.environ.get(VERBOSITY_ENV))
    return nt.NORMAL if env_tier is None else env_tier


def _is_warning(entry: Dict[str, Any]) -> bool:
    if entry.get("code") in WARN_CODES:
        return True
    if entry.get("code") == "waited" and (entry.get("detail") or {}).get("broke_stale"):
        return True
    return False


def render_notes(result: Dict[str, Any], tier: int) -> None:
    """Print the notes a reader at *tier* should see, indented under the receipt."""
    for entry in nt.visible(result.get("notes") or [], tier):
        if "count" in entry and "examples" in entry:
            # Collapsed batch form from notes.collapse().
            examples = "; ".join(str(e) for e in entry.get("examples") or [])
            suffix = f" — {examples}" if examples else ""
            extra = entry["count"] - len(entry.get("examples") or [])
            if extra > 0:
                suffix += f" (+{extra} more)"
            click.echo(f"  {entry['code']:<11} {entry['count']}×{suffix}")
            continue
        click.echo(f"  {entry['code']:<11} {entry.get('text', '')}")
        if tier >= nt.EXPLAIN and entry.get("why"):
            click.echo(f"  {'why':<11} {entry['why']}")
        if tier >= nt.EXPLAIN and entry.get("next"):
            click.echo(f"  {'next':<11} {entry['next']}")


def strict_exit_code(result: Dict[str, Any], strict: bool) -> Optional[int]:
    """Exit 3 under --strict when any warning-class note was emitted."""
    if not strict:
        return None
    if result.get("partial"):
        return 3
    if any(_is_warning(entry) for entry in result.get("notes") or []):
        return 3
    return None


__all__ = ["VERBOSITY_ENV", "WARN_CODES", "resolve_tier", "render_notes", "strict_exit_code"]
