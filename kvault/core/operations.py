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
from kvault.core import decisions as dc
from kvault.core import notes as nt
from kvault.core import structure as st
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
    ignore = st.load_ignore(kg_root)
    return _walk_outline(kg_root, path, depth, max_children, include_gist, 0, visited, ignore)


def _walk_outline(
    kg_root: Path,
    path: str,
    depth: Optional[int],
    max_children: Optional[int],
    include_gist: bool,
    level: int,
    visited: Set[Path],
    ignore: Sequence[str] = (),
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
            kg_root, child_path, depth, max_children, include_gist, level + 1, visited, ignore
        )
        if child is not None:
            children.append(child)
    # Directories with no summary are invisible to every other surface; the
    # outline at least counts them so an agent knows the shape it cannot see.
    # journal/ months are the canonical layout, not ghosts: a reserved parent
    # never counts its children.
    ghost_count = (
        0
        if path != "." and st.is_reserved_name(slug)
        else sum(
            1
            for d in st.child_dirs(kg_root if path == "." else kg_root / path, kg_root, ignore)
            if st.is_ghost(d)
        )
    )

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
        "ghost_count": ghost_count,
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
        counts: List[str] = []
        if node["children_count"]:
            counts.append(f"{node['children_count']} children, {node['descendants_count']} total")
        if node.get("ghost_count"):
            counts.append(f"+{node['ghost_count']} ghost")
        if counts:
            parts.append(f"[{', '.join(counts)}]")
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


def _hierarchy_hint(
    child_count: int, ceiling: int = MAX_DIRECT_CHILDREN
) -> Optional[Dict[str, Any]]:
    if child_count <= ceiling:
        return None
    return {
        "code": "too_many_direct_children",
        "message": (
            f"Parent has {child_count} direct children; consider introducing "
            "intermediate branch nodes."
        ),
        "child_count": child_count,
        "max_direct_children": ceiling,
    }


# ---------------------------------------------------------------------------
# Structure guards (0.15)
# ---------------------------------------------------------------------------
#
# Why these exist: a 1,045-node KB grew 119 flat children under projects/,
# 23 root categories, and infra/ beside infrastructure/ beside
# tech/infrastructure/ — while validate stayed green. Every one of those was
# a legitimate `write --create` that nothing looked at. The orientation tree
# prunes to 20 children, so the agent never saw the sibling it duplicated;
# `mkdir(parents=True)` minted summary-less parents no surface could see;
# and "search before create" was an instruction to the model, which a small
# model running unattended skips. These are the server-side checks.


def _stub_body(path: str, trigger: str) -> str:
    title = _default_title(path.split("/")[-1])
    return (
        f"# {title}\n\n"
        f"Placeholder summary created by kvault when `{trigger}` was written. "
        "Rewrite it as a rollup of its children.\n"
    )


def _missing_ancestor_summaries(kg_root: Path, path: str) -> List[str]:
    """Ancestors of *path* (root excluded) with no ``_summary.md``, shallowest first."""
    # deep_context/ and journal/ are reserved: background material and the
    # log. Stubbing a summary there turns them into phantom nodes (seen on a
    # real KB after a series fold), so they are never stubbed.
    missing = [
        a
        for a in _ancestor_node_paths(path)
        if a != "."
        and not st.is_reserved_name(a.rsplit("/", 1)[-1])
        and not _summary_path_for_node(kg_root, a).exists()
    ]
    return sorted(missing, key=lambda a: a.count("/"))


