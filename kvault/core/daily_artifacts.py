"""Daily artifact generation for kvault knowledge bases.

Creates a deterministic markdown artifact that captures:
1. Goals snapshot from root summary.
2. Near-future context from projects + recent journal entries.
3. Full people summary for deep person context.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from kvault.core.frontmatter import parse_frontmatter


@dataclass(frozen=True)
class DailyArtifactResult:
    """Result for daily artifact generation."""

    artifact_date: date
    path: Path
    content: str
    written: bool


def parse_iso_date(value: Optional[str]) -> date:
    """Parse YYYY-MM-DD date string, defaulting to today."""
    if value is None:
        return date.today()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Expected format: YYYY-MM-DD") from exc


def _read_markdown_body(path: Path) -> str:
    """Read markdown file and strip optional YAML frontmatter."""
    if not path.exists():
        return ""
    raw = path.read_text()
    _, body = parse_frontmatter(raw)
    return body.strip()


def _extract_section_by_keywords(markdown: str, keywords: Sequence[str]) -> str:
    """Extract the first heading section whose title matches one of the keywords."""
    if not markdown.strip():
        return ""

    lines = markdown.splitlines()
    heading_indexes: List[Tuple[int, str]] = []
    for idx, line in enumerate(lines):
        match = re.match(r"^#{1,6}\s+(.+)$", line.strip())
        if match:
            heading_indexes.append((idx, match.group(1).strip().lower()))

    if not heading_indexes:
        return ""

    heading_indexes.append((len(lines), ""))
    normalized_keywords = [k.lower() for k in keywords]

    for i in range(len(heading_indexes) - 1):
        start_idx, heading_text = heading_indexes[i]
        if not any(keyword in heading_text for keyword in normalized_keywords):
            continue
        end_idx = heading_indexes[i + 1][0]
        section = "\n".join(lines[start_idx:end_idx]).strip()
        if section:
            return section

    return ""


def _first_n_lines(markdown: str, max_lines: int) -> str:
    """Return the first N non-empty lines, preserving order."""
    if not markdown.strip():
        return ""
    lines = [line for line in markdown.splitlines() if line.strip()]
    return "\n".join(lines[:max_lines]).strip()


def _extract_journal_sections(journal_content: str, up_to: date) -> List[str]:
    """Extract dated journal sections up to a given date."""
    lines = journal_content.splitlines()
    section_starts = [idx for idx, line in enumerate(lines) if line.startswith("## ")]
    if not section_starts:
        return []

    section_starts.append(len(lines))
    sections: List[str] = []
    for i in range(len(section_starts) - 1):
        start = section_starts[i]
        end = section_starts[i + 1]
        heading = lines[start].strip()

        heading_match = re.match(r"^##\s+(\d{4}-\d{2}-\d{2})$", heading)
        if heading_match:
            entry_date = parse_iso_date(heading_match.group(1))
            if entry_date > up_to:
                continue

        section = "\n".join(lines[start:end]).strip()
        if section:
            sections.append(section)

    return sections


def _journal_candidates(kg_root: Path, artifact_date: date) -> Iterable[Path]:
    """Yield current month, then previous month journal logs."""
    current = kg_root / "journal" / artifact_date.strftime("%Y-%m") / "log.md"
    yield current

    previous_month_day = artifact_date.replace(day=1) - timedelta(days=1)
    previous = kg_root / "journal" / previous_month_day.strftime("%Y-%m") / "log.md"
    if previous != current:
        yield previous


def _recent_journal_excerpt(
    kg_root: Path, artifact_date: date, max_sections: int = 3
) -> Tuple[str, str]:
    """Return (path, excerpt) for most recent journal sections."""
    for path in _journal_candidates(kg_root, artifact_date):
        if not path.exists():
            continue
        content = _read_markdown_body(path)
        sections = _extract_journal_sections(content, up_to=artifact_date)
        if not sections:
            continue
        excerpt = "\n\n".join(sections[-max_sections:])
        return str(path.relative_to(kg_root)), excerpt.strip()
    return "", ""


def _fallback(text: str, message: str) -> str:
    """Return text or a markdown fallback message."""
    return text if text.strip() else message


def _build_daily_content(
    artifact_date: date,
    root_summary: str,
    people_summary: str,
    projects_summary: str,
    recent_journal: str,
    source_paths: Dict[str, str],
) -> str:
    """Render markdown for a daily artifact."""
    generated_at = datetime.now().isoformat(timespec="seconds")

    goal_section = _extract_section_by_keywords(
        root_summary,
        keywords=("goal", "priority", "focus", "objective", "north star"),
    )
    if not goal_section:
        goal_section = _first_n_lines(root_summary, max_lines=25)

    project_near_term = _extract_section_by_keywords(
        projects_summary,
        keywords=("next", "priority", "upcoming", "action", "roadmap", "plan"),
    )
    if not project_near_term:
        project_near_term = _first_n_lines(projects_summary, max_lines=30)

    lines = [
        "---",
        f"date: {artifact_date.isoformat()}",
        f"generated_at: {generated_at}",
        "source: kvault-daily-artifact",
        "aliases: []",
        "---",
        "",
        f"# Daily Artifact - {artifact_date.isoformat()}",
        "",
        "## Goals Snapshot",
        _fallback(goal_section, "_No goal-like section found in `_summary.md`._"),
        "",
        "## Near-Future Context",
        "### Project Signals",
        _fallback(project_near_term, "_No project summary found in `projects/_summary.md`._"),
        "",
        "### Recent Journal Signals",
        _fallback(recent_journal, "_No recent journal sections found._"),
        "",
        "## People Summary (Full)",
        _fallback(people_summary, "_No people summary found in `people/_summary.md`._"),
        "",
        "## Projects Summary",
        _fallback(projects_summary, "_No projects summary found in `projects/_summary.md`._"),
        "",
        "## Source Files",
        f"- Root summary: `{source_paths.get('root_summary', 'missing')}`",
        f"- People summary: `{source_paths.get('people_summary', 'missing')}`",
        f"- Projects summary: `{source_paths.get('projects_summary', 'missing')}`",
        f"- Journal excerpt source: `{source_paths.get('journal', 'missing')}`",
        "",
    ]
    return "\n".join(lines)


def generate_daily_artifact(
    kg_root: Path,
    artifact_date: Optional[date] = None,
    force: bool = False,
) -> DailyArtifactResult:
    """Generate a daily summary artifact in `.kvault/artifacts/daily/`.

    Args:
        kg_root: Knowledge base root.
        artifact_date: Date to generate the artifact for (defaults to today).
        force: Overwrite an existing artifact for the same date.
    """
    resolved_root = Path(kg_root).resolve()
    if not resolved_root.exists():
        raise ValueError(f"Knowledge base root does not exist: {resolved_root}")

    target_date = artifact_date or date.today()
    artifact_path = (
        resolved_root / ".kvault" / "artifacts" / "daily" / f"{target_date.isoformat()}.md"
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    if artifact_path.exists() and not force:
        existing = artifact_path.read_text()
        return DailyArtifactResult(
            artifact_date=target_date,
            path=artifact_path,
            content=existing,
            written=False,
        )

    root_summary_path = resolved_root / "_summary.md"
    people_summary_path = resolved_root / "people" / "_summary.md"
    projects_summary_path = resolved_root / "projects" / "_summary.md"

    root_summary = _read_markdown_body(root_summary_path)
    people_summary = _read_markdown_body(people_summary_path)
    projects_summary = _read_markdown_body(projects_summary_path)
    journal_source_path, recent_journal = _recent_journal_excerpt(resolved_root, target_date)

    content = _build_daily_content(
        artifact_date=target_date,
        root_summary=root_summary,
        people_summary=people_summary,
        projects_summary=projects_summary,
        recent_journal=recent_journal,
        source_paths={
            "root_summary": (
                str(root_summary_path.relative_to(resolved_root))
                if root_summary_path.exists()
                else "missing"
            ),
            "people_summary": (
                str(people_summary_path.relative_to(resolved_root))
                if people_summary_path.exists()
                else "missing"
            ),
            "projects_summary": (
                str(projects_summary_path.relative_to(resolved_root))
                if projects_summary_path.exists()
                else "missing"
            ),
            "journal": journal_source_path or "missing",
        },
    )

    artifact_path.write_text(content)
    return DailyArtifactResult(
        artifact_date=target_date,
        path=artifact_path,
        content=content,
        written=True,
    )
