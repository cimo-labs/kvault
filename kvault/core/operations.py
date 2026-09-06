"""Stateless operations layer for kvault.

All functions take ``kg_root: Path`` as first argument — no globals, no
sessions.  Used by both the MCP server and CLI commands.

CLI workflow (2-call write):
  1. write_entity(kg_root, path, ...) → ancestors + optional auto-journal
  2. update_summaries(kg_root, updates) → batch propagation

MCP strict parent workflow:
  1. prepare_summary_update(kg_root, path) → parent + direct children + digest
  2. write_parent_summary(kg_root, path, content, digest) → stale-child guard
"""

import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from kvault._version import __version__
from kvault.core import notes as nt
from kvault.core.frontmatter import (
    FrontmatterError,
    build_frontmatter,
    parse_frontmatter,
    parse_frontmatter_strict,
)
from kvault.core.locks import KBWriteLock, atomic_write_text
from kvault.core.paths import (
    PathSafetyError,
    resolve_node_path,
    validate_node_target,
)
from kvault.core.storage import (
    SimpleStorage,
    count_entities,
    list_entity_records,
    scan_entities,
)
from kvault.core.validation import (
    INVALID_COMPONENT_HINT,
    NODE_COMPONENT_RE,
    ErrorCode,
    error_response,
    format_journal_entry,
    get_journal_path,
    normalize_path,
    validate_entity_path,
)

_ALLOWED_ROOTS_ENV = "KVAULT_ALLOWED_ROOTS"
# Single source of truth lives in validation.py — this pattern used to be duplicated
# here and the two copies drifted (2026-07-26 audit). Alias kept for existing callers.
_NODE_COMPONENT_RE = NODE_COMPONENT_RE
_HEADING_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*$", re.MULTILINE)
# A body line that is *only* a placeholder marker — optionally prefixed by a list
# marker and a scaffolding label ("Context: TBD", "- TODO"). Deliberately anchored
# to the whole line so a real datum like "Lead time: TBD" inside a filled entity
# does NOT match.
_PLACEHOLDER_LINE_RE = re.compile(
    r"^[-*\s]*"
    r"(?:(?:context|background|details|notes|summary|overview)\s*[:\-]?\s*)?"
    r"(?:tbd|tbc|todo|to be determined|to be added|placeholder|\(placeholder\)|fill in)\.?$",
    re.IGNORECASE,
)
SUMMARY_UPDATE_DIGEST_ALGORITHM = "direct-child-summary-sha256-v1"
MAX_DIRECT_CHILDREN = 10


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------


def configured_allowed_roots() -> List[Path]:
    """Return allowed KB roots from KVAULT_ALLOWED_ROOTS (if configured)."""
    raw = os.environ.get(_ALLOWED_ROOTS_ENV, "").strip()
    if not raw:
        return []
    normalized = raw.replace(os.pathsep, ",")
    tokens = [token.strip() for token in normalized.split(",") if token.strip()]
    return [Path(token).resolve() for token in tokens]


def validate_allowed_root(candidate_root: Path) -> Optional[str]:
    """Validate *candidate_root* against KVAULT_ALLOWED_ROOTS.

    Returns an error message string if blocked, or ``None`` if OK.
    """
    allowed = configured_allowed_roots()
    if not allowed:
        return None
    candidate = candidate_root.resolve()
    if any(candidate == r for r in allowed):
        return None
    allowed_str = ", ".join(str(r) for r in allowed)
    return (
        f"kg_root '{candidate}' is not allowed by {_ALLOWED_ROOTS_ENV}. "
        f"Allowed roots: {allowed_str}"
    )


def validate_within_root(kg_root: Path, path: str) -> bool:
    """Return True if *path* resolves inside *kg_root*."""
    resolved = (kg_root / path).resolve()
    root_resolved = kg_root.resolve()
    try:
        resolved.relative_to(root_resolved)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Hierarchy / tree helpers
# ---------------------------------------------------------------------------


def _default_title(slug: str) -> str:
    """Default display title derived from a path slug."""
    if slug == ".":
        return "Root"
    return slug.replace("_", " ").title()


def _meta_date(value: Any) -> Optional[str]:
    """Coerce a frontmatter date (str or datetime.date) to YYYY-MM-DD, or None."""
    if value is None:
        return None
    text = str(value).strip()
    return text[:10] if text else None


def _extract_gist(content: str, limit: int = 80) -> Optional[str]:
    """First non-heading, non-empty body line, capped at *limit* chars."""
    for line in content.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if len(line) > limit:
            return line[: limit - 1].rstrip() + "…"
        return line
    return None


def build_outline(
    kg_root: Path,
    path: str = ".",
    depth: Optional[int] = None,
    max_children: Optional[int] = 20,
    include_gist: bool = False,
) -> Optional[Dict[str, Any]]:
    """Build an annotated outline of the node tree rooted at *path*.

    Always walks the full subtree to compute descendant counts and recency
    (``updated_max``), then prunes the returned structure to *depth* levels
    below *path* and *max_children* shown per node. Pruned regions are
    described by explicit ``truncated`` markers so nothing is hidden
    silently. Returns ``None`` when *path* is invalid or not a node.
    """
    path = _normalize_node_path(path)
    is_valid, _ = _validate_node_path(path)
    if not is_valid or not validate_within_root(kg_root, path):
        return None
    visited: Set[Path] = set()
    return _walk_outline(kg_root, path, depth, max_children, include_gist, 0, visited)


def _walk_outline(
    kg_root: Path,
    path: str,
    depth: Optional[int],
    max_children: Optional[int],
    include_gist: bool,
    level: int,
    visited: Set[Path],
) -> Optional[Dict[str, Any]]:
    raw = _read_node_raw(kg_root, path)
    if raw is None:
        return None
    node_dir = (kg_root if path == "." else kg_root / path).resolve()
    if node_dir in visited:  # symlink cycle guard — depth may be unbounded
        return None
    visited.add(node_dir)

    slug = "." if path == "." else path.split("/")[-1]
    updated = _meta_date(raw["meta"].get("updated"))

    children: List[Dict[str, Any]] = []
    for child_path in _child_node_paths(kg_root, path):
        child = _walk_outline(
            kg_root, child_path, depth, max_children, include_gist, level + 1, visited
        )
        if child is not None:
            children.append(child)

    descendants = sum(1 + c["descendants_count"] for c in children)
    updated_max = updated
    for c in children:
        cm = c["updated_max"]
        if cm is not None and (updated_max is None or cm > updated_max):
            updated_max = cm

    node: Dict[str, Any] = {
        "path": path,
        "slug": slug,
        "title": raw["title"],
        "title_differs": raw["title"] != _default_title(slug),
        "kind": raw["kind"],
        "updated": updated,
        "updated_max": updated_max,
        "children_count": len(children),
        "descendants_count": descendants,
        "children": children,
        "truncated": None,
    }
    if include_gist:
        node["gist"] = _extract_gist(raw["content"])

    if depth is not None and level >= depth and children:
        hidden_max = None
        for c in children:
            cm = c["updated_max"]
            if cm is not None and (hidden_max is None or cm > hidden_max):
                hidden_max = cm
        node["children"] = []
        node["truncated"] = {
            "kind": "depth",
            "hidden_children": len(children),
            "hidden_nodes": descendants,
            "hidden_updated_max": hidden_max,
        }
    elif max_children is not None and len(children) > max_children:
        hidden = children[max_children:]
        hidden_max = None
        for c in hidden:
            cm = c["updated_max"]
            if cm is not None and (hidden_max is None or cm > hidden_max):
                hidden_max = cm
        node["children"] = children[:max_children]
        node["truncated"] = {
            "kind": "max_children",
            "hidden_children": len(hidden),
            "hidden_nodes": sum(1 + c["descendants_count"] for c in hidden),
            "hidden_updated_max": hidden_max,
        }
    return node