def _write_stub_summaries(kg_root: Path, paths: Sequence[str], trigger: str) -> List[str]:
    """Write a self-flagging stub for each path (caller holds the write lock).

    The body says "Placeholder" on purpose: `kvault check` keeps reporting
    ``placeholder_language`` until an agent rewrites it as a real rollup.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    written: List[str] = []
    for rel_path in paths:
        summary_path = _summary_path_for_node(kg_root, rel_path)
        if summary_path.exists():
            continue
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        meta = {"created": today, "updated": today, "source": "kvault-stub", "aliases": []}
        atomic_write_text(summary_path, build_frontmatter(meta) + _stub_body(rel_path, trigger))
        written.append(rel_path)
    return written


def _stub_note(written: Sequence[str]) -> Dict[str, Any]:
    n = len(written)
    return nt.note(
        "created",
        f"{n} intermediate summar{'y' if n == 1 else 'ies'} stubbed: " + ", ".join(written),
        detail={"paths": list(written), "count": n},
        why=(
            "a directory without _summary.md is invisible to tree, search, and check; "
            "kvault no longer mints those"
        ),
        next_step="kvault update-summaries — the stubs are in ancestor_paths; rewrite them as rollups",
    )


def _root_guard(
    kg_root: Path, path: str, new_root: bool, ignore: Sequence[str]
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Refuse to mint a root category unless *new_root*; note it when allowed.

    A KB with no root categories yet (built by hand, not ``kvault init``)
    has no orientation to protect: its first roots are allowed and noted.
    The guard is for the 2nd..23rd root, which is where the audited KB
    went wrong.
    """
    root_component = path.split("/")[0]
    if (kg_root / root_component).is_dir():
        return None, []
    existing_roots = [d.name for d in st.child_dirs(kg_root, kg_root, ignore)]
    if existing_roots and not new_root:
        return (
            error_response(
                ErrorCode.VALIDATION_ERROR,
                f"'{path}' would add a new root category '{root_component}' "
                f"({len(existing_roots)} exist: {', '.join(existing_roots[:12])}"
                + (" …" if len(existing_roots) > 12 else "")
                + ")",
                details={
                    "reason": "new_root",
                    "proposed_root": root_component,
                    "existing_roots": existing_roots,
                },
                hint=(
                    "Create it under an existing root, or pass --new-root "
                    "(new_root=true) to add a root category deliberately"
                ),
            ),
            [],
        )
    note = nt.note(
        "structure",
        f"new root category '{root_component}' ({len(existing_roots) + 1} roots)",
        detail={
            "kind": "new_root",
            "root": root_component,
            "existing_roots": existing_roots,
        },
        why=(
            "root categories are the top of every agent's orientation pass; "
            "each one is a structural decision"
        ),
        next_step="kvault update-summaries — describe the new root in the root summary",
    )
    return None, [note]


