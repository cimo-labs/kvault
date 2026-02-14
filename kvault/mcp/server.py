"""kvault MCP Server - Model Context Protocol server for knowledge graph operations.

Provides 13 tools for Claude Code to interact with the knowledge graph:

Init (1):
- kvault_init: Initialize KB, return hierarchy + root summary

Entity Tools (5):
- kvault_read_entity: Read entity with YAML frontmatter + parent summary
- kvault_write_entity: Write entity (returns ancestors for propagation, auto-journals if reasoning given)
- kvault_list_entities: List entities in a category
- kvault_delete_entity: Delete an entity
- kvault_move_entity: Move an entity to new path

Summary Tools (4):
- kvault_read_summary: Read a summary file
- kvault_write_summary: Write a single summary file
- kvault_update_summaries: Batch-update multiple summaries in one call
- kvault_get_parent_summaries: Get ancestor summaries

Propagation (1):
- kvault_propagate_all: Get all ancestors for propagation

Workflow (1):
- kvault_write_journal: Write a journal entry

Validation (1):
- kvault_validate_kb: Check KB integrity

2-call write workflow:
  1. kvault_write_entity(..., reasoning="...") → ancestors + auto-journal
  2. kvault_update_summaries(updates=[...]) → batch propagation

Agents use their own Grep/Glob/Read tools for searching.
kvault_read_entity includes the parent summary for sibling context.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

from kvault.core.frontmatter import parse_frontmatter, build_frontmatter
from kvault.core.storage import SimpleStorage, scan_entities, count_entities, list_entity_records
from kvault.core.observability import ObservabilityLogger
from kvault.mcp.state import get_session_manager, WorkflowStep
from kvault.mcp.validation import (
    normalize_path,
    validate_entity_path,
    get_journal_path,
    format_journal_entry,
    ErrorCode,
    error_response,
)

# Global instances (initialized when server starts)
_kg_root: Optional[Path] = None
_storage: Optional[SimpleStorage] = None
_logger: Optional[ObservabilityLogger] = None

_NOT_INIT_MSG = "kvault MCP server not initialized. Call kvault_init first."


def _ensure_initialized() -> None:
    """Ensure global instances are initialized."""
    if _kg_root is None:
        raise RuntimeError(_NOT_INIT_MSG)


def _root() -> Path:
    """Return KB root, raising if not initialized."""
    if _kg_root is None:
        raise RuntimeError(_NOT_INIT_MSG)
    return _kg_root


def _storage_instance() -> SimpleStorage:
    """Return storage, raising if not initialized."""
    if _storage is None:
        raise RuntimeError(_NOT_INIT_MSG)
    return _storage


def _init_infrastructure(kg_root: str) -> Dict[str, Any]:
    """Initialize kvault infrastructure for a given root."""
    global _kg_root, _storage, _logger

    _kg_root = Path(kg_root).resolve()

    kvault_dir = _kg_root / ".kvault"
    kvault_dir.mkdir(parents=True, exist_ok=True)

    _storage = SimpleStorage(_kg_root)
    _logger = ObservabilityLogger(kvault_dir / "logs.db")

    # Load root summary
    root_summary_path = _kg_root / "_summary.md"
    root_summary = ""
    if root_summary_path.exists():
        root_summary = root_summary_path.read_text()

    # Build hierarchy tree
    hierarchy = _build_hierarchy_tree(_kg_root)

    return {
        "kg_root": str(_kg_root),
        "root_summary": root_summary,
        "hierarchy": hierarchy,
        "entity_count": count_entities(_kg_root),
    }


def _build_hierarchy_tree(root: Path, max_depth: int = 3) -> str:
    """Build a tree representation of the KB hierarchy."""
    lines = []

    def _walk(path: Path, prefix: str = "", depth: int = 0):
        if depth > max_depth:
            return

        if path.name.startswith("."):
            return

        try:
            subdirs = sorted(
                [p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")]
            )
        except PermissionError:
            return

        for i, subdir in enumerate(subdirs):
            is_last = i == len(subdirs) - 1
            connector = "└── " if is_last else "├── "
            extension = "    " if is_last else "│   "

            has_summary = (subdir / "_summary.md").exists()
            marker = " ✓" if has_summary else ""

            lines.append(f"{prefix}{connector}{subdir.name}/{marker}")
            _walk(subdir, prefix + extension, depth + 1)

    has_root_summary = (root / "_summary.md").exists()
    lines.append(f"./{' ✓' if has_root_summary else ''}")
    _walk(root, "", 0)

    return "\n".join(lines)


def _validate_within_root(path: str) -> bool:
    """Validate that a resolved path stays within the KB root."""
    kg_root = _root()
    resolved = (kg_root / path).resolve()
    return resolved == kg_root or str(resolved).startswith(str(kg_root.resolve()) + "/")


def _read_entity_with_frontmatter(entity_path: str) -> Optional[Dict[str, Any]]:
    """Read entity, preferring YAML frontmatter over legacy _meta.json.

    Returns dict with 'meta' and 'content' keys.
    """
    kg_root = _root()

    if not _validate_within_root(entity_path):
        return None

    full_path = kg_root / entity_path
    summary_path = full_path / "_summary.md"

    if not summary_path.exists():
        return None

    content = summary_path.read_text()
    meta, body = parse_frontmatter(content)

    # If no frontmatter, check for legacy _meta.json
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


def _write_entity_with_frontmatter(
    entity_path: str,
    meta: Dict[str, Any],
    content: str,
    create: bool = False,
    auto_rebuild: bool = False,
    session_id: Optional[str] = None,
    reasoning: Optional[str] = None,
    journal_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Write entity with YAML frontmatter.

    Args:
        entity_path: Path like "people/contacts/john_doe"
        meta: Frontmatter metadata (must include: source, aliases)
        content: Markdown content (without frontmatter)
        create: If True, fail if entity exists. If False, update.
        auto_rebuild: If True, rebuild index after writing.
        session_id: Optional session ID for workflow tracking.
        reasoning: Optional reasoning for journal auto-logging.
        journal_source: Optional source for journal (defaults to meta['source']).

    Returns:
        Result dict with path, success status, ancestors, and journal info
    """
    kg_root = _root()
    storage = _storage_instance()

    entity_path = normalize_path(entity_path)
    is_valid, err_msg = validate_entity_path(entity_path)
    if not is_valid:
        return error_response(ErrorCode.VALIDATION_ERROR, err_msg or "Invalid path")

    full_path = kg_root / entity_path
    summary_path = full_path / "_summary.md"

    # Check existence
    if create and full_path.exists():
        return error_response(
            ErrorCode.ALREADY_EXISTS,
            f"Entity already exists: {entity_path}",
            hint="Use create=false to update existing entity",
        )
    if not create and not full_path.exists():
        return error_response(
            ErrorCode.NOT_FOUND,
            f"Entity doesn't exist: {entity_path}",
            hint="Use create=true to create new entity",
        )

    # Validate required frontmatter BEFORE applying defaults
    # Required: source, aliases (created/updated are set automatically)
    if "source" not in meta:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "Missing required frontmatter field: source",
            details={"missing_fields": ["source"]},
            hint="Provide a source identifier (e.g., 'manual', 'imessage:thread_id')",
        )
    if "aliases" not in meta:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            "Missing required frontmatter field: aliases",
            details={"missing_fields": ["aliases"]},
            hint="Provide aliases as a list (can be empty: [])",
        )

    # Auto-set 'name' from first alias if not provided
    # This ensures search results show "Alice Smith" not "alice_smith"
    if "name" not in meta and meta.get("aliases"):
        # Use first non-email, non-phone alias as display name
        for alias in meta["aliases"]:
            if isinstance(alias, str) and "@" not in alias and not alias.startswith("+"):
                meta["name"] = alias
                break
        # Fall back to first alias if all are emails/phones
        if "name" not in meta and meta["aliases"]:
            first = meta["aliases"][0]
            if isinstance(first, str):
                meta["name"] = first

    # Set/update date fields (these are always automatic)
    today = datetime.now().strftime("%Y-%m-%d")
    if create:
        meta["created"] = today
    meta["updated"] = today

    # Build full content with frontmatter
    frontmatter = build_frontmatter(meta)
    full_content = frontmatter + content

    # Create directory and write
    full_path.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(full_content)

    # Remove legacy _meta.json if it exists
    meta_json_path = full_path / "_meta.json"
    if meta_json_path.exists():
        meta_json_path.unlink()

    result = {
        "success": True,
        "path": entity_path,
        "created": create,
    }

    # Record in session state
    if session_id:
        session = get_session_manager().get_session(session_id)
        if session:
            if create:
                session.record_execution(created=[entity_path])
            else:
                session.record_execution(updated=[entity_path])

    # Fetch ancestor summaries for propagation (always included)
    ancestor_paths = storage.get_ancestors(entity_path)
    ancestor_paths.append(".")  # include root
    propagation_targets = []
    for ancestor in ancestor_paths:
        summary_data = handle_kvault_read_summary(ancestor)
        if summary_data:
            propagation_targets.append(
                {
                    "path": ancestor,
                    "current_content": summary_data.get("content", ""),
                    "has_meta": bool(summary_data.get("meta")),
                }
            )
    result["ancestors"] = propagation_targets

    # Auto-log journal if reasoning provided
    if reasoning:
        action_type = "create" if create else "update"
        source = journal_source or meta.get("source", "unknown")
        journal_result = handle_kvault_write_journal(
            actions=[
                {
                    "action_type": action_type,
                    "path": entity_path,
                    "reasoning": reasoning,
                }
            ],
            source=source,
            session_id=session_id,
        )
        result["journal_logged"] = journal_result.get("success", False)
        result["journal_path"] = journal_result.get("journal_path")
    else:
        result["journal_logged"] = False

    return result


