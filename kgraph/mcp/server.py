"""kgraph MCP Server - Model Context Protocol server for knowledge graph operations.

Provides 20 tools for Claude Code to interact with the knowledge graph:

Index Tools (4):
- kgraph_search: Full-text search for entities
- kgraph_find_by_alias: Find entity by exact alias
- kgraph_find_by_email_domain: Find entities by email domain
- kgraph_rebuild_index: Rebuild the entity index

Entity Tools (5):
- kgraph_read_entity: Read entity with YAML frontmatter
- kgraph_write_entity: Write entity with YAML frontmatter (validates required fields)
- kgraph_list_entities: List entities in a category
- kgraph_delete_entity: Delete an entity
- kgraph_move_entity: Move an entity to new path

Summary Tools (4):
- kgraph_read_summary: Read a summary file
- kgraph_write_summary: Write a summary file
- kgraph_get_parent_summaries: Get ancestor summaries
- kgraph_propagate_all: Get all ancestors for propagation

Research Tool (1):
- kgraph_research: Research entities using multiple strategies

Workflow Tools (4):
- kgraph_log_phase: Log a workflow phase
- kgraph_write_journal: Write a journal entry
- kgraph_status: Get current workflow status
- kgraph_validate_transition: Check workflow transition validity

Validation Tools (1):
- kgraph_validate_kb: Check KB integrity (index sync, orphaned entries, etc.)

Session Management:
- Most tools accept optional session_id for workflow tracking
- Entity modification tools (write/delete/move) support auto_rebuild parameter
- Workflow warnings are included in responses when steps are skipped
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

from kgraph.core.frontmatter import parse_frontmatter, build_frontmatter, merge_frontmatter
from kgraph.core.index import EntityIndex, IndexEntry
from kgraph.core.storage import SimpleStorage, normalize_entity_id
from kgraph.core.research import EntityResearcher
from kgraph.core.observability import ObservabilityLogger
from kgraph.mcp.state import get_session_manager, SessionState, WorkflowStep
from kgraph.mcp.validation import (
    normalize_path,
    validate_entity_path,
    validate_frontmatter,
    build_default_frontmatter,
    extract_identifiers,
    get_journal_path,
    format_journal_entry,
    ErrorCode,
    error_response,
    success_response,
)


# Global instances (initialized when server starts)
_kg_root: Optional[Path] = None
_index: Optional[EntityIndex] = None
_storage: Optional[SimpleStorage] = None
_researcher: Optional[EntityResearcher] = None
_logger: Optional[ObservabilityLogger] = None


def _ensure_initialized():
    """Ensure global instances are initialized."""
    if _kg_root is None:
        raise RuntimeError("kgraph MCP server not initialized. Call kgraph_init first.")


def _init_infrastructure(kg_root: str) -> Dict[str, Any]:
    """Initialize kgraph infrastructure for a given root."""
    global _kg_root, _index, _storage, _researcher, _logger

    _kg_root = Path(kg_root).resolve()

    kgraph_dir = _kg_root / ".kgraph"
    kgraph_dir.mkdir(parents=True, exist_ok=True)

    _index = EntityIndex(kgraph_dir / "index.db")
    _storage = SimpleStorage(_kg_root)
    _researcher = EntityResearcher(_index)
    _logger = ObservabilityLogger(kgraph_dir / "logs.db")

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
        "entity_count": _index.count(),
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
            subdirs = sorted([
                p for p in path.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            ])
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


def _read_entity_with_frontmatter(entity_path: str) -> Optional[Dict[str, Any]]:
    """Read entity, preferring YAML frontmatter over legacy _meta.json.

    Returns dict with 'meta' and 'content' keys.
    """
    _ensure_initialized()

    full_path = _kg_root / entity_path
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
) -> Dict[str, Any]:
    """Write entity with YAML frontmatter.

    Args:
        entity_path: Path like "people/contacts/john_doe"
        meta: Frontmatter metadata (must include: source, aliases)
        content: Markdown content (without frontmatter)
        create: If True, fail if entity exists. If False, update.
        auto_rebuild: If True, rebuild index after writing.
        session_id: Optional session ID for workflow tracking.

    Returns:
        Result dict with path and success status
    """
    _ensure_initialized()

    entity_path = normalize_path(entity_path)
    is_valid, error = validate_entity_path(entity_path)
    if not is_valid:
        return error_response(ErrorCode.VALIDATION_ERROR, error)

    full_path = _kg_root / entity_path
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

    # Auto-rebuild index if requested
    if auto_rebuild:
        rebuild_result = handle_kgraph_rebuild_index(session_id=session_id)
        result["index_rebuilt"] = True
        result["entity_count"] = rebuild_result.get("entity_count")

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


def _add_workflow_warning(result: Dict[str, Any], session_id: Optional[str], expected_step: WorkflowStep) -> Dict[str, Any]:
    """Add workflow warning to result if steps are out of order."""
    warning = _check_workflow_order(session_id, expected_step)
    if warning:
        result["workflow_warning"] = warning
    return result


# ============================================================================
# Tool Handlers
# ============================================================================


def handle_kgraph_init(kg_root: str) -> Dict[str, Any]:
    """Initialize kgraph and return context.

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