def _create_guard(
    kg_root: Path,
    path: str,
    new_root: bool,
    allow_similar: bool,
    incoming_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the structural checks for a create. Returns an error response or
    ``{"success": True, "notes": [...], "stubs": [...]}``."""
    ignore = st.load_ignore(kg_root)
    err, notes = _root_guard(kg_root, path, new_root, ignore)
    if err is not None:
        return err

    parent_path = _parent_path(path) or "."
    parent_dir = kg_root if parent_path == "." else kg_root / parent_path
    leaf = path.split("/")[-1]
    # Adopting a ghost directory: the directory already exists, so it must
    # not count as its own sibling (that fired over_fanout one create early).
    siblings = (
        [d.name for d in st.child_dirs(parent_dir, kg_root, ignore) if d.name != leaf]
        if parent_dir.is_dir()
        else []
    )

    # A recorded decision beats a name comparison: `distinct_from` on the
    # incoming node or on the existing sibling means "different things".
    raw_declared = (incoming_meta or {}).get("distinct_from") or []
    if isinstance(raw_declared, str):
        raw_declared = [raw_declared]
    declared_paths = {dc.normalize_rel(str(v), path) for v in raw_declared if str(v).strip()}
    collisions = [
        c
        for c in st.sibling_collisions(siblings, leaf)
        if _join_path(parent_path, c.name) not in declared_paths
        and path not in dc.read_decisions(kg_root, _join_path(parent_path, c.name))["distinct_from"]
    ]
    hard = [c for c in collisions if c.kind == "same_words"]
    if hard and not allow_similar:
        twin = _join_path(parent_path, hard[0].name)
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            f"'{path}' collides with existing sibling '{twin}' (same words)",
            details={
                "reason": "similar",
                "collisions": [c.as_dict() for c in collisions],
                "existing": twin,
            },
            hint=(
                f"Update the existing node (kvault read {twin}), or pass "
                "--allow-similar (allow_similar=true) to create anyway"
            ),
        )
    elsewhere = st.basename_matches(kg_root, leaf, exclude=path, ignore=ignore)
    if collisions or elsewhere:
        parts: List[str] = []
        for c in collisions:
            score = "" if c.kind == "same_words" else f" {c.score}"
            parts.append(f"{c.name} ({c.kind}{score})")
        if elsewhere:
            parts.append(f"same name at {', '.join(elsewhere[:3])}")
        first = _join_path(parent_path, collisions[0].name) if collisions else elsewhere[0]
        notes.append(
            nt.note(
                "structure",
                "similar: " + "; ".join(parts),
                detail={
                    "kind": "similar",
                    "siblings": [c.as_dict() for c in collisions],
                    "elsewhere": elsewhere[:5],
                },
                why="a near-duplicate name is how flat sprawl starts; kvault compared names only",
                next_step=f"kvault read {first} — if it is the same thing, update it and delete this one",
            )
        )

    new_count = len(siblings) + 1
    ceiling = dc.child_ceiling(kg_root, parent_path, MAX_DIRECT_CHILDREN)
    if new_count > ceiling:
        notes.append(
            nt.note(
                "structure",
                f"{parent_path} now has {new_count} direct children (ceiling {ceiling})",
                detail={
                    "kind": "over_fanout",
                    "parent": parent_path,
                    "child_count": new_count,
                    "max_direct_children": ceiling,
                },
                why=(
                    "past the ceiling the orientation tree elides children and a parent "
                    "rollup stops fitting on an index page"
                ),
                next_step=f"kvault plan {parent_path}",
            )
        )
    return {
        "success": True,
        "notes": notes,
        "stubs": _missing_ancestor_summaries(kg_root, path),
    }


def _join_path(parent: str, name: str) -> str:
    return name if parent == "." else f"{parent}/{name}"


# ---------------------------------------------------------------------------
# KB info (replaces _init_infrastructure output)
# ---------------------------------------------------------------------------


def get_kb_info(kg_root: Path, include_root_summary: bool = False) -> Dict[str, Any]:
    """Return version, hierarchy, entity count, and (opt-in) root summary.

    ``root_summary`` is opt-in since 0.14.0: on a mature KB it was ~97% of a
    56 KB status payload that agents read at session start. The size is
    always reported so a caller can decide whether to fetch it.
    """
    root_summary_path = kg_root / "_summary.md"
    root_summary = root_summary_path.read_text() if root_summary_path.exists() else ""
    outline = build_outline(kg_root, depth=2)
    info: Dict[str, Any] = {
        "version": __version__,
        "kg_root": str(kg_root),
        "root_summary_chars": len(root_summary),
        "hierarchy": render_outline_text(outline) if outline else "",
        "entity_count": count_entities(kg_root),
    }
    if include_root_summary:
        info["root_summary"] = root_summary
    return info


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


def read_entity(kg_root: Path, path: str, parents: str = "immediate") -> Optional[Dict[str, Any]]:
    """Read entity, with the parent summary for sibling context by default."""
    node = read_node(kg_root, path, parents=parents)
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
    drop_keys: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Merge incoming meta with existing meta and safe defaults."""
    meta: Dict[str, Any] = dict(incoming_meta or {})
    existing_meta: Dict[str, Any] = {}

    existing = _read_node_raw(kg_root, entity_path)
    if existing and isinstance(existing.get("meta"), dict):
        existing_meta = dict(existing["meta"])

    merged: Dict[str, Any] = dict(existing_meta)
    merged.update(meta)
    # A merge cannot express "remove this key"; callers that need to (mark
    # --clear) say so explicitly.
    for key in drop_keys or ():
        merged.pop(key, None)

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
    new_root: bool = False,
    allow_similar: bool = False,
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
        new_root=new_root,
        allow_similar=allow_similar,
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
    new_root: bool = False,
    allow_similar: bool = False,
    drop_meta_keys: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Write any node summary with YAML frontmatter.

    A create runs the structure guards first (0.15): it is refused when it
    would mint a root category without *new_root* or collide with a sibling
    of the same words without *allow_similar*; near-duplicate names and
    over-ceiling parents are reported as ``structure`` notes; and missing
    intermediate parents get stub summaries (a ``created`` note) instead of
    becoming invisible directories.

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

    structure_notes: List[Dict[str, Any]] = []
    stub_paths: List[str] = []
    if create and path != ".":
        guard = _create_guard(
            kg_root, path, new_root=new_root, allow_similar=allow_similar, incoming_meta=meta
        )
        if not guard.get("success"):
            return guard
        structure_notes = guard["notes"]
        stub_paths = guard["stubs"]

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
            drop_keys=drop_meta_keys,
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

    dropped_retracted: List[str] = []
    if event_ids:
        from kvault.core.events import retracted_event_ids

        refs = list(meta.get("source_refs") or [])
        # Re-linking a node to a corrected capture is the documented close-out
        # for a RETRACTED: finding — the retracted ref must not linger.
        retracted = retracted_event_ids(kg_root)
        if retracted:
            keep = []
            for ref in refs:
                if isinstance(ref, str) and ref.startswith("journal:") and ref[8:] in retracted:
                    dropped_retracted.append(ref[8:])
                else:
                    keep.append(ref)
            refs = keep
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
    stubs_written: List[str] = []
    with KBWriteLock(kg_root) as lock:
        full_path.mkdir(parents=True, exist_ok=True)
        if stub_paths:
            stubs_written = _write_stub_summaries(kg_root, stub_paths, trigger=path)
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
    if stubs_written:
        notes.append(_stub_note(stubs_written))
    notes.extend(structure_notes)
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

    if dropped_retracted:
        notes.append(
            nt.note(
                "removed",
                f"dropped {len(dropped_retracted)} retracted provenance ref(s): "
                + ", ".join(dropped_retracted),
                detail={"retracted_refs_dropped": dropped_retracted},
                why="the node now cites the superseding capture; a retracted event is wrong evidence",
            )
        )

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


def _summary_update_gist(raw: Dict[str, Any]) -> Dict[str, Any]:
    """The bounded child shape: enough to write a rollup line, not the body."""
    return {
        "path": raw["path"],
        "kind": raw["kind"],
        "title": raw["title"],
        "gist": _extract_gist(raw["content"]),
        "updated": _meta_date(raw["meta"].get("updated")),
        "has_frontmatter": raw["has_frontmatter"],
    }


CHILDREN_MODES = ("auto", "content", "gist")


def prepare_summary_update(kg_root: Path, path: str, children: str = "auto") -> Dict[str, Any]:
    """Return parent and direct-child summaries for a strict parent update.

    ``children`` selects the child payload: ``content`` (full bodies),
    ``gist`` (path, title, first line, updated), or ``auto`` — content up to
    ``MAX_DIRECT_CHILDREN`` children, gist above it. The digest is always
    computed over full content, so a gist read still authorizes the write.
    On the audited KB the content payload for one 119-child parent was
    56 KB; gists cut that by 60 percent.
    """
    if children not in CHILDREN_MODES:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            f"children must be one of: {', '.join(CHILDREN_MODES)}",
        )
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
    ceiling = dc.child_ceiling(kg_root, path, MAX_DIRECT_CHILDREN)
    mode = children
    if mode == "auto":
        mode = "content" if child_count <= ceiling else "gist"
    notes: List[Dict[str, Any]] = []
    if children == "auto" and mode == "gist":
        notes.append(
            nt.note(
                "truncated",
                f"{child_count} children returned as gists (ceiling {ceiling})",
                detail={"child_count": child_count, "children_mode": "gist"},
                why=(
                    "full bodies for this many children exceed what one rollup should read; "
                    "the digest still covers full content"
                ),
                next_step=(
                    "pass children='content' for full bodies, or kvault plan to split the parent"
                ),
            )
        )
    result: Dict[str, Any] = {"success": True, "path": path}
    if notes:
        result["notes"] = notes
    result["child_count"] = child_count
    result["children_mode"] = mode
    result["children_digest"] = digest
    result["digest_algorithm"] = SUMMARY_UPDATE_DIGEST_ALGORITHM
    result["max_direct_children"] = ceiling
    result["hierarchy_hint"] = _hierarchy_hint(child_count, ceiling)
    result["parent"] = _summary_update_node(parent_raw)
    result["children"] = [
        _summary_update_node(child) if mode == "content" else _summary_update_gist(child)
        for child in children_raw
    ]
    return result


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