def outline_counts(outline: Dict[str, Any]) -> Dict[str, int]:
    """Total nodes in the walked subtree vs nodes shown after pruning."""

    def _shown(node: Dict[str, Any]) -> int:
        return 1 + sum(_shown(c) for c in node["children"])

    return {
        "total_nodes": outline["descendants_count"] + 1,
        "shown_nodes": _shown(outline),
    }


def render_outline_text(outline: Dict[str, Any]) -> str:
    """Render a ``build_outline`` structure as a compact annotated text tree."""
    lines: List[str] = []

    def _fmt(node: Dict[str, Any], label: str) -> str:
        parts = [label]
        if node["title_differs"]:
            parts.append(f"« {node['title']} »")
        if node["children_count"]:
            parts.append(f"[{node['children_count']} children, {node['descendants_count']} total]")
        if node["updated_max"]:
            parts.append(f"~{node['updated_max']}")
        line = " ".join(parts)
        if node.get("gist"):
            line += f" — {node['gist']}"
        return line

    def _walk(node: Dict[str, Any], indent: str, label: str) -> None:
        lines.append(indent + _fmt(node, label))
        for child in node["children"]:
            _walk(child, indent + "  ", child["slug"])
        truncated = node["truncated"]
        if truncated is None:
            return
        if truncated["kind"] == "depth":
            marker = f"…{truncated['hidden_nodes']} nodes below"
            if truncated["hidden_updated_max"]:
                marker += f" (deepest activity ~{truncated['hidden_updated_max']})"
        else:
            marker = (
                f"…{truncated['hidden_children']} more children "
                f"({truncated['hidden_nodes']} nodes) elided"
            )
        lines.append(indent + "  " + marker)

    _walk(outline, "", outline["path"])
    return "\n".join(lines)


def derive_display_alias(entity_path: str) -> str:
    """Derive a human-friendly alias from the entity leaf path."""
    leaf = entity_path.split("/")[-1]
    return leaf.replace("_", " ").strip().title() or leaf


# ---------------------------------------------------------------------------
# Note helpers
# ---------------------------------------------------------------------------


def _fmt_value(value: Any) -> str:
    """Render a frontmatter value compactly for a one-line note."""
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)


def _autofill_why(source: bool, aliases: bool, name: bool, create: bool) -> str:
    """Explain each autofill, so --explain says why and not just what."""
    reasons = []
    if source:
        reasons.append("source: none supplied and none on disk → default applied")
    if aliases:
        trigger = "--create with an empty alias list" if create else "no aliases supplied"
        reasons.append(f"aliases: {trigger} → derived from the path leaf")
    if name:
        reasons.append("name: not supplied → first alias without '@' or a leading '+'")
    return "; ".join(reasons)


def _lock_notes(lock: KBWriteLock) -> List[Dict[str, Any]]:
    """Report lock contention that was previously entirely silent.

    Two failure modes had no record anywhere: a process blocking up to 10s on
    another's lock, and a process forcibly breaking a lock it judged stale.
    The second one destroys another process's mutual exclusion, so it is
    reported at NORMAL rather than TRACE.
    """
    out: List[Dict[str, Any]] = []
    waited_ms = getattr(lock, "waited_ms", 0.0) or 0.0
    if getattr(lock, "broke_stale", False):
        out.append(
            nt.note(
                "waited",
                f"broke another process's stale write lock after {waited_ms:.0f}ms",
                level=nt.NORMAL,
                detail={"waited_ms": round(waited_ms, 1), "broke_stale": True},
                why="the lock's owner process was gone, or the lock exceeded its hard staleness limit",
            )
        )
    elif waited_ms >= 1000.0:
        out.append(
            nt.note(
                "waited",
                f"waited {waited_ms / 1000:.1f}s for another process's write lock",
                level=nt.NORMAL,
                detail={"waited_ms": round(waited_ms, 1), "broke_stale": False},
            )
        )
    elif waited_ms >= 1.0:
        # Sub-millisecond acquisitions are the uncontended norm; reporting them
        # would put a note on every single write even at --trace.
        out.append(
            nt.note(
                "waited",
                f"waited {waited_ms:.0f}ms for the write lock",
                detail={"waited_ms": round(waited_ms, 1), "broke_stale": False},
            )
        )
    return out


def _normalize_node_path(path: str) -> str:
    path = normalize_path(path or ".")
    return "." if path in ("", ".") else path


def _is_reserved_component(name: str) -> bool:
    """Return True for directory names kvault reserves for its own use.

    Hidden (".kvault") and internal ("_schema") namespaces are never semantic
    nodes.  resolve_node_path() refuses to write them and scan_entities() skips
    them, but child *enumeration* used to skip only "."-prefixed names — so an
    internal directory showed up as a child the node API then refused to read.
    Keeping one predicate here stops those phantom children (2026-07-26 audit).
    """
    return name.startswith(".") or name.startswith("_")


def _validate_node_path(path: str) -> Tuple[bool, Optional[str]]:
    if path == ".":
        return True, None
    parts = path.split("/")
    for part in parts:
        if not _NODE_COMPONENT_RE.match(part):
            return (
                False,
                f"Invalid path component: '{part}' ({INVALID_COMPONENT_HINT})",
            )
    return True, None


def _summary_path_for_node(kg_root: Path, path: str) -> Path:
    return kg_root / "_summary.md" if path == "." else kg_root / path / "_summary.md"


def _summary_rel_path(path: str) -> str:
    return "_summary.md" if path == "." else f"{path}/_summary.md"


def _safe_iterdir(path: Path) -> Iterable[Path]:
    try:
        return list(path.iterdir())
    except OSError:
        return []


def _parent_path(path: str) -> Optional[str]:
    if path == ".":
        return None
    parts = Path(path).parts
    if len(parts) <= 1:
        return "."
    return str(Path(*parts[:-1]))


def _ancestor_node_paths(path: str) -> List[str]:
    if path == ".":
        return []
    ancestors: List[str] = []
    current = _parent_path(path)
    while current is not None:
        ancestors.append(current)
        current = _parent_path(current)
    return ancestors


def _node_kind(kg_root: Path, path: str) -> str:
    if path == ".":
        return "root"
    parts = Path(path).parts
    node_dir = kg_root / path
    has_child_nodes = any(
        child.is_dir()
        and not _is_reserved_component(child.name)
        and (child / "_summary.md").exists()
        for child in _safe_iterdir(node_dir)
    )
    if len(parts) < 2 or has_child_nodes:
        return "category"
    return "entity"


def _extract_title(path: str, meta: Dict[str, Any], content: str) -> str:
    for key in ("name", "title", "topic"):
        value = meta.get(key)
        if value:
            return str(value)
    match = _HEADING_RE.search(content)
    if match:
        return match.group(1).strip()
    return _default_title("." if path == "." else path.split("/")[-1])


