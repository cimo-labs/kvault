"""Thin MCP compatibility server for kvault.

The server is intentionally root-bound: each process can operate on exactly one
knowledge base root supplied by ``--kb-root`` or ``KVAULT_KB_ROOT``.
"""

from __future__ import annotations

import os
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

from kvault.core import operations as ops
from kvault.core.daily_artifacts import generate_daily_artifact, parse_iso_date
from kvault.core.observability import ObservabilityLogger
from kvault.core.validation import ErrorCode, error_response, success_response

try:  # Optional dependency installed by knowledgevault[mcp].
    FastMCP = import_module("mcp.server.fastmcp").FastMCP
except ImportError:  # pragma: no cover - exercised when optional extra is absent.
    FastMCP = None

KVAULT_KB_ROOT_ENV = "KVAULT_KB_ROOT"


def resolve_bound_root(kb_root: Optional[Path | str] = None) -> Path:
    """Resolve and validate the server-bound KB root."""
    raw_root = kb_root or os.environ.get(KVAULT_KB_ROOT_ENV)
    if raw_root is None or str(raw_root).strip() == "":
        raise click.ClickException(
            f"kvault-mcp requires --kb-root PATH or {KVAULT_KB_ROOT_ENV}=PATH"
        )

    root = Path(os.path.expanduser(str(raw_root))).resolve()
    if not root.exists():
        raise click.ClickException(f"KB root does not exist: {root}")

    allowed_error = ops.validate_allowed_root(root)
    if allowed_error:
        raise click.ClickException(allowed_error)
    return root


def _tool_root(
    bound_root: Path, kg_root: Optional[str]
) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    if kg_root is None:
        return bound_root, None

    try:
        requested = resolve_bound_root(kg_root)
    except click.ClickException as exc:
        return None, error_response(ErrorCode.VALIDATION_ERROR, str(exc))

    if requested != bound_root:
        return None, error_response(
            ErrorCode.VALIDATION_ERROR,
            "MCP server is bound to a different KB root",
            details={
                "bound_root": str(bound_root),
                "requested_root": str(requested),
            },
            hint="Start a separate kvault-mcp process for each KB root.",
        )
    return bound_root, None


def _status_payload(root: Path) -> Dict[str, Any]:
    info = ops.get_kb_info(root)
    info["health"] = {
        "root_summary_exists": (root / "_summary.md").exists(),
        "kvault_dir_exists": (root / ".kvault").exists(),
    }
    return success_response(info)


def _serialize_daily_result(root: Path, result: Any) -> Dict[str, Any]:
    return success_response(
        {
            "artifact_date": result.artifact_date.isoformat(),
            "path": str(result.path),
            "relative_path": str(result.path.relative_to(root)),
            "content": result.content,
            "written": result.written,
        }
    )