def _reserved_move_problem(source: str, target: str) -> Optional[str]:
    """journal/ is the log and deep_context/ is background material; neither
    is a thing to move on its own, and nothing is moved into the log."""
    if source == "journal" or source.startswith("journal/"):
        return "the journal cannot be moved"
    if source.rsplit("/", 1)[-1] in st.RESERVED_DIRS:
        return "a reserved directory (deep_context/) cannot be moved on its own"
    if target == "journal" or target.startswith("journal/"):
        return "nothing can be moved into journal/"
    if target.rsplit("/", 1)[-1] in st.RESERVED_DIRS:
        return "a target cannot be named journal or deep_context"
    return None


def move_entity(
    kg_root: Path, source_path: str, target_path: str, new_root: bool = False
) -> Dict[str, Any]:
    """Move an entity to a new path.

    The destination chain gets stub summaries for any missing parent (a
    ``created`` note) instead of silent ``mkdir``; a destination under a
    root category that does not exist is refused without *new_root*.
    """
    source_path = _normalize_node_path(source_path)
    target_path = _normalize_node_path(target_path)
    # Node-path validation: a root category (one component) can be moved;
    # only "." is refused.
    is_valid, err_msg = _validate_node_path(source_path)
    if source_path == "." or not is_valid:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            f"Invalid source path: {err_msg or 'the root cannot be moved'}",
        )
    is_valid, err_msg = _validate_node_path(target_path)
    if target_path == "." or not is_valid:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            f"Invalid target path: {err_msg or 'the root cannot be a target'}",
        )
    if target_path == source_path or target_path.startswith(source_path + "/"):
        return error_response(ErrorCode.VALIDATION_ERROR, "Cannot move a node into its own subtree")
    reserved = _reserved_move_problem(source_path, target_path)
    if reserved:
        return error_response(ErrorCode.VALIDATION_ERROR, reserved)
    try:
        source_full = resolve_node_path(kg_root, source_path, reject_symlinks=True)
        target_full = resolve_node_path(kg_root, target_path, reject_symlinks=True)
    except PathSafetyError as exc:
        return error_response(ErrorCode.VALIDATION_ERROR, str(exc))

    if not source_full.exists():
        return error_response(ErrorCode.NOT_FOUND, f"Source doesn't exist: {source_path}")
    if target_full.exists():
        return error_response(ErrorCode.ALREADY_EXISTS, f"Target already exists: {target_path}")
    ignore = st.load_ignore(kg_root)
    root_err, root_notes = _root_guard(kg_root, target_path, new_root, ignore)
    if root_err is not None:
        return root_err
    stub_paths = _missing_ancestor_summaries(kg_root, target_path)

    with KBWriteLock(kg_root) as lock:
        nodes_moved = sum(1 for _ in source_full.rglob("_summary.md"))
        target_full.parent.mkdir(parents=True, exist_ok=True)
        stubs_written = _write_stub_summaries(kg_root, stub_paths, trigger=target_path)
        if target_full.exists():
            # Another process (or a stub) produced the target while we
            # waited for the lock; shutil.move would nest the source inside.
            return error_response(
                ErrorCode.ALREADY_EXISTS, f"Target appeared before the move ran: {target_path}"
            )
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

    notes: List[Dict[str, Any]] = list(root_notes)
    if stubs_written:
        notes.append(_stub_note(stubs_written))
    notes.append(
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
    )
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