# ============================================================================
# Workflow Warnings
# ============================================================================


def _check_workflow_order(session_id: Optional[str], expected_step: WorkflowStep) -> Optional[str]:
    """Check if current workflow step matches expected step.

    Returns warning message if workflow is out of order, None if OK.
    """
    if not session_id:
        return None

    session = get_session_manager().get_session(session_id)
    if not session:
        return None

    # Map expected step to what the current step should be before this action
    step_prerequisites = {
        WorkflowStep.RESEARCH: [WorkflowStep.INIT, WorkflowStep.RESEARCH],
        WorkflowStep.DECIDE: [WorkflowStep.RESEARCH, WorkflowStep.DECIDE],
        WorkflowStep.EXECUTE: [WorkflowStep.DECIDE, WorkflowStep.EXECUTE],
        WorkflowStep.PROPAGATE: [WorkflowStep.EXECUTE, WorkflowStep.PROPAGATE],
        WorkflowStep.LOG: [WorkflowStep.PROPAGATE, WorkflowStep.LOG],
        WorkflowStep.REBUILD: [WorkflowStep.LOG, WorkflowStep.REBUILD],
    }

    valid_current = step_prerequisites.get(expected_step, [])
    if session.current_step not in valid_current:
        return (
            f"Workflow warning: Expected to be at {'/'.join(s.value for s in valid_current)}, "
            f"but currently at '{session.current_step.value}'. "
            f"Consider following the 6-step workflow (research → decide → execute → propagate → log → rebuild)."
        )
    return None


