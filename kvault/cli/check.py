"""KB Integrity Checker for Claude Code UserPromptSubmit Hook.

Checks:
1. PROPAGATE: Parent summaries should be as recent as children
2. LOG: Journal should be updated if entities changed today
3. WRITE: Entities should have required frontmatter (source, aliases)
4. BRANCH: Directories with >10 children should be restructured

Exit codes:
    0 = All hard checks pass (summary-quality warnings are warn-only)
    1 = Hard warnings found (minimal output for Claude context)
"""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

import click

from kvault.core import operations as ops
from kvault.core.frontmatter import parse_frontmatter
from kvault.core.summary_quality import audit_summary_quality, format_summary_quality_warnings

DEFAULT_THRESHOLD_MINUTES = 5


def _get_mtime(path: Path) -> datetime:
    """Get modification time of a file."""
    return datetime.fromtimestamp(path.stat().st_mtime)


def _get_updated_date(path: Path) -> Optional[date]:
    """Parse frontmatter 'updated' (or 'created') field from a _summary.md file.

    Returns a date if found, None otherwise (caller should fall back to mtime).
    """
    try:
        content = path.read_text()
    except Exception:
        return None

    meta, _ = parse_frontmatter(content)
    if not meta:
        return None

    for field in ("updated", "created"):
        val = meta.get(field)
        if val is None:
            continue
        if isinstance(val, date):
            return val
        # Handle string dates like '2026-01-15'
        try:
            return datetime.strptime(str(val).strip("'\""), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue

    return None


def _find_kb_root() -> Optional[Path]:
    """Walk up from cwd looking for _summary.md + .kvault/."""
    current = Path.cwd()
    while current != current.parent:
        if (current / "_summary.md").exists() and (current / ".kvault").exists():
            return current
        current = current.parent
    return None


def _find_entities(kb_root: Path) -> List[Path]:
    """Find all entity _summary.md files (leaf nodes at depth >= 3)."""
    entities = []
    for summary in kb_root.rglob("_summary.md"):
        parent_dir = summary.parent
        rel_path = summary.relative_to(kb_root)
        depth = len(rel_path.parts)

        if parent_dir == kb_root:
            continue
        if depth < 3:
            continue

        has_child_summaries = any(
            (child / "_summary.md").exists()
            for child in parent_dir.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        )

        if not has_child_summaries:
            entities.append(summary)

    return entities


def check_propagation(kb_root: Path, threshold_minutes: int) -> List[str]:
    """Check if parent summaries are as recent as their children.

    Uses a two-layer strategy:
    1. Primary: Compare frontmatter 'updated' dates (survives git operations).
       If child's date is strictly after parent's date, flag it.
    2. Fallback: If either side has no frontmatter date, use file mtime
       with the threshold_minutes parameter.
    """
    warnings = []
    threshold = timedelta(minutes=threshold_minutes)

    for summary in kb_root.rglob("_summary.md"):
        parent_dir = summary.parent

        children = []
        for child_dir in parent_dir.iterdir():
            if child_dir.is_dir() and not child_dir.name.startswith("."):
                child_summary = child_dir / "_summary.md"
                if child_summary.exists():
                    children.append(child_summary)

        if not children:
            continue

        parent_date = _get_updated_date(summary)

        for child in children:
            child_date = _get_updated_date(child)

            stale = False
            detail = ""

            if child_date is not None and parent_date is not None:
                # Primary: frontmatter date comparison (day-level)
                if child_date > parent_date:
                    stale = True
                    detail = f"child updated {child_date}, parent updated {parent_date}"
            else:
                # Fallback: mtime comparison with threshold
                parent_mtime = _get_mtime(summary)
                child_mtime = _get_mtime(child)
                delta = child_mtime - parent_mtime
                if delta > threshold:
                    stale = True
                    detail = f"{int(delta.total_seconds()) // 60}m newer"

            if stale:
                rel_parent = summary.relative_to(kb_root)
                rel_child = child.relative_to(kb_root)
                parent_path = str(rel_parent)
                child_name = rel_child.parent.name
                warnings.append(f"PROPAGATE: edit {parent_path} ({child_name}/ is {detail})")

    return warnings


def check_journal(kb_root: Path) -> List[str]:
    """Check if journal was updated today (if entities were modified today)."""
    warnings = []
    today = date.today()

    entities_modified_today = []
    for entity in _find_entities(kb_root):
        if _get_mtime(entity).date() == today:
            entities_modified_today.append(entity)

    if not entities_modified_today:
        return []

    journal_file = kb_root / "journal" / today.strftime("%Y-%m") / "log.md"

    if not journal_file.exists() or _get_mtime(journal_file).date() != today:
        warnings.append(f"LOG: {len(entities_modified_today)} entities need journal")

    return warnings


def check_frontmatter(kb_root: Path) -> List[str]:
    """Check that entities have required frontmatter fields."""
    warnings = []
    required_fields = ["source", "aliases"]
    entities_with_issues = []

    for entity in _find_entities(kb_root):
        rel_path = entity.relative_to(kb_root)
        try:
            content = entity.read_text()
        except Exception:
            entities_with_issues.append(rel_path.parent.name)
            continue

        meta, _ = parse_frontmatter(content)

        if not meta:
            entities_with_issues.append(rel_path.parent.name)
            continue

        missing = [f for f in required_fields if f not in meta or meta[f] is None]
        if missing:
            entities_with_issues.append(rel_path.parent.name)

    if entities_with_issues:
        warnings.append(f"WRITE: {len(entities_with_issues)} entities need frontmatter")

    return warnings


def check_directory_size(kb_root: Path, max_children: int = 10) -> List[str]:
    """Check if any directory has more than max_children subdirectories."""
    warnings = []

    for summary in kb_root.rglob("_summary.md"):
        parent_dir = summary.parent
        if parent_dir == kb_root:
            continue

        child_dirs = [d for d in parent_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]

        if len(child_dirs) > max_children:
            rel_path = parent_dir.relative_to(kb_root)
            warnings.append(f"BRANCH: {rel_path} has {len(child_dirs)} children (>{max_children})")

    return warnings


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
    "--summary-max-warnings",
    type=int,
    default=5,
    show_default=True,
    help="Maximum summary-quality warnings to print.",
)
@click.pass_context
def check_kb(
    ctx: click.Context,
    kb_root: Optional[Path],
    as_json: bool,
    threshold: int,
    no_summary_quality: bool,
    summary_max_warnings: int,
) -> None:
    """Check KB integrity (propagation, journal, index, frontmatter, branching)."""
    ctx.ensure_object(dict)
    explicit_root = kb_root or ctx.obj.get("kb_root")
    if as_json:
        ctx.obj["as_json"] = True

    if explicit_root is None:
        kb_root = _find_kb_root()
        if kb_root is None:
            # Silent exit if no KB found
            sys.exit(0)
    else:
        kb_root = Path(explicit_root).resolve()

    if not kb_root.exists():
        sys.exit(0)

    allowed_error = ops.validate_allowed_root(kb_root)
    if allowed_error:
        raise click.ClickException(allowed_error)

    hard_warnings: List[str] = []
    hard_warnings.extend(check_propagation(kb_root, threshold))
    hard_warnings.extend(check_journal(kb_root))
    hard_warnings.extend(check_frontmatter(kb_root))
    hard_warnings.extend(check_directory_size(kb_root))

    summary_issues = [] if no_summary_quality else audit_summary_quality(kb_root)

    if ctx.obj.get("as_json"):
        click.echo(
            json.dumps(
                {
                    "success": len(hard_warnings) == 0,
                    "warnings": hard_warnings,
                    "warning_count": len(hard_warnings),
                    "summary_warnings": [
                        {
                            "path": issue.path,
                            "code": issue.code,
                            "message": issue.message,
                            "details": issue.details,
                        }
                        for issue in summary_issues
                    ],
                    "summary_warning_count": len(summary_issues),
                    "summary_quality_enabled": not no_summary_quality,
                },
                indent=2,
                default=str,
            )
        )
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

    for warning in format_summary_quality_warnings(
        summary_issues, max_warnings=summary_max_warnings
    ):
        click.echo(warning)

    if hard_warnings:
        sys.exit(1)
    sys.exit(0)