def create_server(kb_root: Path | str) -> Any:
    """Create a FastMCP server bound to *kb_root*."""
    if FastMCP is None:
        raise click.ClickException(
            "MCP dependencies not installed. Run: pip install 'knowledgevault[mcp]'"
        )

    bound_root = resolve_bound_root(kb_root)
    server = FastMCP(
        "kvault",
        instructions=(
            "Root-bound kvault compatibility tools. This server can only access " f"{bound_root}."
        ),
    )

    @server.tool(name="kvault_init")
    def kvault_init(kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Return bound-root status and reject mismatched roots."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return _status_payload(root)

    @server.tool(name="kvault_status")
    def kvault_status(kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Show KB status."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return _status_payload(root)

    @server.tool(name="kvault_read_entity")
    def kvault_read_entity(path: str, kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Read an entity plus parent summary context."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        result = ops.read_entity(root, path)
        if result is None:
            return error_response(ErrorCode.NOT_FOUND, f"Entity not found: {path}")
        return success_response(result)

    @server.tool(name="kvault_read_node")
    def kvault_read_node(
        path: str,
        parents: str = "immediate",
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read a node plus parent context."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        if parents not in {"none", "immediate", "all"}:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "parents must be one of: none, immediate, all",
            )
        result = ops.read_node(root, path, parents=parents)
        if result is None:
            return error_response(ErrorCode.NOT_FOUND, f"Node not found: {path}")
        return success_response(result)

    @server.tool(name="kvault_write_entity")
    def kvault_write_entity(
        path: str,
        content: str,
        meta: Optional[Dict[str, Any]] = None,
        create: bool = False,
        reasoning: Optional[str] = None,
        journal_source: Optional[str] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update an entity."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return ops.write_entity(
            root,
            path,
            content,
            meta=meta,
            create=create,
            reasoning=reasoning,
            journal_source=journal_source,
            default_source="auto:mcp",
        )

    @server.tool(name="kvault_write_node")
    def kvault_write_node(
        path: str,
        content: str,
        meta: Optional[Dict[str, Any]] = None,
        create: bool = False,
        reasoning: Optional[str] = None,
        journal_source: Optional[str] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update any node summary."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return ops.write_node(
            root,
            path,
            content,
            meta=meta,
            create=create,
            reasoning=reasoning,
            journal_source=journal_source,
            default_source="auto:mcp",
        )

    @server.tool(name="kvault_list_entities")
    def kvault_list_entities(
        category: Optional[str] = None, kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """List entities, optionally filtered by category."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        entities = ops.list_entities(root, category=category)
        return success_response({"entities": entities, "count": len(entities)})

    @server.tool(name="kvault_list_nodes")
    def kvault_list_nodes(
        path: str = ".",
        recursive: bool = False,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List child nodes under a node path."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        nodes = ops.list_nodes(root, path=path, recursive=recursive)
        return success_response({"nodes": nodes, "count": len(nodes)})

    @server.tool(name="kvault_search")
    def kvault_search(
        query: str,
        limit: int = 10,
        include_content: bool = False,
        parents: str = "none",
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search visible node summaries."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        if parents not in {"none", "immediate", "all"}:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "parents must be one of: none, immediate, all",
            )
        result = ops.search_nodes(
            root,
            query=query,
            limit=limit,
            include_content=include_content,
        )
        if parents != "none":
            for item in result["results"]:
                item["node"] = ops.read_node(root, item["path"], parents=parents)
        return success_response(result)

    @server.tool(name="kvault_delete_entity")
    def kvault_delete_entity(path: str, kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Delete an entity directory."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return ops.delete_entity(root, path)

    @server.tool(name="kvault_move_entity")
    def kvault_move_entity(
        source_path: str, target_path: str, kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Move an entity to a new path."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return ops.move_entity(root, source_path, target_path)

    @server.tool(name="kvault_read_summary")
    def kvault_read_summary(path: str = ".", kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Read a summary file."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        result = ops.read_summary(root, path)
        if result is None:
            return error_response(ErrorCode.NOT_FOUND, f"Summary not found: {path}")
        return success_response(result)

    @server.tool(name="kvault_write_summary")
    def kvault_write_summary(
        path: str,
        content: str,
        meta: Optional[Dict[str, Any]] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write a summary file."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return ops.write_summary(root, path, content, meta=meta)

    @server.tool(name="kvault_update_summaries")
    def kvault_update_summaries(
        updates: List[Dict[str, Any]], kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Batch-update summaries."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return ops.update_summaries(root, updates)

    @server.tool(name="kvault_get_parent_summaries")
    def kvault_get_parent_summaries(path: str, kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Get ancestor summaries for propagation."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return ops.get_ancestors(root, path)

    @server.tool(name="kvault_get_ancestors")
    def kvault_get_ancestors(path: str, kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Alias for kvault_get_parent_summaries."""
        return kvault_get_parent_summaries(path=path, kg_root=kg_root)

    @server.tool(name="kvault_propagate_all")
    def kvault_propagate_all(path: str, kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Compatibility alias returning all summary propagation targets."""
        return kvault_get_parent_summaries(path=path, kg_root=kg_root)

    @server.tool(name="kvault_write_journal")
    def kvault_write_journal(
        actions: List[Dict[str, Any]],
        source: str,
        date: Optional[str] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write a journal entry."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return ops.write_journal(root, actions=actions, source=source, date=date)

    @server.tool(name="kvault_generate_daily_artifact")
    def kvault_generate_daily_artifact(
        artifact_date: Optional[str] = None,
        force: bool = False,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a daily artifact markdown file."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            parsed_date = parse_iso_date(artifact_date)
            result = generate_daily_artifact(root, artifact_date=parsed_date, force=force)
        except ValueError as exc:
            return error_response(ErrorCode.VALIDATION_ERROR, str(exc))
        return _serialize_daily_result(root, result)

    @server.tool(name="kvault_validate_kb")
    def kvault_validate_kb(kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Validate KB integrity."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return success_response(ops.validate_kb(root))

    @server.tool(name="kvault_log_phase")
    def kvault_log_phase(
        phase: str,
        data: Dict[str, Any],
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Log an observability phase to .kvault/logs.db."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            logger = ObservabilityLogger(root / ".kvault" / "logs.db")
            logger.log(phase, data)
        except ValueError as exc:
            return error_response(ErrorCode.VALIDATION_ERROR, str(exc))
        return success_response({"session_id": logger.session_id, "phase": phase})

    return server


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--kb-root",
    type=click.Path(path_type=Path),
    default=None,
    help=f"Knowledge base root. May also be set with {KVAULT_KB_ROOT_ENV}.",
)
def main(kb_root: Optional[Path]) -> None:
    """Run the kvault MCP compatibility server over stdio."""
    server = create_server(resolve_bound_root(kb_root))
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