def _add_workflow_warning(
    result: Dict[str, Any], session_id: Optional[str], expected_step: WorkflowStep
) -> Dict[str, Any]:
    """Add workflow warning to result if steps are out of order."""
    warning = _check_workflow_order(session_id, expected_step)
    if warning:
        result["workflow_warning"] = warning
    return result


# ============================================================================
# Tool Handlers
# ============================================================================


def handle_kvault_init(kg_root: str) -> Dict[str, Any]:
    """Initialize kvault and return context.

    This should be called first to set up the knowledge graph.
    Returns hierarchy tree, root summary, and entity count.
    """
    result = _init_infrastructure(kg_root)

    # Create session
    session_mgr = get_session_manager()
    session = session_mgr.create_session(kg_root)
    session.transition(WorkflowStep.RESEARCH)

    result["session_id"] = session.session_id
    result["current_step"] = session.current_step.value

    return result


def handle_kvault_read_entity(path: str) -> Optional[Dict[str, Any]]:
    """Read entity with YAML frontmatter and parent summary for sibling context.

    Args:
        path: Entity path (e.g., "people/contacts/john_doe")

    Returns:
        Entity data with meta, content, parent_summary, and parent_path, or None if not found
    """
    entity_data = _read_entity_with_frontmatter(path)
    if not entity_data:
        return None

    # Include parent summary for sibling context
    kg_root = _root()
    storage = _storage_instance()
    ancestors = storage.get_ancestors(path)
    if ancestors:
        parent_summary_path = kg_root / ancestors[0] / "_summary.md"
        if parent_summary_path.exists():
            entity_data["parent_summary"] = parent_summary_path.read_text()
            entity_data["parent_path"] = ancestors[0]

    return entity_data