def handle_kgraph_search(
    query: str,
    category: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Full-text search for entities.

    Args:
        query: Search query
        category: Optional category filter
        limit: Maximum results

    Returns:
        List of matching entities
    """
    _ensure_initialized()

    results = _index.search(query, category=category, limit=limit)
    return [
        {
            "path": r.path,
            "name": r.name,
            "aliases": r.aliases,
            "category": r.category,
            "email_domains": r.email_domains,
        }
        for r in results
    ]


def handle_kgraph_find_by_alias(alias: str) -> Optional[Dict[str, Any]]:
    """Find entity by exact alias match.

    Args:
        alias: Alias to search for (case-insensitive)

    Returns:
        Entity info if found, None otherwise
    """
    _ensure_initialized()

    result = _index.find_by_alias(alias)
    if result:
        return {
            "path": result.path,
            "name": result.name,
            "aliases": result.aliases,
            "category": result.category,
            "email_domains": result.email_domains,
        }
    return None


def handle_kgraph_find_by_email_domain(domain: str) -> List[Dict[str, Any]]:
    """Find entities by email domain.

    Args:
        domain: Email domain (e.g., "anthropic.com")

    Returns:
        List of matching entities
    """
    _ensure_initialized()

    results = _index.find_by_email_domain(domain)
    return [
        {
            "path": r.path,
            "name": r.name,
            "aliases": r.aliases,
            "category": r.category,
            "email_domains": r.email_domains,
        }
        for r in results
    ]


def handle_kgraph_rebuild_index(session_id: Optional[str] = None) -> Dict[str, Any]:
    """Rebuild the entity index from filesystem.

    Args:
        session_id: Optional session ID for workflow tracking.

    Returns:
        Count of entities indexed
    """
    _ensure_initialized()

    count = _index.rebuild(_kg_root)

    # Record in session state
    if session_id:
        session = get_session_manager().get_session(session_id)
        if session:
            session.record_rebuild(count)

    return {
        "success": True,
        "entity_count": count,
    }


def handle_kgraph_read_entity(path: str) -> Optional[Dict[str, Any]]:
    """Read entity with YAML frontmatter.

    Args:
        path: Entity path (e.g., "people/contacts/john_doe")

    Returns:
        Entity data with meta and content, or None if not found
    """
    return _read_entity_with_frontmatter(path)


def handle_kgraph_write_entity(
    path: str,
    meta: Dict[str, Any],
    content: str,
    create: bool = False,
    auto_rebuild: bool = False,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Write entity with YAML frontmatter.

    Args:
        path: Entity path
        meta: Frontmatter metadata
        content: Markdown content
        create: If True, create new entity. If False, update existing.
        auto_rebuild: If True, rebuild index after writing.
        session_id: Optional session ID for workflow tracking.

    Returns:
        Result with success status
    """
    result = _write_entity_with_frontmatter(path, meta, content, create, auto_rebuild, session_id)
    return _add_workflow_warning(result, session_id, WorkflowStep.EXECUTE)


def handle_kgraph_list_entities(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """List entities, optionally filtered by category.

    Args:
        category: Optional category filter (e.g., "people")

    Returns:
        List of entity summaries
    """
    _ensure_initialized()

    entries = _index.list_all(category=category)
    return [
        {
            "path": e.path,
            "name": e.name,
            "category": e.category,
            "last_updated": e.last_updated,
        }
        for e in entries
    ]


def handle_kgraph_delete_entity(
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
    _ensure_initialized()

    path = normalize_path(path)
    full_path = _kg_root / path

    if not full_path.exists():
        return error_response(ErrorCode.NOT_FOUND, f"Entity doesn't exist: {path}")

    # Remove from filesystem
    shutil.rmtree(full_path)

    # Remove from index
    _index.remove(path)

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

    # Auto-rebuild index if requested
    if auto_rebuild:
        rebuild_result = handle_kgraph_rebuild_index(session_id=session_id)
        result["index_rebuilt"] = True
        result["entity_count"] = rebuild_result.get("entity_count")

    return result


def handle_kgraph_move_entity(
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
    _ensure_initialized()

    source_path = normalize_path(source_path)
    target_path = normalize_path(target_path)

    # Validate paths
    is_valid, error = validate_entity_path(target_path)
    if not is_valid:
        return error_response(
            ErrorCode.VALIDATION_ERROR,
            f"Invalid target path: {error}",
        )

    source_full = _kg_root / source_path
    target_full = _kg_root / target_path

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

    # Update index
    _index.remove(source_path)

    # Re-index target (read and add)
    entity_data = _read_entity_with_frontmatter(target_path)
    if entity_data and entity_data.get("meta"):
        meta = entity_data["meta"]
        _index.add(
            path=target_path,
            name=meta.get("name", target_path.split("/")[-1]),
            aliases=meta.get("aliases", []),
            category=target_path.split("/")[0] if "/" in target_path else "",
        )

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

    # Auto-rebuild index if requested
    if auto_rebuild:
        rebuild_result = handle_kgraph_rebuild_index(session_id=session_id)
        result["index_rebuilt"] = True
        result["entity_count"] = rebuild_result.get("entity_count")

    return result


def handle_kgraph_read_summary(path: str) -> Optional[Dict[str, Any]]:
    """Read a summary file.

    Args:
        path: Path to directory containing _summary.md

    Returns:
        Summary content with optional frontmatter
    """
    _ensure_initialized()

    path = normalize_path(path)
    summary_path = _kg_root / path / "_summary.md"

    if not summary_path.exists():
        # Try as direct file
        summary_path = _kg_root / path
        if not summary_path.exists() or not path.endswith(".md"):
            return None

    content = summary_path.read_text()
    meta, body = parse_frontmatter(content)

    return {
        "path": path,
        "meta": meta,
        "content": body if meta else content,
    }


def handle_kgraph_write_summary(
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
    _ensure_initialized()

    path = normalize_path(path)
    dir_path = _kg_root / path
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


def handle_kgraph_get_parent_summaries(path: str) -> List[Dict[str, Any]]:
    """Get ancestor summaries for a path.

    Args:
        path: Entity or category path

    Returns:
        List of ancestor summaries (closest first)
    """
    _ensure_initialized()

    path = normalize_path(path)
    ancestors = _storage.get_ancestors(path)

    results = []
    for ancestor in ancestors:
        summary_data = handle_kgraph_read_summary(ancestor)
        if summary_data:
            results.append(summary_data)

    # Also include root summary
    root_summary = handle_kgraph_read_summary(".")
    if root_summary:
        results.append(root_summary)

    return results


def handle_kgraph_research(
    name: str,
    aliases: Optional[List[str]] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Research entities using multiple matching strategies.

    Args:
        name: Name to search for
        aliases: Optional additional aliases
        email: Optional email address
        phone: Optional phone number
        session_id: Optional session ID for workflow tracking.

    Returns:
        Research results with matches and suggested action
    """
    _ensure_initialized()

    # Add phone to aliases if provided
    if phone and aliases is None:
        aliases = []
    if phone:
        from kgraph.mcp.validation import normalize_phone
        normalized_phone = normalize_phone(phone)
        aliases.append(normalized_phone)

    # Run research
    candidates = _researcher.research(name, aliases=aliases, email=email)

    # Get suggestion
    action, target, confidence = _researcher.suggest_action(name, aliases=aliases, email=email)

    matches = [
        {
            "path": c.candidate_path,
            "name": c.candidate_name,
            "match_type": c.match_type,
            "score": c.match_score,
            "details": c.match_details,
        }
        for c in candidates
    ]

    # Record in session state
    if session_id:
        session = get_session_manager().get_session(session_id)
        if session:
            session.record_research(matches, intent=action)

    return {
        "matches": matches,
        "suggested_action": action,
        "suggested_target": target,
        "confidence": confidence,
    }


def handle_kgraph_log_phase(
    phase: str,
    data: Dict[str, Any],
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Log a workflow phase.

    Args:
        phase: Phase name (research, decide, execute, propagate, log, rebuild)
        data: Structured data to log
        session_id: Optional session ID

    Returns:
        Log result
    """
    _ensure_initialized()

    # Add session ID to data
    if session_id:
        data["session_id"] = session_id

    _logger.log(phase, data)

    return {
        "success": True,
        "phase": phase,
        "session_id": _logger.session_id,
    }


def handle_kgraph_write_journal(
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
    _ensure_initialized()

    # Parse date
    dt = datetime.now()
    if date:
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            pass

    # Get journal path
    journal_rel_path = get_journal_path(dt)
    journal_full_path = _kg_root / journal_rel_path

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


def handle_kgraph_propagate_all(
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
    _ensure_initialized()

    path = normalize_path(path)
    ancestors = _storage.get_ancestors(path)

    propagation_targets = []
    for ancestor in ancestors:
        summary_data = handle_kgraph_read_summary(ancestor)
        if summary_data:
            propagation_targets.append({
                "path": ancestor,
                "current_content": summary_data.get("content", ""),
                "has_meta": bool(summary_data.get("meta")),
            })

    # Also include root
    root_summary = handle_kgraph_read_summary(".")
    if root_summary:
        propagation_targets.append({
            "path": ".",
            "current_content": root_summary.get("content", ""),
            "has_meta": bool(root_summary.get("meta")),
        })

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


def handle_kgraph_validate_kb() -> Dict[str, Any]:
    """Check KB integrity and report issues.

    Validates:
    1. Index sync - entities exist but not indexed
    2. Orphaned index entries - indexed but no file
    3. Incomplete entities - have "Context TBD" placeholder
    4. Missing frontmatter - entities without YAML frontmatter

    Returns:
        Validation results with issues list
    """
    _ensure_initialized()

    issues = []

    # Get all entities from index
    indexed_entries = _index.list_all()
    indexed_paths = {e.path for e in indexed_entries}

    # Check 1: Find entities on filesystem not in index
    for entity_dir in _kg_root.rglob("_summary.md"):
        # Skip root summary
        if entity_dir.parent == _kg_root:
            continue

        rel_path = str(entity_dir.parent.relative_to(_kg_root))

        # Skip special directories
        if rel_path.startswith(".") or rel_path.startswith("journal"):
            continue

        # Check if it should be an entity (has category/entity structure)
        parts = rel_path.split("/")
        if len(parts) >= 2 and rel_path not in indexed_paths:
            issues.append({
                "type": "index_missing",
                "severity": "warning",
                "path": rel_path,
                "message": "Entity exists on filesystem but not in index",
                "fix": "Run kgraph_rebuild_index()",
            })

    # Check 2: Orphaned index entries
    for entry in indexed_entries:
        full_path = _kg_root / entry.path / "_summary.md"
        if not full_path.exists():
            issues.append({
                "type": "orphaned_index",
                "severity": "warning",
                "path": entry.path,
                "message": "Index entry exists but no entity file",
                "fix": "Run kgraph_rebuild_index() to remove stale entries",
            })

    # Check 3: Incomplete entities and missing frontmatter
    for entry in indexed_entries:
        entity_data = _read_entity_with_frontmatter(entry.path)
        if entity_data:
            content = entity_data.get("content", "")

            # Check for incomplete placeholder
            if "Context TBD" in content or "TBD" in content:
                issues.append({
                    "type": "incomplete_entity",
                    "severity": "info",
                    "path": entry.path,
                    "message": "Entity has placeholder content that needs enrichment",
                    "fix": "Update entity with complete context information",
                })

            # Check for missing frontmatter
            if not entity_data.get("has_frontmatter"):
                issues.append({
                    "type": "missing_frontmatter",
                    "severity": "warning",
                    "path": entry.path,
                    "message": "Entity uses legacy _meta.json instead of YAML frontmatter",
                    "fix": "Rewrite entity with kgraph_write_entity() to migrate to frontmatter",
                })

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


def handle_kgraph_status(session_id: Optional[str] = None) -> Dict[str, Any]:
    """Get current workflow status.

    Args:
        session_id: Optional session ID

    Returns:
        Current session state
    """
    session_mgr = get_session_manager()

    if session_id:
        session = session_mgr.get_session(session_id)
        if session:
            return session.to_dict()
        return {"error": f"Session not found: {session_id}"}

    # Return all sessions
    return {
        "sessions": session_mgr.list_sessions(),
    }


def handle_validate_workflow_transition(
    from_step: str,
    to_step: str,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Check if a workflow transition is valid (advisory).

    Args:
        from_step: Current step name
        to_step: Target step name
        session_id: Optional session ID

    Returns:
        Validation result
    """
    try:
        from_ws = WorkflowStep(from_step)
        to_ws = WorkflowStep(to_step)
    except ValueError as e:
        return {"valid": False, "reason": str(e)}

    session_mgr = get_session_manager()
    if session_id:
        session = session_mgr.get_session(session_id)
        if session:
            valid = session.can_transition_to(to_ws)
            return {
                "valid": valid,
                "reason": None if valid else f"Cannot transition from {from_step} to {to_step}",
                "current_step": session.current_step.value,
            }

    # Check without session
    from kgraph.mcp.state import VALID_TRANSITIONS
    valid = to_ws in VALID_TRANSITIONS.get(from_ws, [])
    return {
        "valid": valid,
        "reason": None if valid else f"Cannot transition from {from_step} to {to_step}",
    }


# ============================================================================
# MCP Server Setup
# ============================================================================


def create_server() -> "Server":
    """Create and configure the MCP server."""
    if not MCP_AVAILABLE:
        raise RuntimeError("MCP package not installed. Install with: pip install 'kgraph[mcp]'")

    server = Server("kgraph")

    # Register tools
    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="kgraph_init",
                description="Initialize kgraph and return context (hierarchy, root summary, entity count). Call this first.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "kg_root": {"type": "string", "description": "Path to knowledge graph root directory"},
                    },
                    "required": ["kg_root"],
                },
            ),
            Tool(
                name="kgraph_search",
                description="Full-text search for entities in the knowledge graph.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "category": {"type": "string", "description": "Optional category filter"},
                        "limit": {"type": "integer", "description": "Max results (default 10)"},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="kgraph_find_by_alias",
                description="Find entity by exact alias (case-insensitive). Use for phone/email lookups.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "alias": {"type": "string", "description": "Alias to find (name, email, phone)"},
                    },
                    "required": ["alias"],
                },
            ),
            Tool(
                name="kgraph_find_by_email_domain",
                description="Find entities by email domain (e.g., 'anthropic.com').",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "description": "Email domain to search"},
                    },
                    "required": ["domain"],
                },
            ),
            Tool(
                name="kgraph_rebuild_index",
                description="Rebuild the entity index from filesystem. Run after creating/deleting entities.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Session ID for workflow tracking"},
                    },
                },
            ),
            Tool(
                name="kgraph_read_entity",
                description="Read entity with YAML frontmatter. Returns meta and content.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Entity path (e.g., 'people/contacts/john_doe')"},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="kgraph_write_entity",
                description="Write entity with YAML frontmatter. Requires 'source' and 'aliases' in meta. Use create=true for new entities.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Entity path"},
                        "meta": {"type": "object", "description": "Frontmatter metadata (must include 'source' and 'aliases')"},
                        "content": {"type": "string", "description": "Markdown content (without frontmatter)"},
                        "create": {"type": "boolean", "description": "True to create new, False to update"},
                        "auto_rebuild": {"type": "boolean", "description": "If true, rebuild index after writing"},
                        "session_id": {"type": "string", "description": "Session ID for workflow tracking"},
                    },
                    "required": ["path", "meta", "content"],
                },
            ),
            Tool(
                name="kgraph_list_entities",
                description="List entities, optionally filtered by category.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Optional category filter"},
                    },
                },
            ),
            Tool(
                name="kgraph_delete_entity",
                description="Delete an entity. WARNING: This is destructive.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Entity path to delete"},
                        "auto_rebuild": {"type": "boolean", "description": "If true, rebuild index after deleting"},
                        "session_id": {"type": "string", "description": "Session ID for workflow tracking"},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="kgraph_move_entity",
                description="Move an entity to a new path.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source_path": {"type": "string", "description": "Current entity path"},
                        "target_path": {"type": "string", "description": "New entity path"},
                        "auto_rebuild": {"type": "boolean", "description": "If true, rebuild index after moving"},
                        "session_id": {"type": "string", "description": "Session ID for workflow tracking"},
                    },
                    "required": ["source_path", "target_path"],
                },
            ),
            Tool(
                name="kgraph_read_summary",
                description="Read a summary file (_summary.md) from a path.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to directory containing _summary.md"},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="kgraph_write_summary",
                description="Write a summary file. Used for category summaries and propagation.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to directory for _summary.md"},
                        "content": {"type": "string", "description": "Markdown content"},
                        "meta": {"type": "object", "description": "Optional frontmatter"},
                        "session_id": {"type": "string", "description": "Session ID for workflow tracking"},
                    },
                    "required": ["path", "content"],
                },
            ),
            Tool(
                name="kgraph_get_parent_summaries",
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
                name="kgraph_research",
                description="Research entities using multiple matching strategies (fuzzy name, alias, email domain).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Name to search for"},
                        "aliases": {"type": "array", "items": {"type": "string"}, "description": "Additional aliases"},
                        "email": {"type": "string", "description": "Email address"},
                        "phone": {"type": "string", "description": "Phone number"},
                        "session_id": {"type": "string", "description": "Session ID for workflow tracking"},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="kgraph_log_phase",
                description="Log a workflow phase for observability.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "phase": {"type": "string", "description": "Phase name (research, decide, execute, etc.)"},
                        "data": {"type": "object", "description": "Structured data to log"},
                        "session_id": {"type": "string", "description": "Optional session ID"},
                    },
                    "required": ["phase", "data"],
                },
            ),
            Tool(
                name="kgraph_write_journal",
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
                        "session_id": {"type": "string", "description": "Session ID for workflow tracking"},
                    },
                    "required": ["actions", "source"],
                },
            ),
            Tool(
                name="kgraph_status",
                description="Get current workflow status and session info.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Optional session ID"},
                    },
                },
            ),
            Tool(
                name="kgraph_validate_transition",
                description="Check if a workflow transition is valid (advisory).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "from_step": {"type": "string", "description": "Current step"},
                        "to_step": {"type": "string", "description": "Target step"},
                        "session_id": {"type": "string", "description": "Optional session ID"},
                    },
                    "required": ["from_step", "to_step"],
                },
            ),
            Tool(
                name="kgraph_propagate_all",
                description="Get all ancestor summaries for propagation. Returns ancestors with current content for Claude to update.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Entity or category path to propagate from"},
                        "session_id": {"type": "string", "description": "Session ID for workflow tracking"},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="kgraph_validate_kb",
                description="Check KB integrity: index sync, orphaned entries, incomplete entities, missing frontmatter.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            if name == "kgraph_init":
                result = handle_kgraph_init(arguments["kg_root"])
            elif name == "kgraph_search":
                result = handle_kgraph_search(
                    arguments["query"],
                    category=arguments.get("category"),
                    limit=arguments.get("limit", 10),
                )
            elif name == "kgraph_find_by_alias":
                result = handle_kgraph_find_by_alias(arguments["alias"])
            elif name == "kgraph_find_by_email_domain":
                result = handle_kgraph_find_by_email_domain(arguments["domain"])
            elif name == "kgraph_rebuild_index":
                result = handle_kgraph_rebuild_index(
                    session_id=arguments.get("session_id"),
                )
            elif name == "kgraph_read_entity":
                result = handle_kgraph_read_entity(arguments["path"])
            elif name == "kgraph_write_entity":
                result = handle_kgraph_write_entity(
                    arguments["path"],
                    arguments["meta"],
                    arguments["content"],
                    create=arguments.get("create", False),
                    auto_rebuild=arguments.get("auto_rebuild", False),
                    session_id=arguments.get("session_id"),
                )
            elif name == "kgraph_list_entities":
                result = handle_kgraph_list_entities(category=arguments.get("category"))
            elif name == "kgraph_delete_entity":
                result = handle_kgraph_delete_entity(
                    arguments["path"],
                    auto_rebuild=arguments.get("auto_rebuild", False),
                    session_id=arguments.get("session_id"),
                )
            elif name == "kgraph_move_entity":
                result = handle_kgraph_move_entity(
                    arguments["source_path"],
                    arguments["target_path"],
                    auto_rebuild=arguments.get("auto_rebuild", False),
                    session_id=arguments.get("session_id"),
                )
            elif name == "kgraph_read_summary":
                result = handle_kgraph_read_summary(arguments["path"])
            elif name == "kgraph_write_summary":
                result = handle_kgraph_write_summary(
                    arguments["path"],
                    arguments["content"],
                    meta=arguments.get("meta"),
                    session_id=arguments.get("session_id"),
                )
            elif name == "kgraph_get_parent_summaries":
                result = handle_kgraph_get_parent_summaries(arguments["path"])
            elif name == "kgraph_research":
                result = handle_kgraph_research(
                    arguments["name"],
                    aliases=arguments.get("aliases"),
                    email=arguments.get("email"),
                    phone=arguments.get("phone"),
                    session_id=arguments.get("session_id"),
                )
            elif name == "kgraph_log_phase":
                result = handle_kgraph_log_phase(
                    arguments["phase"],
                    arguments["data"],
                    session_id=arguments.get("session_id"),
                )
            elif name == "kgraph_write_journal":
                result = handle_kgraph_write_journal(
                    arguments["actions"],
                    arguments["source"],
                    date=arguments.get("date"),
                    session_id=arguments.get("session_id"),
                )
            elif name == "kgraph_status":
                result = handle_kgraph_status(session_id=arguments.get("session_id"))
            elif name == "kgraph_validate_transition":
                result = handle_validate_workflow_transition(
                    arguments["from_step"],
                    arguments["to_step"],
                    session_id=arguments.get("session_id"),
                )
            elif name == "kgraph_propagate_all":
                result = handle_kgraph_propagate_all(
                    arguments["path"],
                    session_id=arguments.get("session_id"),
                )
            elif name == "kgraph_validate_kb":
                result = handle_kgraph_validate_kb()
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
        raise RuntimeError("MCP package not installed. Install with: pip install 'kgraph[mcp]'")

    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """CLI entry point for the MCP server."""
    import asyncio
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