def move_entities(
    kg_root: Path,
    moves: Sequence[Dict[str, Any]],
    new_root: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Move several nodes under one lock, one confirmation, one propagation list.

    All moves are validated before any runs (a bad entry means nothing
    moves). If a move fails mid-batch the result is ``partial`` and names
    what moved, what failed, and what was not attempted. ``dry_run`` reports
    the plan without touching the tree.
    """
    if not isinstance(moves, list) or not moves:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "moves must be a non-empty list of {from, to} objects",
        )
    ignore = st.load_ignore(kg_root)
    errors: List[Dict[str, Any]] = []
    normalized: List[Tuple[str, str]] = []
    targets_seen: Set[str] = set()
    sources_seen: Set[str] = set()
    for index, entry in enumerate(moves):
        if not isinstance(entry, dict) or "from" not in entry or "to" not in entry:
            errors.append({"index": index, "error": "each move needs 'from' and 'to'"})
            continue
        src = _normalize_node_path(str(entry["from"]))
        tgt = _normalize_node_path(str(entry["to"]))
        problem: Optional[str] = None
        # Node-path validation, not entity-path: a root category (one
        # component) is a legitimate thing to move — consolidating 23 roots
        # into hubs is the case this batch exists for. Only "." is refused.
        ok_src, msg_src = _validate_node_path(src)
        ok_tgt, msg_tgt = _validate_node_path(tgt)
        if src == "." or not ok_src:
            problem = f"invalid source: {msg_src or 'the root cannot be moved'}"
        elif tgt == "." or not ok_tgt:
            problem = f"invalid target: {msg_tgt or 'the root cannot be a target'}"
        elif tgt == src or tgt.startswith(src + "/"):
            problem = "cannot move a node into its own subtree"
        elif not (kg_root / src).exists():
            problem = f"source doesn't exist: {src}"
        elif (kg_root / tgt).exists():
            problem = f"target already exists: {tgt}"
        elif tgt in targets_seen:
            problem = f"target used twice in this batch: {tgt}"
        elif src in sources_seen:
            problem = f"source listed twice in this batch: {src}"
        elif any(
            src.startswith(other + "/") or other.startswith(src + "/") for other in sources_seen
        ):
            problem = "source overlaps another move's source in this batch"
        elif any(
            tgt.startswith(other + "/") or other.startswith(tgt + "/") for other in targets_seen
        ):
            # A target that is an ancestor of another target would be stubbed
            # first, and shutil.move would then nest the source inside it.
            problem = "target overlaps another move's target in this batch"
        elif any(tgt.startswith(other + "/") for other in sources_seen) or any(
            other.startswith(src + "/") for other in targets_seen
        ):
            # A target under another move's source: once that source moves,
            # mkdir(parents=True) silently recreates it as a ghost root.
            problem = "target lies inside another move's source in this batch"
        elif _reserved_move_problem(src, tgt):
            problem = _reserved_move_problem(src, tgt) or ""
        if problem is None:
            try:
                resolve_node_path(kg_root, src, reject_symlinks=True)
                resolve_node_path(kg_root, tgt, reject_symlinks=True)
            except PathSafetyError as exc:
                problem = str(exc)
        if problem:
            errors.append({"index": index, "from": src, "to": tgt, "error": problem})
            continue
        targets_seen.add(tgt)
        sources_seen.add(src)
        normalized.append((src, tgt))
    if errors:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            f"{len(errors)} of {len(moves)} moves are invalid; nothing was moved",
            details={"errors": errors},
        )

    notes: List[Dict[str, Any]] = []
    stub_paths: List[str] = []
    for _, tgt in normalized:
        err, root_notes = _root_guard(kg_root, tgt, new_root, ignore)
        if err is not None:
            return err
        for note in root_notes:
            if note["detail"]["root"] not in {n["detail"]["root"] for n in notes}:
                notes.append(note)
        for missing in _missing_ancestor_summaries(kg_root, tgt):
            if missing not in stub_paths:
                stub_paths.append(missing)

    if new_root:
        # --new-root on a batch means consolidation, never addition: the
        # root count after the batch may not exceed the count before. A
        # deliberate new root is `kvault write <root>/... --new-root`.
        roots_before = {d.name for d in st.child_dirs(kg_root, kg_root, ignore)}
        roots_after = set(roots_before)
        for src, tgt in normalized:
            if "/" not in src:
                roots_after.discard(src)
        for src, tgt in normalized:
            roots_after.add(tgt.split("/")[0])
        if len(roots_after) > len(roots_before):
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                f"this batch would increase the root count from {len(roots_before)} to "
                f"{len(roots_after)}; --new-root on a batch is for consolidation",
                details={
                    "reason": "new_root",
                    "roots_before": sorted(roots_before),
                    "roots_after": sorted(roots_after),
                },
                hint="Create a root deliberately with kvault write <root>/<node> --create --new-root, then move into it",
            )

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "did": f"would move {len(normalized)} nodes (dry run)",
            "notes": notes,
            "moves": [{"from": s_, "to": t_} for s_, t_ in normalized],
            "stubs": stub_paths,
            "count": len(normalized),
        }

    moved: List[Dict[str, Any]] = []
    failed: Optional[Dict[str, Any]] = None
    with KBWriteLock(kg_root) as lock:
        stubs_written = _write_stub_summaries(
            kg_root, stub_paths, trigger=f"move --batch ({len(normalized)} moves)"
        )
        for src, tgt in normalized:
            source_full = kg_root / src
            target_full = kg_root / tgt
            try:
                if target_full.exists():
                    # Never let shutil.move nest the source inside an existing
                    # directory; that reports success with the subtree at the
                    # wrong path.
                    raise OSError(f"target appeared before this move ran: {tgt}")
                nodes_moved = sum(1 for _ in source_full.rglob("_summary.md"))
                target_full.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source_full), str(target_full))
            except OSError as exc:
                failed = {"from": src, "to": tgt, "error": str(exc)}
                break
            moved.append({"from": src, "to": tgt, "nodes_moved": nodes_moved})

    done = {(m["from"], m["to"]) for m in moved}
    not_attempted = [
        {"from": s_, "to": t_}
        for s_, t_ in normalized
        if (s_, t_) not in done and (failed is None or (s_, t_) != (failed["from"], failed["to"]))
    ]

    combined: List[Dict[str, Any]] = []
    seen_paths: Set[str] = set()
    for m in moved:
        for target in _propagation_targets(kg_root, m["from"]) + _propagation_targets(
            kg_root, m["to"]
        ):
            if target["path"] not in seen_paths:
                seen_paths.add(target["path"])
                combined.append(target)

    if stubs_written:
        notes.append(_stub_note(stubs_written))
    if failed is not None:
        notes.append(
            nt.note(
                "partial",
                f"{len(moved)} of {len(normalized)} moves done, then {failed['from']} failed: "
                f"{failed['error']}; {len(not_attempted)} not attempted",
                detail={"moved": moved, "failed": failed, "not_attempted": not_attempted},
                why="a move failed mid-batch; earlier moves are on disk and cannot be undone here",
                next_step="fix the cause, then re-run the batch with the remaining moves",
            )
        )
    if combined:
        notes.append(
            nt.note(
                "propagate",
                f"{len(combined)} ancestor summaries are stale across {len(moved)} moves",
                level=nt.NORMAL,
                detail={"paths": [t["path"] for t in combined]},
                why=(
                    "source chains still describe subtrees that left; "
                    "target chains do not describe them yet"
                ),
                next_step="kvault update-summaries",
            )
        )
    notes.extend(_lock_notes(lock))

    result: Dict[str, Any] = {
        "success": True,
        "did": f"moved {len(moved)} of {len(normalized)} nodes",
        "notes": notes,
    }
    if failed is not None:
        result["partial"] = True
        result["failed"] = failed
        result["not_attempted"] = not_attempted
    result["moved"] = moved
    result["count"] = len(moved)
    result["propagation_required"] = len(combined) > 0
    result["ancestor_paths"] = [t["path"] for t in combined]
    result["ancestors"] = combined
    return result


def mark_node(
    kg_root: Path,
    path: str,
    distinct_from: Optional[List[str]] = None,
    max_children: Optional[int] = None,
    series_ok: Optional[bool] = None,
    clear: bool = False,
) -> Dict[str, Any]:
    """Record a structure decision in a node's frontmatter (see core.decisions).

    Goes through ``write_node`` so it is validated, no-op aware, and logged.
    """
    path = _normalize_node_path(path)
    if not (distinct_from or max_children is not None or series_ok is not None or clear):
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "nothing to record: pass --distinct-from, --max-children, --series-ok, or --clear",
        )
    raw = _read_node_raw(kg_root, path)
    if raw is None:
        return error_response(ErrorCode.NOT_FOUND, f"Node doesn't exist: {path}")
    meta = dc.merge_decisions(
        raw["meta"] or {},
        path,
        distinct_from=distinct_from,
        max_children=max_children,
        series_ok=series_ok,
        clear=clear,
    )
    drops = [key for key in dc.DECISION_KEYS if key not in meta and key in (raw["meta"] or {})]
    result = write_node(
        kg_root, path, raw["content"], meta=meta, create=False, drop_meta_keys=drops or None
    )
    if not result.get("success"):
        return result
    decisions = dc.read_decisions(kg_root, path)
    parts: List[str] = []
    if clear:
        parts.append("cleared")
    if distinct_from:
        parts.append(
            "distinct_from += " + ", ".join(decisions["distinct_from"][-len(distinct_from) :])
        )
    if max_children is not None:
        parts.append(f"max_children={decisions['max_children']}")
    if series_ok is not None:
        parts.append(f"series_ok={str(decisions['series_ok']).lower()}")
    result["did"] = f"marked {path}: " + "; ".join(parts)
    result["decisions"] = decisions
    result.pop("ancestors", None)  # a decision does not change what the parent should say
    result["ancestor_paths"] = []
    result["propagation_required"] = False
    return result


# ---------------------------------------------------------------------------
# Ancestors
# ---------------------------------------------------------------------------


def get_ancestors(kg_root: Path, path: str, include_content: bool = True) -> Dict[str, Any]:
    """Get all ancestor summaries (root included) for propagation.

    ``include_content=False`` returns ``{path, has_meta}`` per ancestor and
    is the bounded form (a mature KB's full chain exceeds 100 KB); the
    ``ancestor_paths`` list is always present.
    """
    path = normalize_path(path)
    storage = SimpleStorage(kg_root)
    ancestors = storage.get_ancestors(path)

    propagation_targets = []
    for ancestor in list(ancestors) + ["."]:
        summary_data = read_summary(kg_root, ancestor)
        if not summary_data:
            continue
        target: Dict[str, Any] = {"path": ancestor}
        if include_content:
            target["current_content"] = summary_data.get("content", "")
        target["has_meta"] = bool(summary_data.get("meta"))
        propagation_targets.append(target)

    return {
        "success": True,
        "ancestor_paths": [target["path"] for target in propagation_targets],
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

    # A directory with no summary is invisible to tree, search, and check —
    # the split-brain mechanism from the 2026-09 audit. validate said "valid"
    # on a KB with 19 of them; it does not any more. Tooling directories
    # belong in .kvaultignore.
    for ghost in st.ghost_dirs(kg_root, st.load_ignore(kg_root)):
        issues.append(
            {
                "type": "ghost_directory",
                "severity": "warning",
                "path": ghost,
                "message": "Directory has no _summary.md and is invisible to tree, search, and check",
                "fix": (
                    f"Write a summary with kvault write {ghost} --create, "
                    f"or list it in {st.IGNORE_FILE}"
                ),
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