def handle_kvault_write_entity(
    path: str,
    meta: Dict[str, Any],
    content: str,
    create: bool = False,
    auto_rebuild: bool = False,
    session_id: Optional[str] = None,
    reasoning: Optional[str] = None,
    journal_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Write entity with YAML frontmatter.

    Args:
        path: Entity path
        meta: Frontmatter metadata
        content: Markdown content
        create: If True, create new entity. If False, update existing.
        auto_rebuild: If True, rebuild index after writing.
        session_id: Optional session ID for workflow tracking.
        reasoning: Optional reasoning — triggers auto-journal logging.
        journal_source: Optional source for journal (defaults to meta['source']).

    Returns:
        Result with success status, ancestors for propagation, and journal info
    """
    result = _write_entity_with_frontmatter(
        path, meta, content, create, auto_rebuild, session_id, reasoning, journal_source
    )
    return _add_workflow_warning(result, session_id, WorkflowStep.EXECUTE)


def handle_kvault_list_entities(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """List entities, optionally filtered by category.

    Args:
        category: Optional category filter (e.g., "people")

    Returns:
        List of entity summaries
    """
    entries = list_entity_records(_root(), category=category)
    return [
        {
            "path": e.path,
            "name": e.name,
            "category": e.category,
            "last_updated": e.last_updated,
        }
        for e in entries
    ]


def handle_kvault_delete_entity(
    path: str,
    auto_rebuild: bool = False,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete an entity.

    Args:
        path: Entity path to delete
        auto_rebuild: If True, rebuild index after deleting.
        session_id: Optional session ID for workflow tracking.

    Returns:
        Result with success status
    """
    kg_root = _root()

    path = normalize_path(path)

    if not _validate_within_root(path):
        return error_response(ErrorCode.VALIDATION_ERROR, "Path escapes KB root")

    full_path = kg_root / path

    if not full_path.exists():
        return error_response(ErrorCode.NOT_FOUND, f"Entity doesn't exist: {path}")

    # Remove from filesystem
    shutil.rmtree(full_path)

    result = {
        "success": True,
        "path": path,
        "deleted": True,
    }

    # Record in session state
    if session_id:
        session = get_session_manager().get_session(session_id)
        if session:
            session.record_execution(deleted=[path])

    return result


def handle_kvault_move_entity(
    source_path: str,
    target_path: str,
    auto_rebuild: bool = False,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Move an entity to a new path.

    Args:
        source_path: Current entity path
        target_path: New entity path
        auto_rebuild: If True, rebuild index after moving.
        session_id: Optional session ID for workflow tracking.

    Returns:
        Result with success status
    """
    kg_root = _root()

    source_path = normalize_path(source_path)
    target_path = normalize_path(target_path)

    # Validate paths
    is_valid, err_msg = validate_entity_path(target_path)
    if not is_valid:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            f"Invalid target path: {err_msg}",
        )

    source_full = kg_root / source_path
    target_full = kg_root / target_path

    if not source_full.exists():
        return error_response(ErrorCode.NOT_FOUND, f"Source doesn't exist: {source_path}")

    if target_full.exists():
        return error_response(
            ErrorCode.ALREADY_EXISTS,
            f"Target already exists: {target_path}",
        )

    # Create parent directories
    target_full.parent.mkdir(parents=True, exist_ok=True)

    # Move directory
    shutil.move(str(source_full), str(target_full))

    result = {
        "success": True,
        "source": source_path,
        "target": target_path,
    }

    # Record in session state
    if session_id:
        session = get_session_manager().get_session(session_id)
        if session:
            session.record_execution(moved=[{"source": source_path, "target": target_path}])

    return result


def handle_kvault_read_summary(path: str) -> Optional[Dict[str, Any]]:
    """Read a summary file.

    Args:
        path: Path to directory containing _summary.md

    Returns:
        Summary content with optional frontmatter
    """
    kg_root = _root()

    path = normalize_path(path)

    if not _validate_within_root(path):
        return None

    summary_path = kg_root / path / "_summary.md"

    if not summary_path.exists():
        # Try as direct file
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


def handle_kvault_write_summary(
    path: str,
    content: str,
    meta: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a summary file.

    Args:
        path: Path to directory for _summary.md
        content: Markdown content
        meta: Optional frontmatter
        session_id: Optional session ID for workflow tracking.

    Returns:
        Result with success status
    """
    path = normalize_path(path)
    dir_path = _root() / path
    summary_path = dir_path / "_summary.md"

    # Create directory if needed
    dir_path.mkdir(parents=True, exist_ok=True)

    # Build content
    if meta:
        frontmatter = build_frontmatter(meta)
        full_content = frontmatter + content
    else:
        full_content = content

    summary_path.write_text(full_content)

    # Record in session state
    if session_id:
        session = get_session_manager().get_session(session_id)
        if session:
            session.record_propagation([path])

    return {
        "success": True,
        "path": path,
    }


def handle_kvault_update_summaries(
    updates: List[Dict[str, Any]],
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Batch-update multiple summary files in one call.

    Args:
        updates: List of dicts with 'path', 'content', and optional 'meta'.
        session_id: Optional session ID for workflow tracking.

    Returns:
        Result with updated paths and count. Partial failure: successful
        writes proceed even if one fails.
    """
    _root()  # ensure initialized

    updated = []
    errors = []
    for item in updates:
        path = item.get("path")
        content = item.get("content")
        meta = item.get("meta")
        if not path or content is None:
            errors.append({"path": path or "<missing>", "error": "Missing path or content"})
            continue
        try:
            result = handle_kvault_write_summary(
                path=path,
                content=content,
                meta=meta,
                session_id=session_id,
            )
            if result.get("success"):
                updated.append(path)
            else:
                errors.append({"path": path, "error": result.get("error", "Unknown error")})
        except Exception as e:
            errors.append({"path": path, "error": str(e)})

    result = {
        "success": len(updated) > 0 or (len(updates) == 0),
        "updated": updated,
        "count": len(updated),
    }
    if errors:
        result["errors"] = errors
    return result


def handle_kvault_get_parent_summaries(path: str) -> List[Dict[str, Any]]:
    """Get ancestor summaries for a path.

    Args:
        path: Entity or category path

    Returns:
        List of ancestor summaries (closest first)
    """
    path = normalize_path(path)
    ancestors = _storage_instance().get_ancestors(path)

    results = []
    for ancestor in ancestors:
        summary_data = handle_kvault_read_summary(ancestor)
        if summary_data:
            results.append(summary_data)

    # Also include root summary
    root_summary = handle_kvault_read_summary(".")
    if root_summary:
        results.append(root_summary)

    return results


def handle_kvault_write_journal(
    actions: List[Dict[str, Any]],
    source: str,
    date: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a journal entry for actions taken.

    Args:
        actions: List of action dicts with action_type, path, etc.
        source: Source identifier
        date: Optional date (defaults to today)
        session_id: Optional session ID for workflow tracking.

    Returns:
        Result with journal path
    """
    kg_root = _root()

    # Parse date
    dt = datetime.now()
    if date:
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            pass

    # Get journal path
    journal_rel_path = get_journal_path(dt)
    journal_full_path = kg_root / journal_rel_path

    # Create directory
    journal_full_path.parent.mkdir(parents=True, exist_ok=True)

    # Format entry
    entry = format_journal_entry(actions, source, dt)

    # Append to journal
    if journal_full_path.exists():
        existing = journal_full_path.read_text()
        entry = existing.rstrip() + "\n\n" + entry
    else:
        # Add header for new file
        header = f"# Journal - {dt.strftime('%B %Y')}\n\n"
        entry = header + entry

    journal_full_path.write_text(entry)

    # Record in session state
    if session_id:
        session = get_session_manager().get_session(session_id)
        if session:
            session.record_journal(journal_rel_path)

    return {
        "success": True,
        "journal_path": journal_rel_path,
        "actions_logged": len(actions),
    }


def handle_kvault_propagate_all(
    path: str,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Get all ancestor summaries that need updating for propagation.

    This tool identifies all ancestors from the given path up to root
    and returns their current content. Claude should then update each
    summary to reflect the new semantic landscape.

    Args:
        path: Entity or category path
        session_id: Optional session ID for workflow tracking.

    Returns:
        List of ancestors with their current content
    """
    path = normalize_path(path)
    ancestors = _storage_instance().get_ancestors(path)

    propagation_targets = []
    for ancestor in ancestors:
        summary_data = handle_kvault_read_summary(ancestor)
        if summary_data:
            propagation_targets.append(
                {
                    "path": ancestor,
                    "current_content": summary_data.get("content", ""),
                    "has_meta": bool(summary_data.get("meta")),
                }
            )

    # Also include root
    root_summary = handle_kvault_read_summary(".")
    if root_summary:
        propagation_targets.append(
            {
                "path": ".",
                "current_content": root_summary.get("content", ""),
                "has_meta": bool(root_summary.get("meta")),
            }
        )

    # Record in session state
    if session_id:
        session = get_session_manager().get_session(session_id)
        if session:
            session.record_propagation([t["path"] for t in propagation_targets])

    result = {
        "success": True,
        "ancestors": propagation_targets,
        "count": len(propagation_targets),
    }
    return _add_workflow_warning(result, session_id, WorkflowStep.PROPAGATE)


def handle_kvault_validate_kb() -> Dict[str, Any]:
    """Check KB integrity and report issues.

    Validates:
    1. Incomplete entities - have "Context TBD" placeholder
    2. Missing frontmatter - entities without YAML frontmatter

    Returns:
        Validation results with issues list
    """
    issues = []

    # Scan all entities from filesystem (single source of truth)
    entities = scan_entities(_root())

    for entity in entities:
        entity_data = _read_entity_with_frontmatter(entity.path)
        if entity_data:
            content = entity_data.get("content", "")

            # Check for incomplete placeholder
            if "Context TBD" in content or "TBD" in content:
                issues.append(
                    {
                        "type": "incomplete_entity",
                        "severity": "info",
                        "path": entity.path,
                        "message": "Entity has placeholder content that needs enrichment",
                        "fix": "Update entity with complete context information",
                    }
                )

            # Check for missing frontmatter
            if not entity_data.get("has_frontmatter"):
                issues.append(
                    {
                        "type": "missing_frontmatter",
                        "severity": "warning",
                        "path": entity.path,
                        "message": "Entity uses legacy _meta.json instead of YAML frontmatter",
                        "fix": "Rewrite entity with kvault_write_entity() to migrate to frontmatter",
                    }
                )

    # Sort by severity
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


# ============================================================================
# MCP Server Setup
# ============================================================================


def create_server() -> "Server":
    """Create and configure the MCP server."""
    if not MCP_AVAILABLE:
        raise RuntimeError("MCP package not installed. Install with: pip install 'kvault[mcp]'")

    server = Server("kvault")

    # Register tools
    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="kvault_init",
                description="Initialize kvault and return context (hierarchy, root summary, entity count). Call this first.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "kg_root": {
                            "type": "string",
                            "description": "Path to knowledge graph root directory",
                        },
                    },
                    "required": ["kg_root"],
                },
            ),
            Tool(
                name="kvault_read_entity",
                description="Read entity with YAML frontmatter. Returns meta, content, and parent summary (sibling context).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Entity path (e.g., 'people/contacts/john_doe')",
                        },
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="kvault_write_entity",
                description="Write entity with YAML frontmatter. Returns ancestor summaries for propagation. If reasoning is provided, auto-logs a journal entry.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Entity path"},
                        "meta": {
                            "type": "object",
                            "description": "Frontmatter metadata (must include 'source' and 'aliases')",
                        },
                        "content": {
                            "type": "string",
                            "description": "Markdown content (without frontmatter)",
                        },
                        "create": {
                            "type": "boolean",
                            "description": "True to create new, False to update",
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Why this entity is being created/updated. If provided, auto-logs a journal entry.",
                        },
                        "journal_source": {
                            "type": "string",
                            "description": "Source for journal entry (defaults to meta.source)",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Session ID for workflow tracking",
                        },
                    },
                    "required": ["path", "meta", "content"],
                },
            ),
            Tool(
                name="kvault_list_entities",
                description="List entities, optionally filtered by category.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Optional category filter"},
                    },
                },
            ),
            Tool(
                name="kvault_delete_entity",
                description="Delete an entity. WARNING: This is destructive.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Entity path to delete"},
                        "session_id": {
                            "type": "string",
                            "description": "Session ID for workflow tracking",
                        },
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="kvault_move_entity",
                description="Move an entity to a new path.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source_path": {"type": "string", "description": "Current entity path"},
                        "target_path": {"type": "string", "description": "New entity path"},
                        "session_id": {
                            "type": "string",
                            "description": "Session ID for workflow tracking",
                        },
                    },
                    "required": ["source_path", "target_path"],
                },
            ),
            Tool(
                name="kvault_read_summary",
                description="Read a summary file (_summary.md) from a path.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to directory containing _summary.md",
                        },
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="kvault_write_summary",
                description="Write a summary file. Used for category summaries and propagation.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to directory for _summary.md",
                        },
                        "content": {"type": "string", "description": "Markdown content"},
                        "meta": {"type": "object", "description": "Optional frontmatter"},
                        "session_id": {
                            "type": "string",
                            "description": "Session ID for workflow tracking",
                        },
                    },
                    "required": ["path", "content"],
                },
            ),
            Tool(
                name="kvault_update_summaries",
                description="Batch-update multiple ancestor summaries in one call. Use after kvault_write_entity to propagate changes.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "updates": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "description": "Path to directory for _summary.md",
                                    },
                                    "content": {
                                        "type": "string",
                                        "description": "Updated markdown content",
                                    },
                                    "meta": {
                                        "type": "object",
                                        "description": "Optional frontmatter",
                                    },
                                },
                                "required": ["path", "content"],
                            },
                            "description": "List of summary updates",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Session ID for workflow tracking",
                        },
                    },
                    "required": ["updates"],
                },
            ),
            Tool(
                name="kvault_get_parent_summaries",
                description="Get ancestor summaries for propagation. Returns parent → root summaries.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Entity or category path"},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="kvault_write_journal",
                description="Write a journal entry for actions taken.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "actions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action_type": {"type": "string"},
                                    "path": {"type": "string"},
                                    "reasoning": {"type": "string"},
                                },
                            },
                            "description": "List of actions taken",
                        },
                        "source": {"type": "string", "description": "Source identifier"},
                        "date": {"type": "string", "description": "Optional date (YYYY-MM-DD)"},
                        "session_id": {
                            "type": "string",
                            "description": "Session ID for workflow tracking",
                        },
                    },
                    "required": ["actions", "source"],
                },
            ),
            Tool(
                name="kvault_propagate_all",
                description="Get all ancestor summaries for propagation. Returns ancestors with current content for Claude to update.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Entity or category path to propagate from",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Session ID for workflow tracking",
                        },
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="kvault_validate_kb",
                description="Check KB integrity: incomplete entities, missing frontmatter.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            result: Any = None
            if name == "kvault_init":
                result = handle_kvault_init(arguments["kg_root"])
            elif name == "kvault_read_entity":
                result = handle_kvault_read_entity(arguments["path"])
            elif name == "kvault_write_entity":
                result = handle_kvault_write_entity(
                    arguments["path"],
                    arguments["meta"],
                    arguments["content"],
                    create=arguments.get("create", False),
                    session_id=arguments.get("session_id"),
                    reasoning=arguments.get("reasoning"),
                    journal_source=arguments.get("journal_source"),
                )
            elif name == "kvault_list_entities":
                result = handle_kvault_list_entities(category=arguments.get("category"))
            elif name == "kvault_delete_entity":
                result = handle_kvault_delete_entity(
                    arguments["path"],
                    session_id=arguments.get("session_id"),
                )
            elif name == "kvault_move_entity":
                result = handle_kvault_move_entity(
                    arguments["source_path"],
                    arguments["target_path"],
                    session_id=arguments.get("session_id"),
                )
            elif name == "kvault_read_summary":
                result = handle_kvault_read_summary(arguments["path"])
            elif name == "kvault_write_summary":
                result = handle_kvault_write_summary(
                    arguments["path"],
                    arguments["content"],
                    meta=arguments.get("meta"),
                    session_id=arguments.get("session_id"),
                )
            elif name == "kvault_get_parent_summaries":
                result = handle_kvault_get_parent_summaries(arguments["path"])
            elif name == "kvault_write_journal":
                result = handle_kvault_write_journal(
                    arguments["actions"],
                    arguments["source"],
                    date=arguments.get("date"),
                    session_id=arguments.get("session_id"),
                )
            elif name == "kvault_update_summaries":
                result = handle_kvault_update_summaries(
                    arguments["updates"],
                    session_id=arguments.get("session_id"),
                )
            elif name == "kvault_propagate_all":
                result = handle_kvault_propagate_all(
                    arguments["path"],
                    session_id=arguments.get("session_id"),
                )
            elif name == "kvault_validate_kb":
                result = handle_kvault_validate_kb()
            else:
                result = {"error": f"Unknown tool: {name}"}

            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        except Exception as e:
            error_result = {"error": str(e), "type": type(e).__name__}
            return [TextContent(type="text", text=json.dumps(error_result))]

    return server


async def run_server():
    """Run the MCP server."""
    if not MCP_AVAILABLE:
        raise RuntimeError("MCP package not installed. Install with: pip install 'kvault[mcp]'")

    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """CLI entry point for the MCP server."""
    import asyncio

    asyncio.run(run_server())


if __name__ == "__main__":
    main()
