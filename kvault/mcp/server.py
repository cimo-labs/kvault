"""Thin MCP compatibility server for kvault.

The server is intentionally root-bound: each process can operate on exactly one
knowledge base root supplied by ``--kb-root`` or ``KVAULT_KB_ROOT``.
"""

from __future__ import annotations

import os
import time
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

from kvault.core import notes as nt
from kvault.core import operations as ops
from kvault.core.check import DEFAULT_MAX_CHILDREN, DEFAULT_MAX_FINDINGS, run_checks
from kvault.core.plan import DEFAULT_LIMIT, build_plan
from kvault.core.summary_quality import DEFAULT_MAX_DATED_SECTIONS
from kvault.core.search import KINDS
from kvault.core.daily_artifacts import generate_daily_artifact, parse_iso_date
from kvault.core.observability import ObservabilityLogger
from kvault.core.oplog import OpLog, oplog_disabled, resolve_session_id
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


def _status_payload(root: Path, include_root_summary: bool = False) -> Dict[str, Any]:
    info = ops.get_kb_info(root, include_root_summary=include_root_summary)
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
            "Root-bound kvault compatibility tools. This server can only access "
            f"{bound_root}. Results carry a `notes` array reporting decisions "
            "kvault made on your behalf (autofilled metadata, no-op writes, "
            "half-failures, truncation); read it before acting on the payload."
        ),
    )

    # ONE session per server process. Constructing a logger per tool call
    # minted a fresh session id every time — a production DB accumulated 146
    # rows across 61 "sessions", making every session-scoped query useless.
    server_session = resolve_session_id()
    _phase_logger: List[ObservabilityLogger] = []

    def _session_logger() -> ObservabilityLogger:
        if not _phase_logger:
            _phase_logger.append(
                ObservabilityLogger(bound_root / ".kvault" / "logs.db", session_id=server_session)
            )
        return _phase_logger[0]

    def _record(op: str, result: Dict[str, Any], started: float) -> None:
        """Append a successful mutation to the durable ops log.

        Failure-proof and off the write path: the KB mutation has already
        committed by the time this runs, and OpLog.append never raises. A
        failed append surfaces as a ``skipped`` note — same contract as the
        CLI — so the model knows the durable record is incomplete.
        """
        if not (isinstance(result, dict) and result.get("success")):
            return
        ok = OpLog(bound_root, session_id=server_session).append(
            op=op,
            result=result,
            ms=(time.monotonic() - started) * 1000.0,
            surface="mcp",
        )
        if not ok and not oplog_disabled():
            nt.attach_note(
                result,
                nt.note(
                    "skipped",
                    "operation log unavailable (.kvault/logs.db unwritable or corrupt) — "
                    "the KB operation itself succeeded",
                    next_step="inspect .kvault/logs.db; deleting it lets kvault recreate it",
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
    def kvault_status(
        kg_root: Optional[str] = None, include_root_summary: bool = False
    ) -> Dict[str, Any]:
        """Show KB status (version, hierarchy, counts). `root_summary` is opt-in."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return _status_payload(root, include_root_summary=include_root_summary)

    @server.tool(name="kvault_read_entity")
    def kvault_read_entity(
        path: str, parents: str = "none", kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Read an entity; parents='immediate' adds the parent summary for sibling context."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        if parents not in {"none", "immediate", "all"}:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "parents must be one of: none, immediate, all",
            )
        result = ops.read_entity(root, path, parents=parents)
        if result is None:
            return error_response(ErrorCode.NOT_FOUND, f"Entity not found: {path}")
        return success_response(result)

    @server.tool(name="kvault_read_node")
    def kvault_read_node(
        path: str,
        parents: str = "none",
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read a node; parents='immediate'|'all' adds parent context (off by default since 0.14)."""
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

    def _strip_ancestors(result: Dict[str, Any], ancestors: str) -> Dict[str, Any]:
        """ancestors='paths' (the default since 0.14.0) keeps ancestor_paths and
        drops the full documents.

        `ancestors[].current_content` can exceed 45,000 characters on a mature
        KB — 90% of a write result. Pass ancestors='content' to get the
        documents inline, or fetch them with kvault_get_parent_summaries.
        """
        if ancestors == "paths" and isinstance(result, dict) and result.get("success"):
            result.pop("ancestors", None)
        return result

    @server.tool(name="kvault_write_entity")
    def kvault_write_entity(
        path: str,
        content: str,
        meta: Optional[Dict[str, Any]] = None,
        create: bool = False,
        reasoning: Optional[str] = None,
        journal_source: Optional[str] = None,
        ancestors: str = "paths",
        new_root: bool = False,
        allow_similar: bool = False,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update an entity.

        Read `notes` in the result before proceeding: kvault AUTOFILLS missing
        frontmatter (`source`, `aliases`, `name`) rather than failing — an
        `autofilled` note means the provenance on disk is invented, not
        supplied. `changed: false` means the write was a detected no-op.
        A `partial` note means the node was written but a linked step (event
        promotion) FAILED and needs manual repair. Then rewrite the returned
        ancestor summaries (`kvault_update_summaries`). ancestors='paths'
        (default) omits the bulky `ancestors[].current_content`; pass
        ancestors='content' to inline it (see kvault_write_node).
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        if ancestors not in {"content", "paths"}:
            return error_response(
                ErrorCode.VALIDATION_ERROR, "ancestors must be one of: content, paths"
            )
        started = time.monotonic()
        result = ops.write_entity(
            root,
            path,
            content,
            meta=meta,
            create=create,
            reasoning=reasoning,
            journal_source=journal_source,
            default_source="auto:mcp",
            new_root=new_root,
            allow_similar=allow_similar,
        )
        _record("write", result, started)
        return _strip_ancestors(result, ancestors)

    @server.tool(name="kvault_write_node")
    def kvault_write_node(
        path: str,
        content: str,
        meta: Optional[Dict[str, Any]] = None,
        create: bool = False,
        reasoning: Optional[str] = None,
        journal_source: Optional[str] = None,
        ancestors: str = "paths",
        new_root: bool = False,
        allow_similar: bool = False,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update any node summary.

        A create is refused when it would add a root category (pass
        new_root=true deliberately) or collide with a sibling of the same
        words (allow_similar=true to override); near-duplicate names and
        over-ceiling parents come back as `structure` notes, and missing
        intermediate parents are stubbed (a `created` note) rather than
        left as invisible directories.

        The result narrates kvault's decisions BEFORE the bulk payload:
        `did` (one line), `notes` (autofilled metadata, no-op detection,
        half-failed event promotion, lock contention), `changed`,
        `propagation_required`, and `ancestor_paths` all precede `ancestors`.
        Act on `notes` first — a `partial` note means part of the operation
        failed even though success=true. Since 0.14.0 `ancestors` (the full
        documents) is omitted by default; pass ancestors='content' to inline
        it, or read the chain with kvault_get_parent_summaries.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        if ancestors not in {"content", "paths"}:
            return error_response(
                ErrorCode.VALIDATION_ERROR, "ancestors must be one of: content, paths"
            )
        started = time.monotonic()
        result = ops.write_node(
            root,
            path,
            content,
            meta=meta,
            create=create,
            reasoning=reasoning,
            journal_source=journal_source,
            default_source="auto:mcp",
            new_root=new_root,
            allow_similar=allow_similar,
        )
        _record("write", result, started)
        return _strip_ancestors(result, ancestors)

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
        collapse: bool = True,
        kind: Optional[str] = None,
        path_prefix: Optional[str] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search visible node summaries.

        The result reports its own blind spots: `total_matched` vs `count`
        when `limit` cut the list, per-result `content_omitted_reason`
        (content_max_chars | total_budget_exhausted | empty_node), `collapsed`
        / `collapsed_paths` for ancestor hits that only repeated a descendant's
        match (collapse=False keeps them), and `notes` for unreadable files
        that were skipped. `kind` is a comma-separated subset of
        root,category,entity; `path_prefix` restricts to a subtree. CAUTION:
        the char budget applies only to `content` — `parents != "none"`
        attaches full parent documents OUTSIDE any budget and can dwarf the
        results.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        if parents not in {"none", "immediate", "all"}:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "parents must be one of: none, immediate, all",
            )
        kinds = [k.strip() for k in (kind or "").split(",") if k.strip()] or None
        if kinds and any(k not in KINDS for k in kinds):
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                "kind must be a comma-separated subset of: " + ", ".join(KINDS),
            )
        result = ops.search_nodes(
            root,
            query=query,
            limit=limit,
            include_content=include_content,
            collapse=collapse,
            kinds=kinds,
            path_prefix=path_prefix,
        )
        if parents != "none":
            for item in result["results"]:
                item["node"] = ops.read_node(root, item["path"], parents=parents)
        return success_response(result)

    @server.tool(name="kvault_delete_entity")
    def kvault_delete_entity(path: str, kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Delete an entity directory — DESTRUCTIVE and unconfirmed over MCP.

        Deletes the entire subtree. The result reports `nodes_deleted` /
        `files_deleted` and lists the now-stale ancestor summaries
        (`propagation_required`, `ancestor_paths`) — rewrite them next or
        they keep describing nodes that no longer exist.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        started = time.monotonic()
        result = ops.delete_entity(root, path)
        _record("delete", result, started)
        return result

    @server.tool(name="kvault_move_entity")
    def kvault_move_entity(
        source_path: str,
        target_path: str,
        new_root: bool = False,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Move an entity to a new path.

        BOTH ancestor chains are stale afterwards (`ancestors_source`,
        `ancestors_target`): the source chain still describes the moved
        subtree, the target chain doesn't describe it yet. Rewrite both.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        started = time.monotonic()
        result = ops.move_entity(root, source_path, target_path, new_root=new_root)
        _record("move", result, started)
        return result

    @server.tool(name="kvault_move_entities")
    def kvault_move_entities(
        moves: List[Dict[str, str]],
        new_root: bool = False,
        dry_run: bool = False,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Move several nodes under one lock: `moves` is a list of {from, to}.

        Every move is validated before any runs. The result carries one
        combined `ancestor_paths` list; a `partial` note names what moved
        and what did not if a move fails mid-batch. This is the shape
        `kvault_plan` emits for a cluster.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        started = time.monotonic()
        result = ops.move_entities(root, moves, new_root=new_root, dry_run=dry_run)
        if not dry_run:
            _record("move-batch", result, started)
        return result

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
        """Write a summary file — UNGUARDED; prefer kvault_write_parent_summary.

        This tool has NO stale-children protection: it will happily overwrite
        a parent rollup composed from children you never read. For parent
        summaries use kvault_prepare_summary_update → kvault_write_parent_summary
        instead. Two more traps the result's `notes` will flag: a typo'd path
        CREATES a new subtree (`created` note), and passing `meta` REPLACES
        the existing frontmatter wholesale (`removed` note lists dropped keys)
        — omit `meta` to preserve it.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        started = time.monotonic()
        result = ops.write_summary(root, path, content, meta=meta)
        _record("write-summary", result, started)
        return result

    @server.tool(name="kvault_prepare_summary_update")
    def kvault_prepare_summary_update(
        path: str,
        children: str = "auto",
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read a parent summary and its direct children for a strict update.

        `children` is 'auto' (full bodies up to the direct-child ceiling,
        gists above it — a `truncated` note says which), 'content', or
        'gist'. The digest always covers full content.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return ops.prepare_summary_update(root, path, children=children)

    @server.tool(name="kvault_write_parent_summary")
    def kvault_write_parent_summary(
        path: str,
        content: str,
        children_digest: str,
        meta: Optional[Dict[str, Any]] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write one parent summary after verifying direct children were read."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        started = time.monotonic()
        result = ops.write_parent_summary(root, path, content, children_digest, meta=meta)
        _record("write-parent-summary", result, started)
        return result

    @server.tool(name="kvault_update_summaries")
    def kvault_update_summaries(
        updates: List[Dict[str, Any]], kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Batch-update summaries.

        CAUTION: `success: true` means the BATCH ran, not that every item
        succeeded — check `partial`, `failed`, and `errors[]`. Per-item
        decisions are collapsed by code in `notes` ({code, count, examples}).
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        started = time.monotonic()
        result = ops.update_summaries(root, updates)
        _record("update-summaries", result, started)
        return result

    @server.tool(name="kvault_get_parent_summaries")
    def kvault_get_parent_summaries(
        path: str, ancestors: str = "content", kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get ancestor summaries for propagation.

        ancestors='paths' returns `{path, has_meta}` per ancestor and
        `ancestor_paths` (the full chain's content can exceed 100 KB).
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        if ancestors not in {"content", "paths"}:
            return error_response(
                ErrorCode.VALIDATION_ERROR, "ancestors must be one of: content, paths"
            )
        return ops.get_ancestors(root, path, include_content=(ancestors == "content"))

    @server.tool(name="kvault_get_ancestors")
    def kvault_get_ancestors(
        path: str, ancestors: str = "content", kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Alias for kvault_get_parent_summaries."""
        return kvault_get_parent_summaries(path=path, ancestors=ancestors, kg_root=kg_root)

    @server.tool(name="kvault_propagate_all")
    def kvault_propagate_all(
        path: str, ancestors: str = "content", kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Compatibility alias returning all summary propagation targets."""
        return kvault_get_parent_summaries(path=path, ancestors=ancestors, kg_root=kg_root)

    @server.tool(name="kvault_write_journal")
    def kvault_write_journal(
        actions: List[Dict[str, Any]],
        source: str,
        date: Optional[str] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write a journal entry.

        An unparseable `date` does not fail: the entry files under today and
        the result carries a `guessed` note saying so.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        started = time.monotonic()
        result = ops.write_journal(root, actions=actions, source=source, date=date)
        _record("journal", result, started)
        return result

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

    @server.tool(name="kvault_check")
    def kvault_check(
        threshold_minutes: int = 5,
        summary_quality: bool = True,
        pending_max_age: int = 7,
        max_children: int = DEFAULT_MAX_CHILDREN,
        max_findings: int = DEFAULT_MAX_FINDINGS,
        summary_max_words: Optional[int] = None,
        summary_max_dated_sections: int = DEFAULT_MAX_DATED_SECTIONS,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the maintenance checks (the CLI's `kvault check`, one document).

        `success` is false only for hard findings (PROPAGATE, LOG, WRITE,
        BRANCH). `findings` is the unified list, hard first; SUMMARY,
        PENDING, RETRACTED, GHOST, SIBLINGS, LOOSE and JOURNAL are warn-only
        maintenance work. Lists are capped at `max_findings` per code with
        the hidden counts in `truncated`. `kvault_validate_kb` checks
        integrity only; this is the one that says whether the tree is rotting.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return run_checks(
            root,
            threshold_minutes=threshold_minutes,
            summary_quality=summary_quality,
            pending_max_age=pending_max_age,
            max_children=max_children,
            max_findings=max_findings,
            max_words=summary_max_words,
            max_dated_sections=summary_max_dated_sections,
        )

    @server.tool(name="kvault_plan")
    def kvault_plan(
        path: Optional[str] = None,
        limit: int = DEFAULT_LIMIT,
        max_children: int = DEFAULT_MAX_CHILDREN,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Maintenance worklist in leverage order, with commands and moves.

        Cluster items carry a `moves` list you can pass straight to
        `kvault_move_entities`; `questions` are the judgment calls to answer
        from the summaries (defer only when the evidence is not there). Never
        applies anything.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return build_plan(root, path=path, limit=limit, max_children=max_children)

    @server.tool(name="kvault_validate_kb")
    def kvault_validate_kb(kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Validate KB integrity (frontmatter, placeholders, ghost directories)."""
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
        """Log an observability phase to .kvault/logs.db.

        One server process is one session — repeated calls share a session id.
        (Mutating tools log themselves automatically now; this tool is for
        extra agent-side reasoning you want on the durable record.)
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            logger = _session_logger()
            logger.log(phase, data)
        except ValueError as exc:
            return error_response(ErrorCode.VALIDATION_ERROR, str(exc))
        except Exception as exc:
            # sqlite errors (unwritable dir, corrupt DB, readonly mount) used
            # to escape as an unhandled ToolError. Logging must degrade, not
            # crash the tool surface.
            return error_response(
                ErrorCode.SYSTEM_ERROR,
                f"observability log unavailable: {exc}",
                hint="Check .kvault/ permissions; deleting logs.db lets kvault recreate it.",
            )
        return success_response({"session_id": logger.session_id, "phase": phase})

    @server.tool(name="kvault_log_tail")
    def kvault_log_tail(
        limit: int = 20,
        session: Optional[str] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Show recent KB operations and the decisions they reported.

        Reads the durable ops log (newest first): op, path, `did`, notes,
        changed/partial flags, and session — use it to see what this KB's
        other agents and sessions did recently.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        rows = OpLog(root, session_id=server_session).tail(limit=limit, session=session)
        return success_response({"count": len(rows), "ops": rows})

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
