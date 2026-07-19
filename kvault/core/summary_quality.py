"""Summary quality auditing for hierarchical kvault knowledge bases."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

from kvault.core.frontmatter import parse_frontmatter
from kvault.core.paths import PathSafetyError, resolve_within_root

_SUMMARY_NAME = "_summary.md"

_PLACEHOLDER_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("summary pending", re.compile(r"\bsummary\s+pending\b", re.IGNORECASE)),
    ("TBD", re.compile(r"\btbd\b", re.IGNORECASE)),
    ("TODO", re.compile(r"\btodo\b", re.IGNORECASE)),
    ("to be expanded", re.compile(r"\bto\s+be\s+expanded\b", re.IGNORECASE)),
    ("placeholder", re.compile(r"\bplaceholder\b", re.IGNORECASE)),
    (
        "redirect to details",
        re.compile(
            r"\b(?:see|refer to|go to)\b.{0,100}\b(?:details?|more detailed|full details?)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)


@dataclass(frozen=True)
class SummaryQualityIssue:
    """A deterministic warning about a parent summary."""

    path: str
    code: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def format(self) -> str:
        """Return a compact human-facing warning string."""
        return f"SUMMARY: {self.path}: {self.message}"


def audit_summary_quality(kg_root: Path) -> List[SummaryQualityIssue]:
    """Audit parent summaries for descendant coverage.

    The audit is intentionally deterministic and dependency-light. It cannot
    prove semantic completeness, but it catches the patterns that make parent
    summaries poor navigation surfaces: missing child mentions, very short
    rollups, and placeholder/redirect language.
    """
    root = Path(kg_root).resolve()
    issues: List[SummaryQualityIssue] = []

    for summary_path in sorted(root.rglob(_SUMMARY_NAME)):
        try:
            summary_path = resolve_within_root(
                root,
                summary_path.relative_to(root),
                allow_root=False,
                must_exist=True,
                reject_symlinks=True,
            )
        except (PathSafetyError, ValueError):
            continue
        if not summary_path.is_file() or summary_path.is_symlink():
            continue
        parent_dir = summary_path.parent
        if _is_hidden_path(root, parent_dir):
            continue

        children = _child_summary_dirs(parent_dir)
        if not children:
            continue

        relative_path = _display_summary_path(root, summary_path)
        raw = _safe_read(summary_path)
        _, body = parse_frontmatter(raw)
        word_count = _word_count(body)
        descendant_count = _descendant_summary_count(root, parent_dir)

        min_words = _minimum_word_count(len(children), descendant_count)
        if word_count < min_words:
            issues.append(
                SummaryQualityIssue(
                    path=relative_path,
                    code="too_short",
                    message=(
                        f"too short for {len(children)} children/{descendant_count} "
                        f"descendants ({word_count} words < {min_words})"
                    ),
                    details={
                        "word_count": word_count,
                        "minimum_words": min_words,
                        "child_count": len(children),
                        "descendant_count": descendant_count,
                    },
                )
            )

        missing_children = _missing_child_coverage(body, children)
        if missing_children:
            issues.append(
                SummaryQualityIssue(
                    path=relative_path,
                    code="missing_child_coverage",
                    message="missing immediate child coverage: " + ", ".join(missing_children),
                    details={"missing_children": missing_children},
                )
            )

        placeholder_hits = _placeholder_hits(body)
        if placeholder_hits:
            issues.append(
                SummaryQualityIssue(
                    path=relative_path,
                    code="placeholder_language",
                    message="contains placeholder/redirect language: "
                    + ", ".join(placeholder_hits),
                    details={"matches": placeholder_hits},
                )
            )

    return issues


def format_summary_quality_warnings(
    issues: Sequence[SummaryQualityIssue], max_warnings: int = 5
) -> List[str]:
    """Format quality issues for compact CLI output."""
    if max_warnings < 1:
        max_warnings = 1
    rendered = [issue.format() for issue in issues[:max_warnings]]
    remaining = len(issues) - len(rendered)
    if remaining > 0:
        rendered.append(f"SUMMARY: +{remaining} more summary quality warning(s)")
    return rendered


def _safe_read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _is_hidden_path(kg_root: Path, path: Path) -> bool:
    try:
        rel = path.relative_to(kg_root)
    except ValueError:
        return True
    return any(part.startswith(".") for part in rel.parts)


def _child_summary_dirs(parent_dir: Path) -> List[Path]:
    children = []
    try:
        entries = parent_dir.iterdir()
    except OSError:
        return []
    for child in entries:
        if (
            child.is_dir()
            and not child.is_symlink()
            and not child.name.startswith(".")
            and (child / _SUMMARY_NAME).is_file()
            and not (child / _SUMMARY_NAME).is_symlink()
        ):
            children.append(child)
    return sorted(children)


def _display_summary_path(kg_root: Path, summary_path: Path) -> str:
    if summary_path.parent == kg_root:
        return _SUMMARY_NAME
    return str(summary_path.relative_to(kg_root))


def _descendant_summary_count(kg_root: Path, parent_dir: Path) -> int:
    count = 0
    for summary_path in parent_dir.rglob(_SUMMARY_NAME):
        try:
            summary_path = resolve_within_root(
                kg_root,
                summary_path.relative_to(kg_root),
                allow_root=False,
                must_exist=True,
                reject_symlinks=True,
            )
        except (PathSafetyError, ValueError):
            continue
        if summary_path.parent == parent_dir:
            continue
        if _is_hidden_path(kg_root, summary_path.parent):
            continue
        count += 1
    return count


def _minimum_word_count(child_count: int, descendant_count: int) -> int:
    return min(500, 40 + 25 * child_count + 5 * descendant_count)


def _word_count(markdown: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", markdown))


def _missing_child_coverage(parent_body: str, child_dirs: Iterable[Path]) -> List[str]:
    normalized_parent = _normalize_text(parent_body)
    missing = []
    for child_dir in child_dirs:
        terms = _child_terms(child_dir)
        if not any(_term_in_text(term, normalized_parent) for term in terms):
            missing.append(child_dir.name)
    return missing


def _term_in_text(term: str, normalized_text: str) -> bool:
    normalized_term = _normalize_text(term)
    if not normalized_term:
        return False
    return f" {normalized_term} " in f" {normalized_text} "


def _child_terms(child_dir: Path) -> Set[str]:
    summary_path = child_dir / _SUMMARY_NAME
    raw = _safe_read(summary_path)
    meta, body = parse_frontmatter(raw)

    terms = {child_dir.name, child_dir.name.replace("_", " ")}

    h1 = _first_heading(body)
    if h1:
        terms.add(h1)

    for field_name in ("name", "topic"):
        value = meta.get(field_name)
        if isinstance(value, str) and value.strip():
            terms.add(value.strip())

    aliases = meta.get("aliases", [])
    if isinstance(aliases, list):
        for alias in aliases:
            if alias is not None:
                terms.add(str(alias))

    return {term for term in terms if term.strip()}


def _first_heading(markdown: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return ""


def _normalize_text(value: str) -> str:
    normalized = value.lower().replace("_", " ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _placeholder_hits(markdown: str) -> List[str]:
    hits = []
    for label, pattern in _PLACEHOLDER_PATTERNS:
        if pattern.search(markdown):
            hits.append(label)
    return hits


__all__ = [
    "SummaryQualityIssue",
    "audit_summary_quality",
    "format_summary_quality_warnings",
]