def _read_node_raw(kg_root: Path, path: str) -> Optional[Dict[str, Any]]:
    path = _normalize_node_path(path)
    is_valid, err_msg = _validate_node_path(path)
    if not is_valid or not validate_within_root(kg_root, path):
        return None

    summary_path = _summary_path_for_node(kg_root, path)
    if not summary_path.exists():
        return None
    raw = summary_path.read_text()
    meta, body = parse_frontmatter(raw)
    if not meta:
        meta_path = (kg_root if path == "." else kg_root / path) / "_meta.json"
        if meta_path.exists():
            with open(meta_path) as f:
                meta = json.load(f)
    content = body if meta else raw
    return {
        "path": path,
        "kind": _node_kind(kg_root, path),
        "summary_path": _summary_rel_path(path),
        "meta": meta,
        "content": content,
        "raw_content": raw,
        "has_frontmatter": bool(meta),
        "title": _extract_title(path, meta, content),
    }


def _node_handle(kg_root: Path, path: str) -> Dict[str, Any]:
    raw = _read_node_raw(kg_root, path) or {}
    return {
        "path": path,
        "kind": _node_kind(kg_root, path),
        "title": raw.get("title")
        or _extract_title(path, raw.get("meta", {}), raw.get("content", "")),
        "summary_path": _summary_rel_path(path),
    }


def _child_node_paths(kg_root: Path, path: str) -> List[str]:
    node_dir = kg_root if path == "." else kg_root / path
    children: List[str] = []
    for child in _safe_iterdir(node_dir):
        if not child.is_dir() or _is_reserved_component(child.name):
            continue
        if not (child / "_summary.md").exists():
            continue
        rel_path = str(child.relative_to(kg_root))
        children.append(rel_path)
    return sorted(children)


def _read_node_shallow(kg_root: Path, path: str) -> Optional[Dict[str, Any]]:
    raw = _read_node_raw(kg_root, path)
    if raw is None:
        return None
    node = {
        "path": raw["path"],
        "kind": raw["kind"],
        "summary_path": raw["summary_path"],
        "meta": raw["meta"],
        "content": raw["content"],
        "has_frontmatter": raw["has_frontmatter"],
        "title": raw["title"],
        "children": [
            _node_handle(kg_root, child) for child in _child_node_paths(kg_root, raw["path"])
        ],
    }
    return node


def _propagation_targets(kg_root: Path, path: str) -> List[Dict[str, Any]]:
    targets = []
    for ancestor in _ancestor_node_paths(path):
        summary_data = read_summary(kg_root, ancestor)
        if summary_data:
            targets.append(
                {
                    "path": ancestor,
                    "current_content": summary_data.get("content", ""),
                    "has_meta": bool(summary_data.get("meta")),
                }
            )
    return targets


