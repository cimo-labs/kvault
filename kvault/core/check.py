"""Deterministic KB checks — the findings behind ``kvault check``, ``kvault plan``,
and MCP ``kvault_check``.

Until 0.15 this logic lived in ``kvault.cli.check`` and was reachable only
from the CLI; the MCP server exposed ``validate`` (integrity) and nothing
else, so an MCP-only agent had no way to learn that its KB was structurally
rotten while ``validate`` stayed green. It is a core module now: stateless,
never prints, one document for every surface.

Two classes of finding:

- **hard** (exit 1, the ``[KB]`` line): ``PROPAGATE``, ``LOG``, ``WRITE``,
  ``BRANCH``. Fix before continuing.
- **warn** (exit 0, one line per finding, bounded): ``SUMMARY``, ``PENDING``,
  ``RETRACTED``, and since 0.15 the structural set ``GHOST``, ``SIBLINGS``,
  ``LOOSE``, ``JOURNAL``. Maintenance work; ``kvault plan`` orders it.

Every list in the document is bounded (``max_findings`` per code, with the
hidden count recorded) because the 0.14 ``missing_child_coverage`` line on a
118-child parent was 4.7 KB — a finding that is itself unbounded output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from kvault._version import __version__
from kvault.core import decisions as dc
from kvault.core import structure as st
from kvault.core.events import pending_event_findings, retracted_reference_findings
from kvault.core.frontmatter import parse_frontmatter
from kvault.core.summary_quality import (
    DEFAULT_MAX_DATED_SECTIONS,
    SummaryQualityIssue,
    audit_summary_quality,
)

from kvault.core.operations import MAX_DIRECT_CHILDREN

DEFAULT_THRESHOLD_MINUTES = 5
#: The write guard's ceiling and check's default are one number on purpose.
DEFAULT_MAX_CHILDREN = MAX_DIRECT_CHILDREN
DEFAULT_MAX_FINDINGS = 50
DEFAULT_PENDING_MAX_AGE = 7
#: Sibling pairs reported per parent before the count takes over. A 118-child
#: parent has thousands of colliding pairs; ``plan`` clusters them instead.
MAX_SIBLING_PAIRS_PER_PARENT = 5

HARD_CODES = ("PROPAGATE", "LOG", "WRITE", "BRANCH")
WARN_CODES = ("SUMMARY", "PENDING", "RETRACTED", "GHOST", "SERIES", "SIBLINGS", "LOOSE", "JOURNAL")
STRUCTURE_CODES = ("GHOST", "SERIES", "SIBLINGS", "LOOSE", "JOURNAL")

_SUMMARY_FIX = {
    "too_short": "rewrite the parent as a comprehensive rollup of its children",
    "missing_child_coverage": "rewrite the parent so every immediate child is described",
    "placeholder_language": "replace the placeholder with the actual rollup",
    "too_long": "fold dated sections into current state; move detail to deep_context/",
    "stale_history": "fold the dated sections into current state (chronology belongs in journal/)",
}


@dataclass
class Finding:
    """One check finding. ``text`` is the exact legacy line for hard codes."""

    code: str
    path: str
    message: str
    level: str
    detail: Dict[str, Any] = field(default_factory=dict)
    fix: str = ""
    text: Optional[str] = None

    def line(self) -> str:
        if self.text:
            return self.text
        if self.path in (".", ""):
            return f"{self.code}: {self.message}"
        return f"{self.code}: {self.path}: {self.message}"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "level": self.level,
            "detail": dict(self.detail),
            "fix": self.fix,
        }


# -- helpers -----------------------------------------------------------------


def _get_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


def _get_updated_date(path: Path) -> Optional[date]:
    """Parse frontmatter ``updated`` (or ``created``) from a summary file."""
    try:
        content = path.read_text()
    except Exception:
        return None
    meta, _ = parse_frontmatter(content)
    if not meta:
        return None
    for field_name in ("updated", "created"):
        val = meta.get(field_name)
        if val is None:
            continue
        if isinstance(val, date):
            return val
        try:
            return datetime.strptime(str(val).strip("'\""), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
    return None


def _find_entities(kb_root: Path) -> List[Path]:
    """All entity ``_summary.md`` files (leaf nodes at depth >= 3)."""
    entities = []
    for summary in kb_root.rglob(st.SUMMARY_NAME):
        parent_dir = summary.parent
        rel_path = summary.relative_to(kb_root)
        if parent_dir == kb_root or len(rel_path.parts) < 3:
            continue
        has_child_summaries = any(
            (child / st.SUMMARY_NAME).exists()
            for child in parent_dir.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        )
        if not has_child_summaries:
            entities.append(summary)
    return entities


def _node_dirs(kb_root: Path, ignore: Sequence[str]) -> List[Path]:
    """Root plus every managed directory that carries a summary."""
    out = [kb_root]
    out.extend(d for d in st.walk_dirs(kb_root, ignore) if st.has_summary(d))
    return out


# -- hard findings -----------------------------------------------------------


def propagation_findings(kb_root: Path, threshold_minutes: int) -> List[Finding]:
    """Parents should be at least as recent as their children.

    Frontmatter ``updated`` dates first (they survive git); mtime with the
    threshold as the fallback when either side has no date.
    """
    findings: List[Finding] = []
    threshold = timedelta(minutes=threshold_minutes)
    for summary in kb_root.rglob(st.SUMMARY_NAME):
        parent_dir = summary.parent
        children = [
            child_dir / st.SUMMARY_NAME
            for child_dir in parent_dir.iterdir()
            if child_dir.is_dir()
            and not child_dir.name.startswith(".")
            and (child_dir / st.SUMMARY_NAME).exists()
        ]
        if not children:
            continue
        parent_date = _get_updated_date(summary)
        for child in children:
            child_date = _get_updated_date(child)
            stale = False
            detail = ""
            if child_date is not None and parent_date is not None:
                if child_date > parent_date:
                    stale = True
                    detail = f"child updated {child_date}, parent updated {parent_date}"
            else:
                delta = _get_mtime(child) - _get_mtime(summary)
                if delta > threshold:
                    stale = True
                    detail = f"{int(delta.total_seconds()) // 60}m newer"
            if stale:
                parent_path = str(summary.relative_to(kb_root))
                child_name = child.relative_to(kb_root).parent.name
                findings.append(
                    Finding(
                        code="PROPAGATE",
                        path=st.rel(kb_root, parent_dir),
                        message=f"{child_name}/ is {detail}",
                        level="hard",
                        detail={"child": child_name},
                        fix="kvault update-summaries (rewrite the parent as a rollup)",
                        text=f"PROPAGATE: edit {parent_path} ({child_name}/ is {detail})",
                    )
                )
    return findings


def journal_findings(kb_root: Path) -> List[Finding]:
    """Entities modified today need a journal entry today."""
    today = date.today()
    modified = [e for e in _find_entities(kb_root) if _get_mtime(e).date() == today]
    if not modified:
        return []
    journal_file = kb_root / "journal" / today.strftime("%Y-%m") / "log.md"
    if not journal_file.exists() or _get_mtime(journal_file).date() != today:
        return [
            Finding(
                code="LOG",
                path=".",
                message=f"{len(modified)} entities need journal",
                level="hard",
                detail={"count": len(modified)},
                fix="kvault journal (stdin: the actions taken)",
                text=f"LOG: {len(modified)} entities need journal",
            )
        ]
    return []


def frontmatter_findings(kb_root: Path) -> List[Finding]:
    """Entities need ``source`` and ``aliases``."""
    required = ("source", "aliases")
    bad: List[str] = []
    for entity in _find_entities(kb_root):
        name = entity.relative_to(kb_root).parent.name
        try:
            meta, _ = parse_frontmatter(entity.read_text())
        except Exception:
            bad.append(name)
            continue
        if not meta or any(f not in meta or meta[f] is None for f in required):
            bad.append(name)
    if not bad:
        return []
    return [
        Finding(
            code="WRITE",
            path=".",
            message=f"{len(bad)} entities need frontmatter",
            level="hard",
            detail={"count": len(bad), "entities": bad[:DEFAULT_MAX_FINDINGS]},
            fix="rewrite each with kvault write (frontmatter: source, aliases)",
            text=f"WRITE: {len(bad)} entities need frontmatter",
        )
    ]


def branching_findings(
    kb_root: Path,
    max_children: int = DEFAULT_MAX_CHILDREN,
    ignore: Optional[Sequence[str]] = None,
) -> List[Finding]:
    """Parents over the direct-child ceiling. The root is included since 0.15.

    Counts managed directories (ghosts included — an invisible child is still
    fan-out), never reserved or ignored ones.
    """
    patterns = list(ignore) if ignore is not None else st.load_ignore(kb_root)
    findings: List[Finding] = []
    for node_dir in _node_dirs(kb_root, patterns):
        count = len(st.child_dirs(node_dir, kb_root, patterns))
        rel_path = st.rel(kb_root, node_dir)
        ceiling = dc.child_ceiling(kb_root, rel_path, max_children)
        if count > ceiling:
            findings.append(
                Finding(
                    code="BRANCH",
                    path=rel_path,
                    message=f"has {count} children (>{ceiling})",
                    level="hard",
                    detail={"child_count": count, "max_children": ceiling},
                    fix=f"kvault plan {rel_path} (clusters the children into new parents)",
                    text=f"BRANCH: {rel_path} has {count} children (>{ceiling})",
                )
            )
    return findings


# -- warn findings -----------------------------------------------------------


def summary_findings(issues: Sequence[SummaryQualityIssue]) -> List[Finding]:
    return [
        Finding(
            code="SUMMARY",
            path=issue.path,
            message=issue.message,
            level="warn",
            detail={"summary_code": issue.code, **issue.details},
            fix=_SUMMARY_FIX.get(issue.code, "rewrite the parent summary"),
        )
        for issue in issues
    ]


def pending_findings(pending: Sequence[Dict[str, Any]]) -> List[Finding]:
    return [
        Finding(
            code="PENDING",
            path=str(p.get("event_id")),
            message=f"captured {str(p.get('captured_at'))[:10]} ({p.get('age_days')}d)",
            level="warn",
            detail=dict(p),
            fix="kvault write --event <id> (promote) or kvault events resolve <id>",
        )
        for p in pending
    ]


def retracted_findings(refs: Sequence[Dict[str, Any]]) -> List[Finding]:
    out: List[Finding] = []
    for r in refs:
        follow_up = r.get("superseded_by") or "<id of the corrected capture>"
        out.append(
            Finding(
                code="RETRACTED",
                path=str(r.get("path")),
                message=f"cites retracted {r.get('event_id')} — {str(r.get('reason') or '')[:80]}",
                level="warn",
                detail=dict(r),
                fix=f"rewrite the node, then kvault write --event {follow_up}",
            )
        )
    return out


def ghost_findings(kb_root: Path, ignore: Sequence[str]) -> List[Finding]:
    """Directories without a summary: invisible to tree, search, and every audit."""
    return [
        Finding(
            code="GHOST",
            path=path,
            message="no _summary.md — invisible to tree, search, and check",
            level="warn",
            fix=(
                f"kvault write {path} --create (stdin: a rollup of what is inside), "
                f"or list it in {st.IGNORE_FILE}"
            ),
        )
        for path in st.ghost_dirs(kb_root, ignore)
    ]


LOOSE_KINDS = ("legacy_node_file", "supporting_doc", "artifact")


def _loose_kind(kb_root: Path, path: str) -> str:
    if not path.endswith(".md"):
        return "artifact"
    try:
        meta, _ = parse_frontmatter((kb_root / path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return "supporting_doc"
    return "legacy_node_file" if meta else "supporting_doc"


def loose_findings(kb_root: Path, ignore: Sequence[str]) -> List[Finding]:
    """Files outside the node convention, classified by what they are.

    On a real 564-node KB, 61 of 70 loose files were Markdown *with
    frontmatter*: knowledge written in the pre-directory layout, invisible
    to search and tree. Those are ``legacy_node_file`` and the fix is to
    adopt them as nodes. Plain Markdown is a ``supporting_doc``
    (deep_context/ material); anything else is an ``artifact``. Files whose
    name starts with ``_`` are the KB's own internals and are skipped.
    """
    out: List[Finding] = []
    for path in st.loose_files(kb_root, ignore):
        name = path.rsplit("/", 1)[-1]
        if name.startswith("_"):
            continue
        parent = path.rsplit("/", 1)[0] if "/" in path else "."
        home = "deep_context/" if parent == "." else f"{parent}/deep_context/"
        kind = _loose_kind(kb_root, path)
        if kind == "legacy_node_file":
            node = path[: -len(".md")]
            message = "legacy node file (Markdown with frontmatter) — invisible to search and tree"
            fix = f"adopt it: mkdir -p {node} && git mv {path} {node}/_summary.md, then propagate"
        elif kind == "supporting_doc":
            message = "supporting document outside any node"
            fix = f"move it into {home}, or fold it into the parent summary"
        else:
            message = "artifact outside the node convention"
            fix = f"move it into {home}, or list it in {st.IGNORE_FILE}"
        out.append(
            Finding(
                code="LOOSE",
                path=path,
                message=message,
                level="warn",
                detail={"kind": kind, "parent": parent},
                fix=fix,
            )
        )
    return out


def series_findings(kb_root: Path, ignore: Sequence[str], min_size: int = 3) -> List[Finding]:
    """Parents whose children differ only by date/time words: a chronology
    written as nodes. Chronology belongs in journal/ or in one node's
    current state; a real KB had 40 daily cards under one parent."""
    out: List[Finding] = []
    for node_dir in _node_dirs(kb_root, ignore):
        names = [d.name for d in st.child_dirs(node_dir, kb_root, ignore)]
        parent = st.rel(kb_root, node_dir)
        if dc.series_allowed(kb_root, parent):
            continue
        for key, members in st.date_series(names, min_size=min_size):
            out.append(
                Finding(
                    code="SERIES",
                    path=parent,
                    message=(
                        f"{len(members)} children differ only by date/time words "
                        f"('{key}'), e.g. {members[0]}"
                    ),
                    level="warn",
                    detail={"key": key, "count": len(members), "members": members[:10]},
                    fix=(
                        "fold the dated nodes into one current-state node under this parent "
                        "and put the timeline in journal/; if they must stay, name them by "
                        "topic, not date"
                    ),
                )
            )
    return out


def sibling_findings(
    kb_root: Path,
    ignore: Sequence[str],
    pairs_per_parent: int = MAX_SIBLING_PAIRS_PER_PARENT,
) -> List[Finding]:
    """Near-duplicate sibling names, and the same basename at several depths."""
    out: List[Finding] = []
    for node_dir in _node_dirs(kb_root, ignore):
        names = [d.name for d in st.child_dirs(node_dir, kb_root, ignore)]
        if len(names) < 2:
            continue
        # Members of one date series are a chronology, not near-duplicates;
        # SERIES: reports them once. Without this the daily cards on a real
        # KB produced 60 colliding pairs.
        parent = st.rel(kb_root, node_dir)
        pairs = [
            (a, c)
            for a, c in st.sibling_pairs(names)
            if not st.same_series(a, c.name)
            and not dc.are_distinct(kb_root, _child(parent, a), _child(parent, c.name))
        ]
        for a, c in pairs[:pairs_per_parent]:
            score = "" if c.kind == "same_words" else f" {c.score}"
            out.append(
                Finding(
                    code="SIBLINGS",
                    path=parent,
                    message=f"{a} ~ {c.name} ({c.kind}{score})",
                    level="warn",
                    detail={"kind": c.kind, "a": a, "b": c.name, "score": c.score},
                    fix=(
                        "same thing: merge into one node and delete the other; "
                        "subtopic: nest it with kvault move"
                    ),
                )
            )
        hidden = len(pairs) - pairs_per_parent
        if hidden > 0:
            out.append(
                Finding(
                    code="SIBLINGS",
                    path=parent,
                    message=f"+{hidden:,} more colliding pairs among {len(names)} children",
                    level="warn",
                    detail={"kind": "more_pairs", "hidden": hidden, "total": len(pairs)},
                    fix=f"kvault plan {parent}",
                )
            )
    for name, paths in sorted(st.basename_duplicates(kb_root, ignore).items()):
        # a_m/n_z buckets under two branches, and one basename under sibling
        # parents (customers/{key,standard}/oem), are layouts, not twins.
        if st.is_bucket_name(name) or st.is_facet_layout(paths):
            continue
        # a recorded `distinct_from` between any two of them settles it
        paths = [
            p for p in paths if not any(dc.are_distinct(kb_root, p, o) for o in paths if o != p)
        ]
        if len(paths) < 2:
            continue
        titled = [f"{p} «{_node_title(kb_root, p)}»" for p in paths[:4]]
        out.append(
            Finding(
                code="SIBLINGS",
                path=name,
                message=f"'{name}' exists at {len(paths)} places: {', '.join(titled)}"
                + (" …" if len(paths) > 4 else ""),
                level="warn",
                detail={
                    "kind": "same_name_elsewhere",
                    "paths": paths,
                    "titles": [_node_title(kb_root, p) for p in paths],
                },
                fix="same thing: merge; different things: rename one so the name is not ambiguous",
            )
        )
    return out


def _child(parent: str, name: str) -> str:
    return name if parent == "." else f"{parent}/{name}"


def _node_title(kb_root: Path, rel_path: str) -> str:
    summary = kb_root / rel_path / st.SUMMARY_NAME
    try:
        meta, body = parse_frontmatter(summary.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    for key in ("name", "title", "topic"):
        value = (meta or {}).get(key)
        if value:
            return str(value)[:40]
    for line in body.splitlines():
        if line.startswith("#"):
            return line.lstrip("# ").strip()[:40]
    return ""


def journal_layout_findings(kb_root: Path) -> List[Finding]:
    return [
        Finding(
            code="JOURNAL",
            path=f["path"],
            message=f["reason"],
            level="warn",
            fix=(
                "keep one history: journal/YYYY-MM/log.md written by kvault journal; "
                f"fold other files into it or list them in {st.IGNORE_FILE}"
            ),
        )
        for f in st.journal_layout_findings(kb_root)
    ]


# -- legacy string wrappers (kept for kvault.cli.check imports and tests) ---


def check_propagation(kb_root: Path, threshold_minutes: int) -> List[str]:
    return [f.line() for f in propagation_findings(kb_root, threshold_minutes)]


def check_journal(kb_root: Path) -> List[str]:
    return [f.line() for f in journal_findings(kb_root)]


def check_frontmatter(kb_root: Path) -> List[str]:
    return [f.line() for f in frontmatter_findings(kb_root)]


def check_directory_size(kb_root: Path, max_children: int = DEFAULT_MAX_CHILDREN) -> List[str]:
    return [f.line() for f in branching_findings(kb_root, max_children)]


# -- the document ------------------------------------------------------------


def _cap(findings: List[Finding], limit: int) -> tuple[List[Finding], int]:
    if limit <= 0 or len(findings) <= limit:
        return findings, 0
    return findings[:limit], len(findings) - limit


def run_checks(
    kb_root: Path,
    threshold_minutes: int = DEFAULT_THRESHOLD_MINUTES,
    summary_quality: bool = True,
    max_words: Optional[int] = None,
    max_dated_sections: int = DEFAULT_MAX_DATED_SECTIONS,
    pending_max_age: int = DEFAULT_PENDING_MAX_AGE,
    max_children: int = DEFAULT_MAX_CHILDREN,
    max_findings: int = DEFAULT_MAX_FINDINGS,
) -> Dict[str, Any]:
    """Run every check and return one document.

    ``success`` is false only for hard findings. The legacy keys
    (``warnings`` as strings, ``summary_warnings``, ``pending_events``,
    ``retracted_refs``) are unchanged; ``findings`` is the unified structured
    list hard-first, and ``structure_warnings`` holds the 0.15 codes. Lists
    of warn-class findings are capped at *max_findings* per code with the
    hidden count in ``truncated``.
    """
    root = Path(kb_root)
    ignore = st.load_ignore(root)

    hard: List[Finding] = []
    hard.extend(propagation_findings(root, threshold_minutes))
    hard.extend(journal_findings(root))
    hard.extend(frontmatter_findings(root))
    hard.extend(branching_findings(root, max_children, ignore))

    issues = (
        audit_summary_quality(root, max_words=max_words, max_dated_sections=max_dated_sections)
        if summary_quality
        else []
    )
    pending = pending_event_findings(root, max_age_days=pending_max_age)
    retracted = retracted_reference_findings(root)

    truncated: Dict[str, int] = {}
    warn_groups: List[List[Finding]] = []
    for code, group in (
        ("SUMMARY", summary_findings(issues)),
        ("PENDING", pending_findings(pending)),
        ("RETRACTED", retracted_findings(retracted)),
        ("GHOST", ghost_findings(root, ignore)),
        ("SERIES", series_findings(root, ignore)),
        ("SIBLINGS", sibling_findings(root, ignore)),
        ("LOOSE", loose_findings(root, ignore)),
        ("JOURNAL", journal_layout_findings(root)),
    ):
        shown, hidden = _cap(group, max_findings)
        if hidden:
            truncated[code] = hidden
        warn_groups.append(shown)
    warn = [f for group in warn_groups for f in group]
    structure = [f for f in warn if f.code in STRUCTURE_CODES]
    structure_total = sum(len(g) for g in warn_groups[3:]) + sum(
        truncated.get(c, 0) for c in STRUCTURE_CODES
    )

    warn_total = len(warn) + sum(truncated.values())
    doc: Dict[str, Any] = {
        "success": not hard,
        "version": __version__,
        "did": f"checked: {len(hard)} hard, {warn_total} warn",
        "warnings": [f.line() for f in hard],
        "warning_count": len(hard),
        "summary_warnings": [
            {
                "path": issue.path,
                "code": issue.code,
                "message": issue.message,
                "details": issue.details,
            }
            for issue in issues
        ],
        "summary_warning_count": len(issues),
        "summary_quality_enabled": summary_quality,
        "pending_events": pending,
        "pending_event_count": len(pending),
        "retracted_refs": retracted,
        "retracted_ref_count": len(retracted),
        "structure_warnings": [f.as_dict() for f in structure],
        "structure_warning_count": structure_total,
        "findings": [f.as_dict() for f in hard + warn],
        "finding_count": len(hard) + warn_total,
        "hard_count": len(hard),
        "warn_count": warn_total,
        "truncated": truncated,
        "ignore_patterns": list(ignore),
    }
    return doc


__all__ = [
    "DEFAULT_THRESHOLD_MINUTES",
    "DEFAULT_MAX_CHILDREN",
    "DEFAULT_MAX_FINDINGS",
    "DEFAULT_PENDING_MAX_AGE",
    "HARD_CODES",
    "WARN_CODES",
    "STRUCTURE_CODES",
    "Finding",
    "propagation_findings",
    "journal_findings",
    "frontmatter_findings",
    "branching_findings",
    "summary_findings",
    "ghost_findings",
    "series_findings",
    "loose_findings",
    "sibling_findings",
    "journal_layout_findings",
    "check_propagation",
    "check_journal",
    "check_frontmatter",
    "check_directory_size",
    "run_checks",
]
