"""Structure decisions recorded in node frontmatter, honored by every rule.

Nobody schedules a review of a knowledge base. The feedback that actually
arrives is a correction during use: "those are two different projects",
"that parent is meant to be flat", "those daily notes are on purpose". If a
correction only lives in a chat, the next `kvault check` flags the same pair
and the next `kvault plan` proposes the same merge, and the correction
evaporates. So a decision is written into the tree, next to the thing it is
about, where the deterministic rules read it:

- ``distinct_from: [path, ...]`` on a node — these are different things.
  The sibling-collision finding, the duplicate finding, and the write-time
  similarity guard skip the pair; ``plan`` never proposes merging them.
  Either side may carry the entry.
- ``max_children: N`` on a parent — its own child ceiling. ``BRANCH:``, the
  over-fanout note, ``plan`` clustering, and the strict-path gist switch use
  it for that parent.
- ``series_ok: true`` on a parent — its dated children are a deliberate
  chronology. ``SERIES:`` and the fold are suppressed for it.
- ``verify_by: YYYY-MM-DD`` on a node (0.16) — it records facts that go
  stale (a pending change, an open review, a deployment in progress);
  ``STALE:`` reports it once the date passes. kvault cannot check a fact;
  it can hold the agent that wrote one to a date for re-checking it.

``kvault mark <path> --distinct-from <other> | --max-children N |
--series-ok | --verify-by DATE`` writes them through the normal write path
(validated, logged, no-op aware), so recording a correction is one command.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from kvault.core.frontmatter import parse_frontmatter

DECISION_KEYS = ("distinct_from", "max_children", "series_ok", "verify_by")
#: What ``--clear`` drops. ``verify_by`` is not a structure call and has its
#: own clear (``--verify-by none``): a correction to the tree must not
#: silently disarm a re-check date.
STRUCTURE_DECISION_KEYS = ("distinct_from", "max_children", "series_ok")
_DATE_TEXT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$")
_RELATIVE_RE = re.compile(r"^\+(\d{1,4})([dw])$")
_CLEAR_WORDS = ("", "none", "clear", "0", "no", "false")

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


def as_date(value: Any) -> Optional[date]:
    """A frontmatter date (YAML date, datetime, or 'YYYY-MM-DD' string), or None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().strip("'\"") if value is not None else ""
    if not _DATE_TEXT_RE.match(text):
        return None  # "2026-09-201" is not 2026-09-20
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_verify_by(value: str, today: Optional[date] = None) -> str:
    """``2026-10-15``, ``+14d`` or ``+2w`` (from *today*) → ``YYYY-MM-DD``; a clear word → ``""``.

    Raises ``ValueError`` for anything else: a date nobody can parse would
    never come due, which is the failure this key exists to prevent.
    """
    text = str(value).strip().lower()
    if text in _CLEAR_WORDS:
        return ""
    base = today or date.today()
    rel = _RELATIVE_RE.match(text)
    if rel:
        days = int(rel.group(1)) * (7 if rel.group(2) == "w" else 1)
        return (base + timedelta(days=days)).isoformat()
    parsed = as_date(text) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None
    if parsed is None:
        raise ValueError(
            f"verify_by must be YYYY-MM-DD, +Nd or +Nw (e.g. +14d), or 'none' to clear; got {value!r}"
        )
    return parsed.isoformat()


def read_decisions(kg_root: Path, node_path: str) -> Dict[str, Any]:
    """The decision keys for *node_path*, normalized; missing = defaults.

    ``verify_by`` is ``YYYY-MM-DD`` when set and parseable, else None;
    ``verify_by_raw`` keeps what was written so an unparseable value can be
    reported instead of silently never coming due.
    """
    out: Dict[str, Any] = {
        "distinct_from": [],
        "max_children": None,
        "series_ok": False,
        "verify_by": None,
        "verify_by_raw": None,
    }
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
    vb = meta.get("verify_by")
    if vb is not None and str(vb).strip():
        out["verify_by_raw"] = str(vb)
        due = as_date(vb)
        out["verify_by"] = due.isoformat() if due else None
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
    verify_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Return *meta* with the decision keys updated (``clear`` drops the structure keys first).

    *verify_by* is already normalized by ``parse_verify_by``: a date string
    sets it, ``""`` removes it.
    """
    updated = dict(meta)
    if clear:
        for key in STRUCTURE_DECISION_KEYS:
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
    if verify_by is not None:
        if verify_by:
            updated["verify_by"] = verify_by
        else:
            updated.pop("verify_by", None)
    return updated


__all__ = [
    "DECISION_KEYS",
    "STRUCTURE_DECISION_KEYS",
    "as_date",
    "parse_verify_by",
    "normalize_rel",
    "read_decisions",
    "are_distinct",
    "child_ceiling",
    "series_allowed",
    "merge_decisions",
]