def _summary_update_node(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return the public node shape used by strict summary update tools."""
    return {
        "path": raw["path"],
        "kind": raw["kind"],
        "summary_path": raw["summary_path"],
        "title": raw["title"],
        "meta": raw["meta"],
        "content": raw["content"],
        "has_frontmatter": raw["has_frontmatter"],
    }


class ChildDigestError(RuntimeError):
    """A parent's direct children could not be fully resolved for a digest.

    Deliberately loud.  The stale-write guard exists to reject a parent summary
    composed from incomplete child state; a digest taken over a *filtered* child
    list inverts that guarantee, because the guard then happily APPROVES a
    rewrite that erases the children it never saw.  Refusing is the only safe
    answer (2026-07-26 audit — verified data-loss path).
    """

    def __init__(self, parent_path: str, unreadable: List[str]) -> None:
        self.parent_path = parent_path
        self.unreadable = list(unreadable)
        super().__init__(
            f"Cannot compute a children digest for '{parent_path}': "
            f"{len(self.unreadable)} on-disk child node(s) exist but cannot be read "
            f"through the node API: {', '.join(self.unreadable)}. "
            "Refusing rather than dropping them — a digest over a partial child list "
            "would approve a parent summary that erases these children."
        )


def _direct_child_raw_nodes(
    kg_root: Path,
    path: str,
    child_paths: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Read the direct child nodes of *path*.

    Children that cannot be read are *not* dropped here — they are reported to
    _children_digest() via the ``child_paths`` enumeration so it can refuse.
    """
    children: List[Dict[str, Any]] = []
    for child_path in _child_node_paths(kg_root, path) if child_paths is None else child_paths:
        raw = _read_node_raw(kg_root, child_path)
        if raw is not None:
            children.append(raw)
    return children


def _children_digest(
    parent_path: str,
    children: List[Dict[str, Any]],
    expected_paths: Optional[Iterable[str]] = None,
) -> str:
    """Hash a parent's direct children for the stale-write guard.

    *expected_paths* is the raw on-disk child enumeration.  Any enumerated child
    missing from *children* raises ChildDigestError instead of being silently
    excluded from the hash — see that class for why silence is a data-loss bug.
    """
    if expected_paths is not None:
        # Compare NORMALIZED paths, but report the raw on-disk path.
        # children[]["path"] has been through _normalize_node_path (via
        # _read_node_raw) while expected_paths has not, so a raw comparison
        # false-positives on any child whose on-disk name normalizes to
        # something different -- e.g. mixed case on macOS's case-insensitive
        # filesystem. That would hard-block a legitimate summary update, which
        # is the opposite of the bug this guard exists to catch.
        present = {_normalize_node_path(child["path"]) for child in children}
        expected_by_norm = {_normalize_node_path(p): p for p in expected_paths}
        missing = sorted(expected_by_norm[key] for key in set(expected_by_norm) - present)
        if missing:
            raise ChildDigestError(parent_path, missing)
    sorted_children = sorted(children, key=lambda child: child["path"])
    payload = {
        "algorithm": SUMMARY_UPDATE_DIGEST_ALGORITHM,
        "parent_path": parent_path,
        "children": [
            {
                "path": child["path"],
                "summary_path": child["summary_path"],
                "raw_sha256": hashlib.sha256(child["raw_content"].encode("utf-8")).hexdigest(),
            }
            for child in sorted_children
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _hierarchy_hint(child_count: int) -> Optional[Dict[str, Any]]:
    if child_count <= MAX_DIRECT_CHILDREN:
        return None
    return {
        "code": "too_many_direct_children",
        "message": (
            f"Parent has {child_count} direct children; consider introducing "
            "intermediate branch nodes."
        ),
        "child_count": child_count,
        "max_direct_children": MAX_DIRECT_CHILDREN,
    }


# ---------------------------------------------------------------------------
# KB info (replaces _init_infrastructure output)
# ---------------------------------------------------------------------------


def get_kb_info(kg_root: Path) -> Dict[str, Any]:
    """Return hierarchy, entity count, and root summary for *kg_root*."""
    root_summary_path = kg_root / "_summary.md"
    root_summary = root_summary_path.read_text() if root_summary_path.exists() else ""
    outline = build_outline(kg_root, depth=2)
    return {
        "version": __version__,
        "kg_root": str(kg_root),
        "root_summary": root_summary,
        "hierarchy": render_outline_text(outline) if outline else "",
        "entity_count": count_entities(kg_root),
    }


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


def _read_entity_raw(kg_root: Path, entity_path: str) -> Optional[Dict[str, Any]]:
    """Read entity's ``_summary.md``, returning meta + content.

    Falls back to legacy ``_meta.json`` for metadata if no frontmatter.
    """
    if not validate_within_root(kg_root, entity_path):
        return None
    full_path = kg_root / entity_path
    summary_path = full_path / "_summary.md"
    if not summary_path.exists():
        return None
    content = summary_path.read_text()
    meta, body = parse_frontmatter(content)
    if not meta:
        meta_path = full_path / "_meta.json"
        if meta_path.exists():
            with open(meta_path) as f:
                meta = json.load(f)
    return {
        "path": entity_path,
        "meta": meta,
        "content": body if meta else content,
        "has_frontmatter": bool(meta) and summary_path.exists(),
    }


def read_entity(kg_root: Path, path: str) -> Optional[Dict[str, Any]]:
    """Read entity with parent summary for sibling context."""
    node = read_node(kg_root, path, parents="immediate")
    if not node:
        return None
    entity_data = {
        "path": node["path"],
        "meta": node.get("meta", {}),
        "content": node.get("content", ""),
        "has_frontmatter": node.get("has_frontmatter", False),
    }
    parent = node.get("parent")
    if parent:
        entity_data["parent_summary"] = parent.get("content", "")
        entity_data["parent_path"] = parent.get("path")
    return entity_data


def read_node(kg_root: Path, path: str, parents: str = "immediate") -> Optional[Dict[str, Any]]:
    """Read any node summary, with parent context by default."""
    path = _normalize_node_path(path)
    node = _read_node_shallow(kg_root, path)
    if node is None:
        return None

    if parents not in {"none", "immediate", "all"}:
        return None

    node["parent"] = None
    if parents in {"immediate", "all"}:
        parent_path = _parent_path(path)
        if parent_path is not None:
            node["parent"] = _read_node_shallow(kg_root, parent_path)

    if parents == "all":
        node["parents"] = [
            parent
            for ancestor in _ancestor_node_paths(path)
            if (parent := _read_node_shallow(kg_root, ancestor)) is not None
        ]

    return node


def read_summary(kg_root: Path, path: str) -> Optional[Dict[str, Any]]:
    """Read ``_summary.md`` at *path*."""
    path = normalize_path(path)
    if not validate_within_root(kg_root, path):
        return None
    summary_path = kg_root / path / "_summary.md"
    if not summary_path.exists():
        summary_path = kg_root / path
        if not summary_path.exists() or not path.endswith(".md"):
            return None
    content = summary_path.read_text()
    meta, body = parse_frontmatter(content)
    return {
        "path": path,
        "meta": meta,
        "content": body if meta else content,
    }


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def _resolve_entity_meta(
    kg_root: Path,
    entity_path: str,
    incoming_meta: Optional[Dict[str, Any]],
    create: bool,
    journal_source: Optional[str] = None,
    default_source: str = "auto:cli",
) -> Dict[str, Any]:
    """Merge incoming meta with existing meta and safe defaults."""
    meta: Dict[str, Any] = dict(incoming_meta or {})
    existing_meta: Dict[str, Any] = {}

    existing = _read_node_raw(kg_root, entity_path)
    if existing and isinstance(existing.get("meta"), dict):
        existing_meta = dict(existing["meta"])

    merged: Dict[str, Any] = dict(existing_meta)
    merged.update(meta)

    if not merged.get("source"):
        merged["source"] = journal_source or existing_meta.get("source") or default_source
        merged["_autofilled_source"] = True

    aliases = merged.get("aliases")
    if aliases is None and isinstance(existing_meta.get("aliases"), list):
        aliases = list(existing_meta["aliases"])
    if aliases is None:
        aliases = []
    if not isinstance(aliases, list):
        raise ValueError("frontmatter field 'aliases' must be a list")
    if create and len(aliases) == 0:
        aliases = [derive_display_alias(entity_path)]
        merged["_autofilled_aliases"] = True
    merged["aliases"] = aliases
    return merged


def write_entity(
    kg_root: Path,
    path: str,
    content: str,
    meta: Optional[Dict[str, Any]] = None,
    create: bool = False,
    reasoning: Optional[str] = None,
    journal_source: Optional[str] = None,
    default_source: str = "auto:cli",
    event_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Write entity with YAML frontmatter.

    Returns result dict with path, ancestors, and journal info.
    """
    path = normalize_path(path)
    is_valid, err_msg = validate_entity_path(path)
    if not is_valid:
        return error_response(ErrorCode.VALIDATION_ERROR, err_msg or "Invalid path")

    return write_node(
        kg_root,
        path=path,
        content=content,
        meta=meta,
        create=create,
        reasoning=reasoning,
        journal_source=journal_source,
        default_source=default_source,
        event_ids=event_ids,
    )


def _is_noop_node_write(
    existing: Dict[str, Any], content: str, resolved_meta: Dict[str, Any]
) -> bool:
    """True when *content* and *resolved_meta* match the existing node.

    ``created``/``updated`` are excluded from the comparison — they are the
    fields a no-op write must not refresh. Body comparison mirrors the
    ``parse_frontmatter`` round-trip (leading newlines stripped).
    """

    def _stable(meta: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in meta.items() if k not in ("created", "updated")}

    return existing["content"] == content.lstrip("\n") and _stable(
        existing.get("meta") or {}
    ) == _stable(resolved_meta)


def write_node(
    kg_root: Path,
    path: str,
    content: str,
    meta: Optional[Dict[str, Any]] = None,
    create: bool = False,
    reasoning: Optional[str] = None,
    journal_source: Optional[str] = None,
    default_source: str = "auto:cli",
    event_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Write any node summary with YAML frontmatter.

    When *event_ids* is given, each captured event must be pending (or
    already promoted — idempotent retries).  The write stamps
    ``journal:<event-id>`` provenance into the node's ``source_refs`` and
    resolves the events as promoted to this path.
    """
    path = _normalize_node_path(path)
    is_valid, err_msg = _validate_node_path(path)
    if not is_valid:
        return error_response(ErrorCode.VALIDATION_ERROR, err_msg or "Invalid path")
    if not validate_within_root(kg_root, path):
        return error_response(ErrorCode.VALIDATION_ERROR, "Path escapes KB root")

    try:
        resolve_node_path(kg_root, path, allow_root=(path == "."), reject_symlinks=True)
    except PathSafetyError as exc:
        return error_response(ErrorCode.VALIDATION_ERROR, str(exc))

    if event_ids:
        from kvault.core.events import check_events_promotable

        promotable = check_events_promotable(kg_root, event_ids)
        if not promotable.get("success"):
            return promotable

    full_path = kg_root if path == "." else kg_root / path
    summary_path = _summary_path_for_node(kg_root, path)

    # Check existence
    if create and summary_path.exists():
        return error_response(
            ErrorCode.ALREADY_EXISTS,
            f"Node already exists: {path}",
            hint="Use create=false to update existing entity",
        )
    if not create and not summary_path.exists():
        return error_response(
            ErrorCode.NOT_FOUND,
            f"Node doesn't exist: {path}",
            hint="Use create=true to create new entity",
        )

    if meta is not None and not isinstance(meta, dict):
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "frontmatter field 'meta' must be an object when provided",
            hint="Pass meta as a JSON object, or omit it to reuse/apply defaults",
        )

    try:
        meta = _resolve_entity_meta(
            kg_root=kg_root,
            entity_path=path,
            incoming_meta=meta,
            create=create,
            journal_source=journal_source,
            default_source=default_source,
        )
    except ValueError as exc:
        return error_response(ErrorCode.VALIDATION_ERROR, str(exc))

    autofilled_source = bool(meta.pop("_autofilled_source", False))
    autofilled_aliases = bool(meta.pop("_autofilled_aliases", False))

    if not isinstance(meta.get("source"), str) or not str(meta.get("source")).strip():
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "Missing required frontmatter field: source",
            details={"missing_fields": ["source"]},
            hint="Provide a source identifier (e.g., 'manual', 'imessage:thread_id')",
        )
    if not isinstance(meta.get("aliases"), list):
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "Missing required frontmatter field: aliases",
            details={"missing_fields": ["aliases"]},
            hint="Provide aliases as a list (can be empty: [])",
        )

    # Auto-set 'name' from first alias. This has had no signal at all until
    # now — the display name a node is known by was silently derived.
    autofilled_name = False
    if "name" not in meta and meta.get("aliases"):
        for alias in meta["aliases"]:
            if isinstance(alias, str) and "@" not in alias and not alias.startswith("+"):
                meta["name"] = alias
                autofilled_name = True
                break
        if "name" not in meta and meta["aliases"]:
            first = meta["aliases"][0]
            if isinstance(first, str):
                meta["name"] = first
                autofilled_name = True

    if event_ids:
        refs = list(meta.get("source_refs") or [])
        for event_id in event_ids:
            ref = f"journal:{event_id}"
            if ref not in refs:
                refs.append(ref)
        meta["source_refs"] = refs

    # Date fields — a no-op rewrite (same body, same meta) keeps existing
    # created/updated so bulk re-writes don't flatten the recency signal.
    today = datetime.now().strftime("%Y-%m-%d")
    is_noop = False
    noop_dates: Dict[str, Any] = {}
    if create:
        meta["created"] = today
        meta["updated"] = today
    else:
        existing = _read_node_raw(kg_root, path)
        # The legacy guard is load-bearing, and it CANNOT be has_frontmatter:
        # _read_node_raw sets that to bool(meta) AFTER falling back to
        # _meta.json, so a legacy node with metadata reports True. On such a
        # node the comparison can match, and taking the fast path below would
        # skip writing frontmatter *and* delete _meta.json — destroying the
        # node's metadata entirely (2026-08-11 review, reproduced). Any node
        # still carrying a _meta.json must take the full migrating write.
        if (
            existing is not None
            and existing.get("has_frontmatter")
            and not (full_path / "_meta.json").exists()
            and _is_noop_node_write(existing, content, meta)
        ):
            is_noop = True
            for key in ("created", "updated"):
                if key in existing["meta"]:
                    meta[key] = existing["meta"][key]
                    noop_dates[key] = existing["meta"][key]
                else:
                    meta.pop(key, None)
        else:
            meta["updated"] = today

    # Write
    frontmatter = build_frontmatter(meta)
    full_content = frontmatter + content
    meta_json_removed = False
    with KBWriteLock(kg_root) as lock:
        full_path.mkdir(parents=True, exist_ok=True)
        # A detected no-op skips the rewrite entirely rather than rewriting
        # identical bytes. That stops the mtime bump which made `kvault check`
        # manufacture PROPAGATE warnings for edits that never happened.
        if not is_noop:
            atomic_write_text(summary_path, full_content)

        # Legacy _meta.json cleanup runs on BOTH paths, including the no-op
        # fast path. storage.scan_entities still reads _meta.json as a
        # fallback identity source, so leaving it behind would let a node
        # carry two competing metadata records indefinitely.
        meta_json_path = full_path / "_meta.json"
        if meta_json_path.exists():
            meta_json_path.unlink()
            meta_json_removed = True

    notes: List[Dict[str, Any]] = []
    if autofilled_source or autofilled_aliases or autofilled_name:
        filled = {}
        if autofilled_source:
            filled["source"] = meta.get("source")
        if autofilled_aliases:
            filled["aliases"] = meta.get("aliases")
        if autofilled_name:
            filled["name"] = meta.get("name")
        notes.append(
            nt.note(
                "autofilled",
                " · ".join(f"{k}={_fmt_value(v)}" for k, v in filled.items()),
                detail=filled,
                why=_autofill_why(autofilled_source, autofilled_aliases, autofilled_name, create),
            )
        )
    if is_noop:
        preserved = ", ".join(f"{k} {v}" for k, v in sorted(noop_dates.items()))
        notes.append(
            nt.note(
                "unchanged",
                "body and metadata identical — file not rewritten"
                + (f", {preserved} preserved" if preserved else ""),
                detail=dict(noop_dates),
                why="compared body and all frontmatter except created/updated; no difference",
                next_step="nothing to do for this node",
            )
        )
    if meta_json_removed:
        notes.append(
            nt.note(
                "removed",
                "legacy _meta.json deleted — metadata now lives in frontmatter",
                detail={"path": f"{path}/_meta.json", "count": 1},
            )
        )
    for lock_note in _lock_notes(lock):
        notes.append(lock_note)

    result: Dict[str, Any] = {
        "success": True,
        "path": path,
        "created": create,
        "changed": not is_noop,
    }
    events_result: Optional[Dict[str, Any]] = None
    events_warning: Optional[str] = None
    if event_ids:
        from kvault.core.events import promote_events

        promotion = promote_events(kg_root, event_ids, path)
        events_result = promotion
        if not promotion.get("success"):
            # The node write already happened; surface the promotion failure
            # loudly instead of pretending the event was resolved.
            events_warning = (
                "Node was written but event promotion failed; resolve the "
                "events explicitly or retry with --event"
            )
            ids = ", ".join(event_ids)
            notes.append(
                nt.note(
                    "partial",
                    f"node written, but event promotion FAILED — "
                    f"{promotion.get('error', 'unknown error')}",
                    detail={
                        "event_ids": list(event_ids),
                        "error_code": promotion.get("error_code"),
                    },
                    why=(
                        "the event was pending when the write was admitted and changed "
                        "state before promotion ran; the node is on disk, the event is "
                        "not linked to it"
                    ),
                    next_step=f"kvault events show {event_ids[0]}",
                )
            )
            del ids

    # Auto-journal if reasoning provided
    journal_logged = False
    journal_path: Optional[str] = None
    if reasoning:
        action_type = "create" if create else "update"
        source = journal_source or meta.get("source", "unknown")
        journal_result = write_journal(
            kg_root,
            actions=[
                {
                    "action_type": action_type,
                    "path": path,
                    "reasoning": reasoning,
                }
            ],
            source=source,
        )
        journal_logged = journal_result.get("success", False)
        journal_path = journal_result.get("journal_path")

    # Fetch ancestor summaries for propagation.
    #
    # NOTE ON SEMANTICS: propagation_required means "ancestor summaries exist
    # and may need rolling up", NOT "this call dirtied them". It is
    # deliberately left true after a detected no-op: an earlier changed write
    # may still be unpropagated, and kvault cannot tell from this call alone.
    # Narrowing it to "did I change something" would make a stale chain
    # invisible on retry, which silently breaks the documented two-call
    # workflow. The `changed` flag above answers the narrower question.
    propagation_targets = _propagation_targets(kg_root, path)

    verb = "created" if create else ("no change to" if is_noop else "updated")
    did = f"{verb} {path}"
    if events_warning:
        did += "; event promotion failed"

    # KEY ORDER IS READING ORDER over MCP: FastMCP hands the model
    # json.dumps(result) and Python preserves insertion order. `ancestors`
    # carries the full current_content of every ancestor — three complete
    # documents on a typical write — so every decision signal is inserted
    # BEFORE it, and the bulk payload goes last.
    result["did"] = did
    if notes:
        result["notes"] = notes
    if nt.has_partial(notes):
        result["partial"] = True
    next_step = next((n["next"] for n in notes if n.get("next")), None)
    if next_step:
        result["next"] = next_step
    if autofilled_source or autofilled_aliases or autofilled_name:
        result["meta_autofilled"] = {
            "source": autofilled_source,
            "aliases": autofilled_aliases,
            "name": autofilled_name,
        }
    if events_result is not None:
        result["events"] = events_result
    if events_warning:
        result["events_warning"] = events_warning
    result["journal_logged"] = journal_logged
    if journal_path is not None:
        result["journal_path"] = journal_path
    result["propagation_required"] = len(propagation_targets) > 0
    result["ancestor_paths"] = [t["path"] for t in propagation_targets]
    result["ancestors"] = propagation_targets

    return result


def write_summary(
    kg_root: Path,
    path: str,
    content: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Write a single ``_summary.md``."""
    path = _normalize_node_path(path)
    if not validate_within_root(kg_root, path):
        return error_response(ErrorCode.VALIDATION_ERROR, "Path escapes KB root")
    is_valid, err_msg = _validate_node_path(path)
    if not is_valid:
        return error_response(ErrorCode.VALIDATION_ERROR, err_msg or "Invalid path")
    try:
        resolve_node_path(kg_root, path, allow_root=(path == "."), reject_symlinks=True)
    except PathSafetyError as exc:
        return error_response(ErrorCode.VALIDATION_ERROR, str(exc))
    dir_path = kg_root if path == "." else kg_root / path
    summary_path = _summary_path_for_node(kg_root, path)
    dir_existed = dir_path.exists()
    summary_existed = summary_path.exists()
    existing = _read_node_raw(kg_root, path)
    preserved_meta = existing.get("meta", {}) if existing and meta is None else {}
    final_meta = meta if meta is not None else preserved_meta

    # When the caller passes meta explicitly it REPLACES the existing
    # frontmatter rather than merging — keys the caller didn't repeat are
    # gone. That was silent; now it is a note. created/updated are NOT
    # excluded: unlike write_node, write_summary never re-stamps dates, so a
    # dropped `updated` really is lost (degrading check's PROPAGATE date
    # comparison to its mtime fallback for that node).
    dropped_keys: List[str] = []
    if meta is not None and existing and isinstance(existing.get("meta"), dict):
        dropped_keys = sorted(k for k in existing["meta"] if k not in meta)

    if final_meta:
        full_content = build_frontmatter(final_meta) + content
    else:
        full_content = content
    with KBWriteLock(kg_root) as lock:
        dir_path.mkdir(parents=True, exist_ok=True)
        atomic_write_text(summary_path, full_content)

    created = not summary_existed
    notes: List[Dict[str, Any]] = []
    if created and path != ".":
        text = f"new node created at {path}"
        if not dir_existed:
            text += " (directory did not exist)"
        notes.append(
            nt.note(
                "created",
                text,
                detail={"path": path, "dir_created": not dir_existed},
                why=(
                    "write-summary creates missing directories, so a typo'd path "
                    "silently forks a new subtree"
                ),
                next_step="verify the path is intended; kvault move --confirm to relocate",
            )
        )
    if dropped_keys:
        notes.append(
            nt.note(
                "removed",
                "frontmatter replaced wholesale — dropped keys: " + ", ".join(dropped_keys),
                detail={"path": path, "dropped_keys": dropped_keys},
                why=(
                    "meta passed to write-summary replaces existing frontmatter "
                    "instead of merging; omit meta to preserve it"
                ),
            )
        )
    notes.extend(_lock_notes(lock))

    result: Dict[str, Any] = {
        "success": True,
        "path": path,
        "created": created,
        "did": ("created" if created else "updated") + f" summary {path}",
    }
    if notes:
        result["notes"] = notes
    return result


def prepare_summary_update(kg_root: Path, path: str) -> Dict[str, Any]:
    """Return parent and direct-child summaries for a strict parent update."""
    path = _normalize_node_path(path)
    is_valid, err_msg = _validate_node_path(path)
    if not is_valid:
        return error_response(ErrorCode.VALIDATION_ERROR, err_msg or "Invalid path")
    if not validate_within_root(kg_root, path):
        return error_response(ErrorCode.VALIDATION_ERROR, "Path escapes KB root")

    parent_raw = _read_node_raw(kg_root, path)
    if parent_raw is None:
        return error_response(ErrorCode.NOT_FOUND, f"Parent node not found: {path}")

    # Enumerate once, then require the digest to cover every enumerated child.
    child_paths = _child_node_paths(kg_root, path)
    children_raw = _direct_child_raw_nodes(kg_root, path, child_paths=child_paths)
    child_count = len(children_raw)
    try:
        digest = _children_digest(path, children_raw, expected_paths=child_paths)
    except ChildDigestError as exc:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            str(exc),
            details={"path": path, "unreadable_children": exc.unreadable},
            hint=(
                "Rename each listed directory to a valid node component "
                "(lowercase letters/digits, then letters/digits/'_'/'-'), or move it "
                "out of the KB. Do not update this parent summary until it resolves."
            ),
        )
    return {
        "success": True,
        "path": path,
        "parent": _summary_update_node(parent_raw),
        "children": [_summary_update_node(child) for child in children_raw],
        "child_count": child_count,
        "children_digest": digest,
        "digest_algorithm": SUMMARY_UPDATE_DIGEST_ALGORITHM,
        "max_direct_children": MAX_DIRECT_CHILDREN,
        "hierarchy_hint": _hierarchy_hint(child_count),
    }


def write_parent_summary(
    kg_root: Path,
    path: str,
    content: str,
    children_digest: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Write a parent summary only if direct children match *children_digest*."""
    if not isinstance(children_digest, str) or not children_digest.strip():
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "children_digest is required",
            hint="Call prepare_summary_update first and pass its children_digest.",
        )

    with KBWriteLock(kg_root):
        return _write_parent_summary_locked(kg_root, path, content, children_digest, meta)


def _write_parent_summary_locked(
    kg_root: Path,
    path: str,
    content: str,
    children_digest: str,
    meta: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    prepared = prepare_summary_update(kg_root, path)
    if not prepared.get("success"):
        return prepared

    expected_digest = prepared["children_digest"]
    if children_digest != expected_digest:
        return error_response(
            ErrorCode.WORKFLOW_ERROR,
            "children_digest is stale for parent summary update",
            details={
                "path": prepared["path"],
                "received_digest": children_digest,
                "expected_digest": expected_digest,
                "child_count": prepared["child_count"],
                "hierarchy_hint": prepared["hierarchy_hint"],
            },
            hint="Call kvault_prepare_summary_update again and rewrite from current children.",
        )

    result = write_summary(kg_root, prepared["path"], content, meta=meta)
    if not result.get("success"):
        return result

    # Carry the nested write's narration — this is the RECOMMENDED parent
    # path over MCP, and dropping the notes here meant the wholesale-meta
    # 'removed' note fired only on the discouraged tool.
    out: Dict[str, Any] = {
        "success": True,
        "path": prepared["path"],
        "did": result.get("did"),
    }
    if result.get("notes"):
        out["notes"] = result["notes"]
    out.update(
        {
            "child_count": prepared["child_count"],
            "children_digest": expected_digest,
            "digest_algorithm": SUMMARY_UPDATE_DIGEST_ALGORITHM,
            "max_direct_children": MAX_DIRECT_CHILDREN,
            "hierarchy_hint": prepared["hierarchy_hint"],
        }
    )
    return out


def update_summaries(
    kg_root: Path,
    updates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Batch-update multiple summary files."""
    updated: List[str] = []
    errors: List[Dict[str, Any]] = []
    with KBWriteLock(kg_root):
        return _update_summaries_locked(kg_root, updates, updated, errors)


def _update_summaries_locked(
    kg_root: Path,
    updates: List[Dict[str, Any]],
    updated: List[str],
    errors: List[Dict[str, Any]],
) -> Dict[str, Any]:
    item_notes: List[Dict[str, Any]] = []
    for item in updates:
        p = item.get("path")
        c = item.get("content")
        m = item.get("meta")
        if not p or c is None:
            errors.append({"path": p or "<missing>", "error": "Missing path or content"})
            continue
        try:
            r = write_summary(kg_root, path=p, content=c, meta=m)
            if r.get("success"):
                updated.append(p)
                item_notes.extend(r.get("notes") or [])
            else:
                errors.append({"path": p, "error": r.get("error", "Unknown error")})
        except Exception as e:
            errors.append({"path": p, "error": str(e)})

    # Per-item notes are collapsed by code at the batch boundary — the ONLY
    # place this loop's fan-out is aggregated. Without this, a 40-ancestor
    # maintenance batch emits 40+ near-identical notes.
    notes: List[Dict[str, Any]] = []
    if errors:
        notes.append(
            nt.note(
                "partial",
                f"{len(errors)} of {len(updates)} summary updates failed",
                detail={"failed_paths": [e.get("path") for e in errors]},
                why="the batch continues past individual failures; success reflects the batch, not each item",
                next_step="inspect errors[] and re-run the failed items",
            )
        )
    notes.extend(nt.collapse(item_notes))

    # `success` semantics deliberately unchanged (true when anything updated
    # or the batch was empty). `partial` is the honest signal for mixed
    # outcomes; flipping `success` here would break existing consumers.
    result: Dict[str, Any] = {
        "success": len(updated) > 0 or len(updates) == 0,
        "did": f"updated {len(updated)} of {len(updates)} summaries",
        "updated": updated,
        "count": len(updated),
        "attempted": len(updates),
        "failed": len(errors),
    }
    if errors:
        result["partial"] = True
    if notes:
        result["notes"] = notes
    if errors:
        result["errors"] = errors
    return result


# ---------------------------------------------------------------------------
# List / delete / move
# ---------------------------------------------------------------------------


def list_entities(kg_root: Path, category: Optional[str] = None) -> List[Dict[str, Any]]:
    """List entities, optionally filtered by category."""
    entries = list_entity_records(kg_root, category=category)
    return [
        {
            "path": e.path,
            "name": e.name,
            "category": e.category,
            "last_updated": e.last_updated,
        }
        for e in entries
    ]


def list_nodes(kg_root: Path, path: str = ".", recursive: bool = False) -> List[Dict[str, Any]]:
    """List child nodes under *path*."""
    path = _normalize_node_path(path)
    if _read_node_raw(kg_root, path) is None:
        return []

    nodes: List[Dict[str, Any]] = []

    def _walk(parent: str) -> None:
        for child in _child_node_paths(kg_root, parent):
            nodes.append(_node_handle(kg_root, child))
            if recursive:
                _walk(child)

    _walk(path)
    return nodes


def search_nodes(
    kg_root: Path,
    query: str,
    limit: int = 10,
    include_content: bool = False,
    content_max_chars: int = 6000,
    total_max_chars: int = 20000,
    collapse: bool = True,
    kinds: Optional[Sequence[str]] = None,
    path_prefix: Optional[str] = None,
) -> Dict[str, Any]:
    """Search visible kvault node summaries."""
    from kvault.core.search import search_nodes as _search_nodes

    return _search_nodes(
        kg_root,
        query=query,
        limit=limit,
        include_content=include_content,
        content_max_chars=content_max_chars,
        total_max_chars=total_max_chars,
        collapse=collapse,
        kinds=kinds,
        path_prefix=path_prefix,
    )


def delete_entity(kg_root: Path, path: str) -> Dict[str, Any]:
    """Delete an entity directory."""
    path = normalize_path(path)
    try:
        full_path = validate_node_target(kg_root, path, require_exists=False)
    except PathSafetyError as exc:
        return error_response(ErrorCode.VALIDATION_ERROR, str(exc))
    if not full_path.exists():
        return error_response(ErrorCode.NOT_FOUND, f"Entity doesn't exist: {path}")
    try:
        validate_node_target(kg_root, path, require_exists=True)
    except PathSafetyError as exc:
        return error_response(ErrorCode.VALIDATION_ERROR, str(exc))
    with KBWriteLock(kg_root) as lock:
        # Count BEFORE rmtree — the only moment the answer to "what did I
        # just destroy" is still knowable.
        nodes_deleted = sum(1 for _ in full_path.rglob("_summary.md"))
        files_deleted = sum(1 for p in full_path.rglob("*") if p.is_file())
        shutil.rmtree(full_path)

    targets = _propagation_targets(kg_root, path)
    notes = [
        nt.note(
            "removed",
            f"deleted {nodes_deleted} node(s), {files_deleted} file(s) under {path}",
            detail={"path": path, "nodes": nodes_deleted, "files": files_deleted},
        ),
        nt.note(
            "propagate",
            f"{len(targets)} ancestor summaries may still describe the deleted subtree",
            level=nt.NORMAL,
            detail={"ancestor_paths": [t["path"] for t in targets]},
            why="delete does not rewrite parents; their rollups now reference nodes that are gone",
            next_step="kvault update-summaries",
        ),
    ]
    notes.extend(_lock_notes(lock))
    return {
        "success": True,
        "path": path,
        "deleted": True,
        "did": f"deleted {path} ({nodes_deleted} nodes, {files_deleted} files)",
        "notes": notes,
        "nodes_deleted": nodes_deleted,
        "files_deleted": files_deleted,
        "propagation_required": len(targets) > 0,
        "ancestor_paths": [t["path"] for t in targets],
        "ancestors": targets,
    }


def move_entity(kg_root: Path, source_path: str, target_path: str) -> Dict[str, Any]:
    """Move an entity to a new path."""
    source_path = normalize_path(source_path)
    target_path = normalize_path(target_path)

    is_valid, err_msg = validate_entity_path(source_path)
    if not is_valid:
        return error_response(ErrorCode.VALIDATION_ERROR, f"Invalid source path: {err_msg}")
    is_valid, err_msg = validate_entity_path(target_path)
    if not is_valid:
        return error_response(ErrorCode.VALIDATION_ERROR, f"Invalid target path: {err_msg}")
    if target_path == source_path or target_path.startswith(source_path + "/"):
        return error_response(ErrorCode.VALIDATION_ERROR, "Cannot move a node into its own subtree")
    try:
        source_full = resolve_node_path(kg_root, source_path, reject_symlinks=True)
        target_full = resolve_node_path(kg_root, target_path, reject_symlinks=True)
    except PathSafetyError as exc:
        return error_response(ErrorCode.VALIDATION_ERROR, str(exc))

    if not source_full.exists():
        return error_response(ErrorCode.NOT_FOUND, f"Source doesn't exist: {source_path}")
    if target_full.exists():
        return error_response(ErrorCode.ALREADY_EXISTS, f"Target already exists: {target_path}")

    with KBWriteLock(kg_root) as lock:
        nodes_moved = sum(1 for _ in source_full.rglob("_summary.md"))
        target_full.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_full), str(target_full))

    # BOTH ancestor chains are stale after a move: the source chain still
    # describes a subtree that left, the target chain doesn't yet describe
    # the one that arrived.
    src_targets = _propagation_targets(kg_root, source_path)
    tgt_targets = _propagation_targets(kg_root, target_path)
    combined: List[Dict[str, Any]] = []
    seen_paths: Set[str] = set()
    for t in src_targets + tgt_targets:
        if t["path"] not in seen_paths:
            seen_paths.add(t["path"])
            combined.append(t)

    notes = [
        nt.note(
            "propagate",
            f"both ancestor chains are stale: {len(combined)} summaries to update",
            level=nt.NORMAL,
            detail={
                "source_chain": [t["path"] for t in src_targets],
                "target_chain": [t["path"] for t in tgt_targets],
            },
            why=(
                "the source chain still describes the moved subtree; "
                "the target chain does not describe it yet"
            ),
            next_step="kvault update-summaries",
        )
    ]
    notes.extend(_lock_notes(lock))
    return {
        "success": True,
        "source": source_path,
        "target": target_path,
        "did": f"moved {source_path} → {target_path} ({nodes_moved} nodes)",
        "notes": notes,
        "nodes_moved": nodes_moved,
        "propagation_required": len(combined) > 0,
        "ancestor_paths": [t["path"] for t in combined],
        "ancestors_source": [t["path"] for t in src_targets],
        "ancestors_target": [t["path"] for t in tgt_targets],
        "ancestors": combined,
    }


# ---------------------------------------------------------------------------
# Ancestors
# ---------------------------------------------------------------------------


def get_ancestors(kg_root: Path, path: str) -> Dict[str, Any]:
    """Get all ancestor summaries for propagation."""
    path = normalize_path(path)
    storage = SimpleStorage(kg_root)
    ancestors = storage.get_ancestors(path)

    propagation_targets = []
    for ancestor in ancestors:
        summary_data = read_summary(kg_root, ancestor)
        if summary_data:
            propagation_targets.append(
                {
                    "path": ancestor,
                    "current_content": summary_data.get("content", ""),
                    "has_meta": bool(summary_data.get("meta")),
                }
            )

    root_summary = read_summary(kg_root, ".")
    if root_summary:
        propagation_targets.append(
            {
                "path": ".",
                "current_content": root_summary.get("content", ""),
                "has_meta": bool(root_summary.get("meta")),
            }
        )

    return {
        "success": True,
        "ancestors": propagation_targets,
        "count": len(propagation_targets),
    }


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


def write_journal(
    kg_root: Path,
    actions: List[Dict[str, Any]],
    source: str,
    date: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a journal entry."""
    dt = datetime.now()
    guessed_date: Optional[str] = None
    if date:
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            # The fallback used to be completely silent — the entry landed
            # under today's date with no indication the input was discarded.
            guessed_date = date

    journal_rel_path = get_journal_path(dt)
    journal_full_path = kg_root / journal_rel_path

    with KBWriteLock(kg_root):
        journal_full_path.parent.mkdir(parents=True, exist_ok=True)

        entry = format_journal_entry(actions, source, dt)
        if journal_full_path.exists():
            existing = journal_full_path.read_text()
            entry = existing.rstrip() + "\n\n" + entry
        else:
            header = f"# Journal - {dt.strftime('%B %Y')}\n\n"
            entry = header + entry

        atomic_write_text(journal_full_path, entry)
    result: Dict[str, Any] = {
        "success": True,
        "journal_path": journal_rel_path,
        "actions_logged": len(actions),
    }
    if guessed_date:
        result["notes"] = [
            nt.note(
                "guessed",
                f"date '{guessed_date}' is not YYYY-MM-DD — entry filed under today "
                f"({dt.strftime('%Y-%m-%d')})",
                detail={"input": guessed_date, "used": dt.strftime("%Y-%m-%d")},
                why="an unparseable date falls back to today rather than failing the journal write",
            )
        ]
    return result


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def _is_incomplete_entity(content: str) -> bool:
    """True only for stub entities — empty bodies or pure placeholder scaffolding.

    An entity is incomplete when, after dropping the title/headings and blank lines,
    it has no substantive prose, OR every remaining line is just a placeholder marker
    (``TBD``, ``Context: TBD``, ``TODO`` …). A single placeholder field inside an
    otherwise filled entity (e.g. ``Lead time: TBD``) does NOT count — that's a real
    datum, not a stub.
    """
    body_lines = [s for ln in content.splitlines() if (s := ln.strip()) and not s.startswith("#")]
    if not body_lines:
        return True
    return all(_PLACEHOLDER_LINE_RE.match(ln) for ln in body_lines)


def validate_kb(kg_root: Path) -> Dict[str, Any]:
    """Check KB integrity and report issues."""
    issues: List[Dict[str, Any]] = []
    entities = scan_entities(kg_root)

    # Malformed frontmatter makes a node invisible to entity scans, so this
    # check walks summary files directly instead of relying on scan_entities.
    for summary_file in sorted(kg_root.rglob("_summary.md")):
        rel_parts = summary_file.parent.relative_to(kg_root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        try:
            parse_frontmatter_strict(summary_file.read_text(encoding="utf-8"))
        except FrontmatterError as exc:
            issues.append(
                {
                    "type": "malformed_frontmatter",
                    "severity": "warning",
                    "path": str(Path(*rel_parts)) if rel_parts else ".",
                    "message": f"Frontmatter is malformed and read as empty: {exc}",
                    "fix": "Rewrite the node with kvault write to repair its frontmatter",
                }
            )
        except OSError:
            continue

    for entity in entities:
        entity_data = _read_entity_raw(kg_root, entity.path)
        if entity_data:
            content = entity_data.get("content", "")
            if _is_incomplete_entity(content):
                issues.append(
                    {
                        "type": "incomplete_entity",
                        "severity": "info",
                        "path": entity.path,
                        "message": "Entity has placeholder content that needs enrichment",
                        "fix": "Update entity with complete context information",
                    }
                )
            if not entity_data.get("has_frontmatter"):
                issues.append(
                    {
                        "type": "missing_frontmatter",
                        "severity": "warning",
                        "path": entity.path,
                        "message": "Entity uses legacy _meta.json instead of YAML frontmatter",
                        "fix": "Rewrite entity with kvault write to migrate to frontmatter",
                    }
                )

    severity_order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda x: severity_order.get(x["severity"], 99))
    return {
        "valid": len([i for i in issues if i["severity"] in ("error", "warning")]) == 0,
        "issue_count": len(issues),
        "issues": issues,
        "summary": {
            "errors": len([i for i in issues if i["severity"] == "error"]),
            "warnings": len([i for i in issues if i["severity"] == "warning"]),
            "info": len([i for i in issues if i["severity"] == "info"]),
        },
    }
