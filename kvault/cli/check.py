"""``kvault check`` — the CLI face of ``kvault.core.check``.

Hard findings (exit 1) are collapsed into one ``[KB]`` line for hook use;
warn-class findings print one bounded group per prefix. The human output is
tier-invariant and ``--json`` is one document (frozen since 0.13). The
prefix vocabulary is ``[KB]``, ``SUMMARY:``, ``PENDING:``, ``RETRACTED:``
and, since 0.15, ``GHOST:``, ``SIBLINGS:``, ``LOOSE:``, ``JOURNAL:``.

Exit codes:
    0 = All hard checks pass (warn-class findings are warn-only)
    1 = Hard findings (minimal output for agent context)
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

from kvault.core import operations as ops
from kvault.core.check import (  # noqa: F401 — re-exported for callers and tests
    ALL_CODES,
    DEFAULT_MAX_CHILDREN,
    DEFAULT_MAX_FINDINGS,
    DEFAULT_PENDING_MAX_AGE,
    DEFAULT_THRESHOLD_MINUTES,
    STRUCTURE_CODES,
    _find_entities,
    _get_mtime,
    _get_updated_date,
    check_directory_size,
    check_frontmatter,
    check_journal,
    check_propagation,
    normalize_codes,
    run_checks,
)
from kvault.core.summary_quality import DEFAULT_MAX_DATED_SECTIONS

_STRUCTURE_FIX_LINE = {
    "GHOST": "write a summary (kvault write <path> --create) or list it in .kvaultignore",
    "SERIES": "chronology as nodes → kvault plan folds them under one current-state node's "
    "deep_context/; new entries go to journal/",
    "SIBLINGS": "same thing → merge; subtopic → kvault move; kvault plan lists the moves",
    "LOOSE": "move into <node>/deep_context/, make it a node, or list it in .kvaultignore",
    "JOURNAL": "one history: journal/YYYY-MM/log.md via kvault journal; a deliberate second "
    "layout goes in .kvaultignore",
}


def _find_kb_root() -> Optional[Path]:
    """Walk up from cwd looking for _summary.md + .kvault/."""
    current = Path.cwd()
    while current != current.parent:
        if (current / "_summary.md").exists() and (current / ".kvault").exists():
            return current
        current = current.parent
    return None


def _echo_group(
    code: str, findings: List[Dict[str, Any]], max_lines: Optional[int], hidden_extra: int = 0
) -> None:
    # A per-parent overflow marker ("+2,340 more colliding pairs") is a count,
    # not a finding; it prints after the capped lines instead of competing
    # with them for the cap. max_lines None = print every line.
    markers = [f for f in findings if (f.get("detail") or {}).get("kind") == "more_pairs"]
    regular = [f for f in findings if f not in markers]
    for finding in regular[:max_lines]:
        click.echo(f"{code}: {finding['path']} — {finding['message']}")
    shown = len(regular) if max_lines is None else min(len(regular), max_lines)
    hidden = len(regular) - shown + hidden_extra
    if hidden > 0:
        click.echo(f"{code}: (+{hidden} more)")
    for marker in markers:
        click.echo(f"{code}: {marker['path']} — {marker['message']}")
    if findings:
        click.echo(f"{code}: fix → {_STRUCTURE_FIX_LINE[code]}")


@click.command("check")
@click.option(
    "--kb-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Knowledge base root (auto-detected if not specified)",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option(
    "--threshold",
    type=int,
    default=DEFAULT_THRESHOLD_MINUTES,
    show_default=True,
    help="Staleness threshold in minutes",
)
@click.option(
    "--no-summary-quality",
    is_flag=True,
    help="Skip parent-summary quality warnings.",
)
@click.option(
    "--max-lines",
    "--summary-max-warnings",
    "max_lines",
    type=int,
    default=5,
    show_default=True,
    help="Warn-class lines to print per prefix (0 = all). --summary-max-warnings is the old name.",
)
@click.option(
    "--code",
    "codes",
    multiple=True,
    help=(
        "Run and report only these codes (repeatable or comma-separated), e.g. "
        "--code SIBLINGS or --code GHOST,LOOSE. Exit 1 only if a selected hard code fires."
    ),
)
@click.option(
    "--max-findings",
    type=int,
    default=DEFAULT_MAX_FINDINGS,
    show_default=True,
    help="Warn-class findings kept per code in the document (0 = all; --json and the line counts).",
)
@click.option(
    "--summary-max-words",
    type=int,
    default=None,
    help=(
        "Word ceiling for a parent summary (SUMMARY: too_long). Default: per-node "
        "formula min(2000, 1000 + 50*children + 5*descendants); 0 disables."
    ),
)
@click.option(
    "--summary-max-dated-sections",
    type=int,
    default=DEFAULT_MAX_DATED_SECTIONS,
    show_default=True,
    help="Dated/delta headings a parent summary may carry (SUMMARY: stale_history); 0 disables.",
)
@click.option(
    "--pending-max-age",
    type=int,
    default=DEFAULT_PENDING_MAX_AGE,
    show_default=True,
    help="Days a captured event may stay pending before it is flagged.",
)
@click.option(
    "--max-children",
    type=int,
    default=DEFAULT_MAX_CHILDREN,
    show_default=True,
    help="Direct-child ceiling for BRANCH: (the root counts too).",
)
@click.pass_context
def check_kb(
    ctx: click.Context,
    kb_root: Optional[Path],
    as_json: bool,
    threshold: int,
    no_summary_quality: bool,
    max_lines: int,
    codes: Tuple[str, ...],
    max_findings: int,
    summary_max_words: Optional[int],
    summary_max_dated_sections: int,
    pending_max_age: int,
    max_children: int,
) -> None:
    """Check KB integrity (propagation, journal, frontmatter, branching, structure)."""
    ctx.ensure_object(dict)
    if ctx.obj.get("strict"):
        # check's exit codes are already a contract (0 = hard checks pass,
        # 1 = hard warnings) consumed by hooks and scripts; --strict's exit 3
        # would silently change what those consumers see.
        raise click.ClickException(
            "--strict is not supported for check; its exit codes are already a contract"
        )
    explicit_root = kb_root or ctx.obj.get("kb_root")
    if as_json:
        ctx.obj["as_json"] = True
    try:
        selected = normalize_codes(codes)
    except ValueError as exc:
        if ctx.obj.get("as_json"):
            click.echo(
                json.dumps(
                    {"success": False, "error_code": "validation_error", "error": str(exc)},
                    indent=2,
                )
            )
            sys.exit(2)
        raise click.UsageError(str(exc))
    lines: Optional[int] = None if max_lines <= 0 else max_lines

    if explicit_root is None:
        kb_root = _find_kb_root()
        if kb_root is None:
            # Silent exit BY DESIGN: this command runs as a UserPromptSubmit hook in
            # every directory, so "there is no KB here" must stay quiet. Only an
            # EXPLICIT --kb-root is a hard error (2026-07-26 audit).
            sys.exit(0)
    else:
        kb_root = Path(explicit_root).resolve()
        if not kb_root.is_dir():
            raise click.ClickException(f"--kb-root does not exist or is not a directory: {kb_root}")
        if not (kb_root / "_summary.md").is_file():
            raise click.ClickException(
                f"--kb-root is not a kvault KB (no _summary.md at its root): {kb_root}"
            )

    allowed_error = ops.validate_allowed_root(kb_root)
    if allowed_error:
        raise click.ClickException(allowed_error)

    doc = run_checks(
        kb_root,
        threshold_minutes=threshold,
        summary_quality=not no_summary_quality,
        max_words=summary_max_words,
        max_dated_sections=summary_max_dated_sections,
        pending_max_age=pending_max_age,
        max_children=max_children,
        max_findings=max_findings,
        codes=selected,
    )
    hard_warnings: List[str] = doc["warnings"]

    if ctx.obj.get("as_json"):
        click.echo(json.dumps(doc, indent=2, default=str))
        sys.exit(1 if hard_warnings else 0)

    if hard_warnings:
        prop_warnings = [w for w in hard_warnings if w.startswith("PROPAGATE")]
        if prop_warnings:
            msg = f"[KB] Fix before continuing: {'; '.join(prop_warnings[:5])}"
            if len(prop_warnings) > 5:
                msg += f" (+{len(prop_warnings) - 5} more)"
            click.echo(msg)
        else:
            msg = f"[KB] {len(hard_warnings)} issues: {'; '.join(hard_warnings[:3])}"
            if len(hard_warnings) > 3:
                msg += f" (+{len(hard_warnings) - 3} more)"
            click.echo(msg)

    def _more(prefix: str, total: int) -> None:
        if lines is not None and total > lines:
            click.echo(f"{prefix}: (+{total - lines} more)")

    summary = doc["summary_warnings"]
    for issue in summary[:lines]:
        click.echo(f"SUMMARY: {issue['path']}: {issue['message']}")
    _more("SUMMARY", len(summary))

    # Warn-only, like SUMMARY: — a captured candidate that was never
    # promoted or explicitly resolved is unfinished maintenance work.
    pending = doc["pending_events"]
    for finding in pending[:lines]:
        click.echo(
            f"PENDING: {finding['event_id']} captured {str(finding['captured_at'])[:10]} "
            f"({finding['age_days']}d) — resolve with kvault write --event or "
            f"kvault events resolve"
        )
    _more("PENDING", len(pending))

    retracted = doc["retracted_refs"]
    for finding in retracted[:lines]:
        reason = str(finding.get("reason") or "")[:80]
        follow_up = finding.get("superseded_by") or "<id of the corrected capture>"
        click.echo(
            f"RETRACTED: {finding['path']} cites retracted {finding['event_id']} — {reason} — "
            f"rewrite the node, then write --event {follow_up} (drops the retracted ref)"
        )
    _more("RETRACTED", len(retracted))

    # 0.15: the structural set. One bounded group per prefix, fix line last.
    for code in STRUCTURE_CODES:
        group = [f for f in doc["structure_warnings"] if f["code"] == code]
        _echo_group(code, group, lines, doc["truncated"].get(code, 0))

    sys.exit(1 if hard_warnings else 0)
