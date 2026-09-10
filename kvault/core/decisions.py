"""Structure decisions recorded in node frontmatter, honored by every rule.

Nobody schedules a review of a knowledge base. The feedback that actually
arrives is a correction during use: "those are two different projects",
"that parent is meant to be flat", "those daily notes are on purpose". If a
correction only lives in a chat, the next `kvault check` flags the same pair
and the next `kvault plan` proposes the same merge, and the correction
evaporates. So a decision is written into the tree, next to the thing it is
about, where the deterministic rules read it:

- ``distinct_from: [path, ...]`` on a node — these are different things.
  The sibling-collision finding, the same-name-elsewhere finding, and the
  write-time similarity guard skip the pair; ``plan`` never proposes merging
  them. Either side may carry the entry.
- ``max_children: N`` on a parent — its own child ceiling. ``BRANCH:``, the
  over-fanout note, ``plan`` clustering, and the strict-path gist switch use
  it for that parent.
- ``series_ok: true`` on a parent — its dated children are a deliberate
  chronology. ``SERIES:`` and the fold are suppressed for it.

``kvault mark <path> --distinct-from <other> | --max-children N |
--series-ok`` writes them through the normal write path (validated, logged,
no-op aware), so recording a correction is one command.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from kvault.core.frontmatter import parse_frontmatter

DECISION_KEYS = ("distinct_from", "max_children", "series_ok")

_SUMMARY_NAME = "_summary.md"


def normalize_rel(value: str, node_path: str) -> str:
    """A bare name means a sibling; anything with a slash is KB-relative."""
    value = str(value).strip().strip("/")
    if not value:
        return ""
    if "/" in value or node_path in (".", ""):
        return value
    parent = node_path.rsplit("/", 1)[0] if "/" in node_path else "."
    return value if parent == "." else f"{parent}/{value}"


def read_decisions(kg_root: Path, node_path: str) -> Dict[str, Any]:
    """The three decision keys for *node_path*, normalized; missing = defaults."""
    out: Dict[str, Any] = {"distinct_from": [], "max_children": None, "series_ok": False}
    summary = (
        Path(kg_root) / node_path / _SUMMARY_NAME
        if node_path not in (".", "")
        else Path(kg_root) / _SUMMARY_NAME
    )
    try:
        meta, _ = parse_frontmatter(summary.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return out
    if not meta:
        return out
    raw = meta.get("distinct_from")
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list):
        seen: List[str] = []
        for item in raw:
            rel = normalize_rel(str(item), node_path)
            if rel and rel not in seen:
                seen.append(rel)
        out["distinct_from"] = seen
    mc = meta.get("max_children")
    if isinstance(mc, int) and not isinstance(mc, bool) and mc > 0:
        out["max_children"] = mc
    elif isinstance(mc, str) and mc.strip().isdigit() and int(mc) > 0:
        out["max_children"] = int(mc)
    so = meta.get("series_ok")
    out["series_ok"] = so is True or (
        isinstance(so, str) and so.strip().lower() in ("true", "yes", "ok")
    )
    return out


def are_distinct(kg_root: Path, a: str, b: str) -> bool:
    """True when either node records the other under ``distinct_from``."""
    if a == b:
        return False
    return (
        b in read_decisions(kg_root, a)["distinct_from"]
        or a in read_decisions(kg_root, b)["distinct_from"]
    )


def child_ceiling(kg_root: Path, node_path: str, default: int) -> int:
    """The parent's own ``max_children`` if set, else *default*."""
    override = read_decisions(kg_root, node_path)["max_children"]
    return int(override) if override else default


def series_allowed(kg_root: Path, node_path: str) -> bool:
    return bool(read_decisions(kg_root, node_path)["series_ok"])


def merge_decisions(
    meta: Dict[str, Any],
    node_path: str,
    distinct_from: Optional[List[str]] = None,
    max_children: Optional[int] = None,
    series_ok: Optional[bool] = None,
    clear: bool = False,
) -> Dict[str, Any]:
    """Return *meta* with the decision keys updated (``clear`` drops all three first)."""
    updated = dict(meta)
    if clear:
        for key in DECISION_KEYS:
            updated.pop(key, None)
    if distinct_from:
        existing = updated.get("distinct_from")
        current: List[str] = (
            list(existing)
            if isinstance(existing, list)
            else ([existing] if isinstance(existing, str) else [])
        )
        for item in distinct_from:
            rel = normalize_rel(item, node_path)
            if rel and rel not in current:
                current.append(rel)
        updated["distinct_from"] = current
    if max_children is not None:
        if max_children > 0:
            updated["max_children"] = int(max_children)
        else:
            updated.pop("max_children", None)
    if series_ok is not None:
        if series_ok:
            updated["series_ok"] = True
        else:
            updated.pop("series_ok", None)
    return updated


__all__ = [
    "DECISION_KEYS",
    "normalize_rel",
    "read_decisions",
    "are_distinct",
    "child_ceiling",
    "series_allowed",
    "merge_decisions",
]
