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

from kvault import __version__
from kvault.core import operations as ops
from kvault.core.daily_artifacts import generate_daily_artifact, parse_iso_date
from kvault.core.observability import ObservabilityLogger
from kvault.core.reconciliation import (
    ReconciliationError,
    ReconciliationPlan,
    apply_reconciliation,
    approve_reconciliation,
    prepare_reconciliation,
    reconciliation_status,
    recover_reconciliations,
)
from kvault.core.validation import ErrorCode, audit_kb, error_response, success_response

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
    from kvault.core.migration import current_schema_version

    info = ops.get_kb_info(root)
    info["version"] = __version__
    info["schema_version"] = current_schema_version(root)
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
            "Root-bound kvault journal-first tools. Capture immutable evidence before "
            f"reconciling semantic state. This server can only access {bound_root}."
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

    @server.tool(name="kvault_capture")
    def kvault_capture(
        content: str,
        source: str,
        source_ref: Optional[str] = None,
        occurred_at: Optional[str] = None,
        tags: Optional[List[str]] = None,
        sensitivity: str = "personal",
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Capture immutable evidence before semantic routing."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            from kvault.core.events import capture_event

            result = capture_event(
                root,
                content=content,
                source=source,
                source_ref=source_ref,
                occurred_at=occurred_at,
                tags=tags or [],
                sensitivity=sensitivity,
            )
            payload: Dict[str, Any] = result.model_dump(mode="json")
            return success_response(payload)
        except Exception as exc:
            return error_response(ErrorCode.WORKFLOW_ERROR, str(exc))

    @server.tool(name="kvault_events_list")
    def kvault_events_list(
        status: Optional[str] = None, kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """List captured events, optionally filtered by lifecycle state."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            from kvault.core.events import list_events

            records = list_events(root, status=status)
            serialized = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in records
            ]
            return success_response({"events": serialized, "count": len(serialized)})
        except Exception as exc:
            return error_response(ErrorCode.WORKFLOW_ERROR, str(exc))

    @server.tool(name="kvault_events_show")
    def kvault_events_show(event_id: str, kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Read one captured event."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            from kvault.core.events import derive_event_states, get_event

            event = get_event(root, event_id)
            if event is None:
                return error_response(ErrorCode.NOT_FOUND, f"Event not found: {event_id}")
            payload: Dict[str, Any] = event.model_dump(mode="json")
            state = derive_event_states(root).get(event_id, "pending")
            payload["state"] = (
                state.model_dump(mode="json") if hasattr(state, "model_dump") else state
            )
            return success_response({"event": payload})
        except Exception as exc:
            return error_response(ErrorCode.WORKFLOW_ERROR, str(exc))

    @server.tool(name="kvault_events_import")
    def kvault_events_import(
        input_path: str,
        processed_path: Optional[str] = None,
        input_format: str = "moss-capture",
        dry_run: bool = True,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Import legacy memory-candidate JSONL without mutating source files."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        if input_format != "moss-capture":
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "input_format must be moss-capture",
            )
        try:
            from kvault.core.migration import import_moss_capture

            result = import_moss_capture(
                root,
                input_path,
                processed_path,
                dry_run=dry_run,
            )
            return result.model_dump(mode="json")
        except Exception as exc:
            return error_response(ErrorCode.WORKFLOW_ERROR, str(exc))

    @server.tool(name="kvault_migrate")
    def kvault_migrate(
        dry_run: bool = True,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Preview or apply the explicit kvault 0.12 schema migration."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            from kvault.core.migration import migrate

            return migrate(root, dry_run=dry_run).model_dump(mode="json")
        except Exception as exc:
            return error_response(ErrorCode.WORKFLOW_ERROR, str(exc))

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

    @server.tool(name="kvault_tree")
    def kvault_tree(
        path: str = ".",
        depth: Optional[int] = None,
        max_children: int = 20,
        gist: bool = False,
        format: str = "text",
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Annotated outline of the node tree — orient here before reading.

        Shows titles, child/descendant counts, and most-recent activity per
        node, with explicit markers for anything pruned by depth or
        max_children. Text format is the cheapest full-tree view.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        if format not in {"text", "json"}:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "format must be one of: text, json",
            )
        outline = ops.build_outline(
            root, path=path, depth=depth, max_children=max_children, include_gist=gist
        )
        if outline is None:
            return error_response(ErrorCode.NOT_FOUND, f"Node not found: {path}")
        counts = ops.outline_counts(outline)
        rendered: Any = ops.render_outline_text(outline) if format == "text" else outline
        return success_response(
            {
                "path": outline["path"],
                "total_nodes": counts["total_nodes"],
                "shown_nodes": counts["shown_nodes"],
                "outline": rendered,
            }
        )

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

    @server.tool(name="kvault_reconcile_prepare")
    def kvault_reconcile_prepare(
        event_ids: List[str],
        paths: Optional[List[str]] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Load evidence, policy, bounded orientation, and requested node revisions."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            return prepare_reconciliation(root, event_ids, paths=paths)
        except Exception as exc:
            details = exc.details if isinstance(exc, ReconciliationError) else None
            return error_response(ErrorCode.WORKFLOW_ERROR, str(exc), details=details)

    @server.tool(name="kvault_reconcile_apply")
    def kvault_reconcile_apply(
        plan: Dict[str, Any], kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Policy-check and atomically apply a complete reconciliation plan."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            result = apply_reconciliation(root, ReconciliationPlan.model_validate(plan))
            return result.model_dump(mode="json")
        except (ReconciliationError, ValueError) as exc:
            details = exc.details if isinstance(exc, ReconciliationError) else None
            return error_response(ErrorCode.WORKFLOW_ERROR, str(exc), details=details)

    @server.tool(name="kvault_reconcile_approve")
    def kvault_reconcile_approve(
        reconciliation_id: str,
        actor: str,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply an unchanged review-gated plan after explicit human approval."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            return approve_reconciliation(root, reconciliation_id, actor).model_dump(mode="json")
        except Exception as exc:
            details = exc.details if isinstance(exc, ReconciliationError) else None
            return error_response(ErrorCode.WORKFLOW_ERROR, str(exc), details=details)

    @server.tool(name="kvault_reconcile_status")
    def kvault_reconcile_status(
        reconciliation_id: str, kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Inspect immutable and operational reconciliation state."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            return reconciliation_status(root, reconciliation_id)
        except Exception as exc:
            details = exc.details if isinstance(exc, ReconciliationError) else None
            code = (
                ErrorCode.NOT_FOUND
                if isinstance(exc, ReconciliationError) and exc.code == "reconciliation_not_found"
                else ErrorCode.WORKFLOW_ERROR
            )
            return error_response(code, str(exc), details=details)

    @server.tool(name="kvault_reconcile_recover")
    def kvault_reconcile_recover(kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Recover interrupted transactions without clearing a live lock."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            return recover_reconciliations(root)
        except Exception as exc:
            details = exc.details if isinstance(exc, ReconciliationError) else None
            return error_response(ErrorCode.WORKFLOW_ERROR, str(exc), details=details)

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
        result = audit_kb(root)
        return {"success": result["valid"], **result}

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
