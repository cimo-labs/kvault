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
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from kvault.core.frontmatter import build_frontmatter, parse_frontmatter
from kvault.core.storage import (
    SimpleStorage,
    count_entities,
    list_entity_records,
    scan_entities,
)
from kvault.core.validation import (
    ErrorCode,
    error_response,
    format_journal_entry,
    get_journal_path,
    normalize_path,
    validate_entity_path,
)

_ALLOWED_ROOTS_ENV = "KVAULT_ALLOWED_ROOTS"
_NODE_COMPONENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
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


def _normalize_node_path(path: str) -> str:
    path = normalize_path(path or ".")
    return "." if path in ("", ".") else path


def _validate_node_path(path: str) -> Tuple[bool, Optional[str]]:
    if path == ".":
        return True, None
    parts = path.split("/")
    for part in parts:
        if not _NODE_COMPONENT_RE.match(part):
            return (
                False,
                f"Invalid path component: '{part}' (must be lowercase alphanumeric with underscores)",
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
        child.is_dir() and not child.name.startswith(".") and (child / "_summary.md").exists()
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
        if not child.is_dir() or child.name.startswith("."):
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


def _direct_child_raw_nodes(kg_root: Path, path: str) -> List[Dict[str, Any]]:
    children: List[Dict[str, Any]] = []
    for child_path in _child_node_paths(kg_root, path):
        raw = _read_node_raw(kg_root, child_path)
        if raw is not None:
            children.append(raw)
    return children


def _children_digest(parent_path: str, children: List[Dict[str, Any]]) -> str:
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
) -> Dict[str, Any]:
    """Write any node summary with YAML frontmatter."""
    path = _normalize_node_path(path)
    is_valid, err_msg = _validate_node_path(path)
    if not is_valid:
        return error_response(ErrorCode.VALIDATION_ERROR, err_msg or "Invalid path")
    if not validate_within_root(kg_root, path):
        return error_response(ErrorCode.VALIDATION_ERROR, "Path escapes KB root")

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

    # Auto-set 'name' from first alias
    if "name" not in meta and meta.get("aliases"):
        for alias in meta["aliases"]:
            if isinstance(alias, str) and "@" not in alias and not alias.startswith("+"):
                meta["name"] = alias
                break
        if "name" not in meta and meta["aliases"]:
            first = meta["aliases"][0]
            if isinstance(first, str):
                meta["name"] = first

    # Date fields — a no-op rewrite (same body, same meta) keeps existing
    # created/updated so bulk re-writes don't flatten the recency signal.
    today = datetime.now().strftime("%Y-%m-%d")
    if create:
        meta["created"] = today
        meta["updated"] = today
    else:
        existing = _read_node_raw(kg_root, path)
        if existing is not None and _is_noop_node_write(existing, content, meta):
            for key in ("created", "updated"):
                if key in existing["meta"]:
                    meta[key] = existing["meta"][key]
                else:
                    meta.pop(key, None)
        else:
            meta["updated"] = today

    # Write
    frontmatter = build_frontmatter(meta)
    full_content = frontmatter + content
    full_path.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(full_content)

    # Remove legacy _meta.json
    meta_json_path = full_path / "_meta.json"
    if meta_json_path.exists():
        meta_json_path.unlink()

    result: Dict[str, Any] = {
        "success": True,
        "path": path,
        "created": create,
    }
    if autofilled_source or autofilled_aliases:
        result["meta_autofilled"] = {
            "source": autofilled_source,
            "aliases": autofilled_aliases,
        }

    # Fetch ancestor summaries for propagation.
    propagation_targets = _propagation_targets(kg_root, path)
    result["ancestors"] = propagation_targets
    result["propagation_required"] = len(propagation_targets) > 0

    # Auto-journal if reasoning provided
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
        result["journal_logged"] = journal_result.get("success", False)
        result["journal_path"] = journal_result.get("journal_path")
    else:
        result["journal_logged"] = False

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
    dir_path = kg_root if path == "." else kg_root / path
    summary_path = _summary_path_for_node(kg_root, path)
    dir_path.mkdir(parents=True, exist_ok=True)
    existing = _read_node_raw(kg_root, path)
    preserved_meta = existing.get("meta", {}) if existing and meta is None else {}
    final_meta = meta if meta is not None else preserved_meta
    if final_meta:
        full_content = build_frontmatter(final_meta) + content
    else:
        full_content = content
    summary_path.write_text(full_content)
    return {"success": True, "path": path}


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

    children_raw = _direct_child_raw_nodes(kg_root, path)
    child_count = len(children_raw)
    digest = _children_digest(path, children_raw)
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

    return {
        "success": True,
        "path": prepared["path"],
        "child_count": prepared["child_count"],
        "children_digest": expected_digest,
        "digest_algorithm": SUMMARY_UPDATE_DIGEST_ALGORITHM,
        "max_direct_children": MAX_DIRECT_CHILDREN,
        "hierarchy_hint": prepared["hierarchy_hint"],
    }


def update_summaries(
    kg_root: Path,
    updates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Batch-update multiple summary files."""
    updated: List[str] = []
    errors: List[Dict[str, Any]] = []
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
            else:
                errors.append({"path": p, "error": r.get("error", "Unknown error")})
        except Exception as e:
            errors.append({"path": p, "error": str(e)})
    result: Dict[str, Any] = {
        "success": len(updated) > 0 or len(updates) == 0,
        "updated": updated,
        "count": len(updated),
    }
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
    )


def delete_entity(kg_root: Path, path: str) -> Dict[str, Any]:
    """Delete an entity directory."""
    path = normalize_path(path)
    if not validate_within_root(kg_root, path):
        return error_response(ErrorCode.VALIDATION_ERROR, "Path escapes KB root")
    full_path = kg_root / path
    if not full_path.exists():
        return error_response(ErrorCode.NOT_FOUND, f"Entity doesn't exist: {path}")
    shutil.rmtree(full_path)
    return {"success": True, "path": path, "deleted": True}


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
    if not validate_within_root(kg_root, source_path):
        return error_response(ErrorCode.VALIDATION_ERROR, "Source path escapes KB root")
    if not validate_within_root(kg_root, target_path):
        return error_response(ErrorCode.VALIDATION_ERROR, "Target path escapes KB root")

    source_full = kg_root / source_path
    target_full = kg_root / target_path
    if not source_full.exists():
        return error_response(ErrorCode.NOT_FOUND, f"Source doesn't exist: {source_path}")
    if target_full.exists():
        return error_response(ErrorCode.ALREADY_EXISTS, f"Target already exists: {target_path}")

    target_full.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_full), str(target_full))
    return {"success": True, "source": source_path, "target": target_path}


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
    if date:
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            pass

    journal_rel_path = get_journal_path(dt)
    journal_full_path = kg_root / journal_rel_path
    journal_full_path.parent.mkdir(parents=True, exist_ok=True)

    entry = format_journal_entry(actions, source, dt)
    if journal_full_path.exists():
        existing = journal_full_path.read_text()
        entry = existing.rstrip() + "\n\n" + entry
    else:
        header = f"# Journal - {dt.strftime('%B %Y')}\n\n"
        entry = header + entry

    journal_full_path.write_text(entry)
    return {
        "success": True,
        "journal_path": journal_rel_path,
        "actions_logged": len(actions),
    }


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
