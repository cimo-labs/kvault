"""Thin MCP compatibility server for kvault.

The server is intentionally root-bound: each process can operate on exactly one
knowledge base root supplied by ``--kb-root`` or ``KVAULT_KB_ROOT``.
"""

from __future__ import annotations

import functools
import inspect
import os
import time
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import click
import pydantic_core
from pydantic import BaseModel, ConfigDict

from kvault.core import events as ev
from kvault.core import notes as nt
from kvault.core import operations as ops
from kvault.core.check import (
    DEFAULT_MAX_CHILDREN,
    DEFAULT_MAX_FINDINGS,
    normalize_codes,
    run_checks,
)
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
#: Enums in the tool schemas, so an MCP-only agent sees the allowed values.
ParentsMode = Literal["none", "gist", "immediate", "all"]
EventAction = Literal["list", "show", "resolve", "retract"]
#: "promoted" is not offered: promotion is a node write (kvault_write_node
#: event_ids), which stamps provenance; resolving as promoted by hand did not.
EventOutcome = Literal["journal_only", "duplicate", "no_op", "rejected"]
EventStatus = Literal["pending", "resolved", "retracted"]
BatchParentsMode = Literal["none", "gist"]


class Patch(BaseModel):
    """One exact edit to a body: ``old_str`` must occur in it exactly once."""

    model_config = ConfigDict(extra="forbid")
    old_str: str
    new_str: str


class SummaryUpdate(BaseModel):
    """One kvault_update_summaries item: the new body (``content``) or ``patches``.

    ``meta`` merges onto the existing frontmatter; a null value deletes a key.
    """

    model_config = ConfigDict(extra="forbid")
    path: str
    content: Optional[str] = None
    patches: Optional[List[Patch]] = None
    meta: Optional[Dict[str, Any]] = None


_NOT_UTF8 = "could not be read (not UTF-8, or no permission); check its _summary.md"


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


def _serialize_daily_result(
    root: Path, result: Any, include_content: bool = True
) -> Dict[str, Any]:
    payload = {
        "artifact_date": result.artifact_date.isoformat(),
        "path": str(result.path),
        "relative_path": str(result.path.relative_to(root)),
        "content_chars": len(result.content),
        "written": result.written,
    }
    if include_content:
        payload["content"] = result.content
    return success_response(payload)


def _compact_text(fn: Any) -> Any:
    """Wrap a tool so its result reaches the client as compact JSON text (0.17).

    FastMCP serializes a dict result with two-space indentation and sends a
    second, structured copy beside it. Indentation alone put 9 to 12 of 30
    default searches over the ~4 KB many clients inline, on copies of two
    real KBs; compact text put none over. The wrapper returns a string, and
    the tool registers without structured output where FastMCP supports it,
    so the client gets one compact copy.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        result = fn(*args, **kwargs)
        if isinstance(result, str):
            return result
        # pydantic_core, as FastMCP itself uses: compact by default, and it
        # writes date-typed YAML keys (`2026-09-30: joined`), which json.dumps
        # refuses, plus ISO datetimes and sets.
        return pydantic_core.to_json(result, fallback=str).decode()

    # Evaluated here: FastMCP resolves this module's string annotations itself,
    # but takes an explicit __signature__ as it stands.
    wrapper.__signature__ = inspect.signature(  # type: ignore[attr-defined]
        fn, eval_str=True
    ).replace(return_annotation=str)
    return wrapper


#: Smallest outline budget kvault_tree accepts (0 means no limit).
TREE_MIN_CHARS = 100


def _outline_depth(outline: Dict[str, Any]) -> int:
    """Levels below the outline's root that the rendered outline shows."""
    children = outline.get("children") or []
    return 1 + max(_outline_depth(child) for child in children) if children else 0


#: Epoch-1 tools superseded by the node tools; registered only on request (0.17).
LEGACY_TOOLS = (
    "kvault_init",
    "kvault_read_entity",
    "kvault_write_entity",
    "kvault_list_entities",
    "kvault_write_summary",
    "kvault_get_ancestors",
    "kvault_propagate_all",
    "kvault_log_phase",
)
KVAULT_MCP_LEGACY_TOOLS_ENV = "KVAULT_MCP_LEGACY_TOOLS"


def _forbid_unknown_arguments(server: Any) -> None:
    """Reject argument names a tool does not have (0.17).

    FastMCP drops unknown keys, so `budget=2000` on kvault_read_nodes ran at
    the default budget without a word. Each tool's argument model now
    forbids extra keys: the call fails with a validation error naming the
    argument, and the schema says additionalProperties: false. Best effort
    against FastMCP internals: if they change, tools keep working leniently.
    """
    tools = getattr(getattr(server, "_tool_manager", None), "_tools", None) or {}
    for tool in tools.values():
        model = getattr(getattr(tool, "fn_metadata", None), "arg_model", None)
        if model is None:
            continue
        try:
            model.model_config["extra"] = "forbid"
            model.model_rebuild(force=True)
            tool.parameters = _inline_refs(model.model_json_schema(by_alias=True))
        except Exception:  # pragma: no cover - depends on the installed mcp
            continue


def _inline_refs(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Replace ``$ref``s to ``$defs`` with the definitions themselves (0.17).

    The typed patch and update items are the first nested models in kvault's
    tool schemas, and some function-calling clients do not resolve ``$ref``.
    The models are not recursive, so inlining terminates.
    """
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/") and ref[8:] in defs:
                rest = {k: resolve(v) for k, v in node.items() if k != "$ref"}
                return {**resolve(defs[ref[8:]]), **rest}
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    resolved: Dict[str, Any] = resolve(schema)
    return resolved


def create_server(kb_root: Path | str, legacy_tools: Optional[bool] = None) -> Any:
    """Create a FastMCP server bound to *kb_root*.

    The epoch-1 tools in ``LEGACY_TOOLS`` are registered only with
    *legacy_tools* (or ``KVAULT_MCP_LEGACY_TOOLS=1``): clients that load
    every schema spent calls reading 30 of them and mixed both generations
    in one turn.
    """
    if FastMCP is None:
        raise click.ClickException(
            "MCP dependencies not installed. Run: pip install 'knowledgevault[mcp]'"
        )

    bound_root = resolve_bound_root(kb_root)
    if legacy_tools is None:
        legacy_tools = os.environ.get(KVAULT_MCP_LEGACY_TOOLS_ENV, "").lower() in (
            "1",
            "true",
            "yes",
        )
    server = FastMCP(
        "kvault",
        instructions=(
            "Root-bound kvault compatibility tools. This server can only access "
            f"{bound_root}. Results carry a `notes` array reporting decisions "
            "kvault made on your behalf (autofilled metadata, no-op writes, "
            "half-failures, truncation); read it before acting on the payload. "
            "Keep reads small: kvault_search returns compact hits by default "
            "(parents='gist' adds where each hit sits for about 2 KB); read the "
            "hits you picked with one kvault_read_nodes call; kvault_check with "
            "codes=[...] and max_findings=0 returns one finding code's full list."
        ),
    )

    unstructured = (
        {"structured_output": False}
        if "structured_output" in inspect.signature(server.tool).parameters
        else {}
    )

    def _tool(name: str, register: bool = True) -> Any:
        """Register *fn* as a compact-text tool; the closure keeps the dict-
        returning function, so tools that call each other still get dicts."""

        def decorator(fn: Any) -> Any:
            if register:
                server.tool(name=name, **unstructured)(_compact_text(fn))
            return fn

        return decorator

    def _legacy_tool(name: str) -> Any:
        return _tool(name, register=bool(legacy_tools))

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

    @_legacy_tool("kvault_init")
    def kvault_init(kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Return bound-root status and reject mismatched roots."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return _status_payload(root)

    @_tool("kvault_status")
    def kvault_status(
        kg_root: Optional[str] = None, include_root_summary: bool = False
    ) -> Dict[str, Any]:
        """Show KB status (version, hierarchy, counts). `root_summary` is opt-in."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return _status_payload(root, include_root_summary=include_root_summary)

    _PARENTS_ERROR = "parents must be one of: " + ", ".join(ops.PARENTS_MODES)

    @_legacy_tool("kvault_read_entity")
    def kvault_read_entity(
        path: str, parents: ParentsMode = "none", kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Read an entity; parents='gist' adds each ancestor's path, title, and one line;
        'immediate' adds the parent's full summary."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        if parents not in ops.PARENTS_MODES:
            return error_response(ErrorCode.VALIDATION_ERROR, _PARENTS_ERROR)
        try:
            result = ops.read_entity(root, path, parents=parents)
        except (OSError, UnicodeDecodeError, ValueError):
            return error_response(ErrorCode.VALIDATION_ERROR, f"{path} {_NOT_UTF8}")
        if result is None:
            return error_response(ErrorCode.NOT_FOUND, f"Entity not found: {path}")
        return success_response(result)

    @_tool("kvault_read_node")
    def kvault_read_node(
        path: str,
        parents: ParentsMode = "none",
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read a node. parents='gist' adds every ancestor as {path, title, gist} (a few
        hundred bytes); 'immediate'|'all' add full parent documents (can be tens of KB).
        To read several nodes use kvault_read_nodes."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        if parents not in ops.PARENTS_MODES:
            return error_response(ErrorCode.VALIDATION_ERROR, _PARENTS_ERROR)
        try:
            result = ops.read_node(root, path, parents=parents)
        except (OSError, UnicodeDecodeError, ValueError):
            return error_response(ErrorCode.VALIDATION_ERROR, f"{path} {_NOT_UTF8}")
        if result is None:
            return error_response(ErrorCode.NOT_FOUND, f"Node not found: {path}")
        return success_response(result)

    @_tool("kvault_read_nodes")
    def kvault_read_nodes(
        paths: List[str],
        parents: BatchParentsMode = "none",
        total_max_chars: Optional[int] = None,
        max_total_chars: Optional[int] = None,
        budget: Optional[int] = None,
        max_chars: Optional[int] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read up to 25 nodes in one call, under one shared budget (default 3,500).

        The budget counts whole nodes as compact JSON (content, meta, child
        paths), sized so the result fits clients that inline about 4 KB of
        tool output; raise it when you can take more. `max_total_chars`
        (the CLI flag's name), `budget` and `max_chars` are accepted as
        aliases of total_max_chars. A node past the budget comes back cut
        (`content_truncated`); one whose metadata alone does not fit is listed
        in `omitted`; a `truncated` note names both. Each node: path, kind,
        title, meta, content, up to 50 child paths (`children_count` past
        that), and with parents='gist' its ancestors as {path, title, gist}.
        Full parent documents ('immediate'|'all') are one node at a time:
        kvault_read_node. Paths that are not nodes are listed in `missing`.
        Use it after a search: pick the hits, read them together.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        chosen = next(
            (v for v in (total_max_chars, max_total_chars, budget, max_chars) if v is not None),
            ops.READ_NODES_MCP_MAX_CHARS,
        )
        return ops.read_nodes(root, paths, parents=parents, total_max_chars=chosen)

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

    def _ancestors_error(ancestors: str) -> Optional[Dict[str, Any]]:
        if ancestors in {"content", "paths"}:
            return None
        return error_response(
            ErrorCode.VALIDATION_ERROR, "ancestors must be one of: content, paths"
        )

    @_legacy_tool("kvault_write_entity")
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

    @_tool("kvault_write_node")
    def kvault_write_node(
        path: str,
        content: str = "",
        meta: Optional[Dict[str, Any]] = None,
        create: bool = False,
        reasoning: Optional[str] = None,
        journal_source: Optional[str] = None,
        ancestors: str = "paths",
        new_root: bool = False,
        allow_similar: bool = False,
        event_ids: Optional[List[str]] = None,
        patches: Optional[List[Patch]] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update any node summary.

        Pass the body in `content`, or edit an existing node with `patches`:
        `[{old_str, new_str}]`, each old_str matching the current body
        (without frontmatter) exactly once, applied in order; any miss writes
        nothing. A patch sends only what changes instead of the whole body.

        `event_ids` promotes captured events (kvault_capture) into this node in
        the same write: each must be pending, the node's source_refs gain
        `journal:<id>`, and the events resolve as promoted to this path.

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
        # `content` is typed str, not Optional[str]: FastMCP parses a string
        # argument of any other type as JSON first, so a body such as
        # '["step 1"]' was refused and "null" read as no content.
        result = ops.write_node(
            root,
            path,
            None if patches is not None and content == "" else content,
            meta=meta,
            create=create,
            reasoning=reasoning,
            journal_source=journal_source,
            default_source="auto:mcp",
            new_root=new_root,
            allow_similar=allow_similar,
            event_ids=event_ids,
            patches=[p.model_dump() for p in patches] if patches is not None else None,
        )
        _record("write", result, started)
        return _strip_ancestors(result, ancestors)

    @_legacy_tool("kvault_list_entities")
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

    @_tool("kvault_list_nodes")
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

    @_tool("kvault_tree")
    def kvault_tree(
        path: str = ".",
        depth: Optional[int] = None,
        max_children: int = 20,
        gist: bool = False,
        format: str = "text",
        max_chars: int = 3500,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Annotated outline of the node tree — orient here before reading.

        Shows titles, child/descendant counts, and most-recent activity per
        node, with explicit markers for anything pruned by depth or
        max_children. Text format is the cheapest full-tree view.

        The outline stays under max_chars (default 3,500; 0 = no limit;
        otherwise at least 100), counted as the client receives it: it is
        shown at the deepest depth that fits (the requested one, then 3, 2,
        1), and a text outline is cut at a line past that. A `truncated` note
        gives the depth shown; drill into a branch with path=<node>.
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
        if max_chars != 0 and max_chars < TREE_MIN_CHARS:
            return error_response(
                ErrorCode.VALIDATION_ERROR,
                f"max_chars must be 0 (no limit) or at least {TREE_MIN_CHARS}",
            )

        def size_of(rendered: Any) -> int:
            # As serialized for the client: escaped newlines and quotes count.
            return len(pydantic_core.to_json(rendered).decode()) - (
                2 if isinstance(rendered, str) else 0
            )

        def render(level: Optional[int]) -> Tuple[Any, Any, int]:
            tree = ops.build_outline(
                root, path=path, depth=level, max_children=max_children, include_gist=gist
            )
            if tree is None:
                return None, None, 0
            text: Any = ops.render_outline_text(tree) if format == "text" else tree
            return tree, text, size_of(text)

        outline, rendered, size = render(depth)
        if outline is None:
            return error_response(ErrorCode.NOT_FOUND, f"Node not found: {path}")
        notes: List[Dict[str, Any]] = []
        if max_chars and size > max_chars:
            # A root outline at depth 2 was ~10 KB on a 700-node KB and an
            # unbounded one ~40 KB on 500 nodes: past ~4 KB many clients write
            # the result to a file, and agents oriented on nothing (0.17).
            full = size
            reached = _outline_depth(outline)
            shown = reached if depth is None else min(depth, reached)
            for level in (3, 2, 1):
                if level >= reached or (depth is not None and level >= depth):
                    continue  # the same outline again
                outline, rendered, size = render(level)
                shown = level
                if size <= max_chars:
                    break
            cut = 0
            if size > max_chars and isinstance(rendered, str):
                lines = rendered.splitlines()
                kept: List[str] = []
                used = size_of(f"\n… (+{len(lines)} more lines)")  # room for the marker
                for line in lines:
                    step = size_of(line) + (2 if kept else 0)  # "\\n" between lines
                    if used + step > max_chars:
                        break
                    kept.append(line)
                    used += step
                cut = len(lines) - len(kept)
                rendered = "\n".join(kept) + f"\n… (+{cut} more lines)"
                size = size_of(rendered)
            over = size > max_chars  # the json format is not cut
            notes.append(
                nt.note(
                    "truncated",
                    f"outline shown at depth {shown}"
                    + (f" and cut by {cut} lines" if cut else "")
                    + (
                        f" is still {size} chars, over max_chars {max_chars}"
                        if over
                        else f" to stay under {max_chars} chars"
                    )
                    + f" (full: {full})",
                    detail={
                        "depth_shown": shown,
                        "max_chars": max_chars,
                        "full_chars": full,
                        "shown_chars": size,
                    },
                    next_step="drill down with path=<node>, or raise max_chars",
                )
            )
        counts = ops.outline_counts(outline)
        payload: Dict[str, Any] = {
            "path": outline["path"],
            "total_nodes": counts["total_nodes"],
            "shown_nodes": counts["shown_nodes"],
            "outline": rendered,
        }
        if notes:
            payload["notes"] = notes
        return success_response(payload)

    @_tool("kvault_search")
    def kvault_search(
        query: str,
        limit: int = 8,
        compact: bool = True,
        parents: ParentsMode = "none",
        include_content: bool = False,
        content_max_chars: int = 6000,
        total_max_chars: int = 20000,
        snippet_chars: Optional[int] = None,
        collapse: bool = True,
        kind: Optional[str] = None,
        path_prefix: Optional[str] = None,
        include_background: bool = False,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search visible node summaries.

        Hits are compact by default over MCP (0.16): path, title, kind,
        last_updated (frontmatter date), and a one-line snippet — under
        4 KB for the default 8 hits. compact=False adds score, matched_fields,
        summary_path, a 440-character snippet, and the collapsed-path
        lists (~9-10 KB). parents='gist' adds one shared `parents` map from
        every ancestor path of the hits to {title, gist} (~2-3 KB; a hit's
        ancestors are the prefixes of its path); 'immediate'|'all' attach
        full documents per hit only while they fit in total_max_chars (a
        `truncated` note says when). Read the hits you pick with
        kvault_read_nodes.

        The result reports its own blind spots: `total_matched` vs `count`
        when `limit` cut the list, per-result `content_omitted_reason`,
        `collapsed` (ancestor hits that only repeated a descendant's match;
        compact=False also lists them in `collapsed_paths`, collapse=False
        keeps them), and `notes` for skipped files and for loose Markdown
        files that are not searched. include_content and full parents share
        one total_max_chars budget. `kind` is a comma-separated subset of
        root,category,entity; `path_prefix` restricts to a subtree.
        A match under deep_context/ (a parked duplicate or long-form notes)
        is folded into the node that keeps it when that node matches about
        as well and is in the results; a note lists the folded paths.
        include_background=true returns them all.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        if parents not in ops.PARENTS_MODES:
            return error_response(ErrorCode.VALIDATION_ERROR, _PARENTS_ERROR)
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
            content_max_chars=content_max_chars,
            total_max_chars=total_max_chars,
            collapse=collapse,
            kinds=kinds,
            path_prefix=path_prefix,
            compact=compact,
            snippet_chars=snippet_chars,
            parents=parents,
            include_background=include_background,
        )
        return success_response(result)

    @_tool("kvault_delete_entity")
    def kvault_delete_entity(
        path: str, ancestors: str = "paths", kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Delete an entity directory — DESTRUCTIVE and unconfirmed over MCP.

        Deletes the entire subtree. The result reports `nodes_deleted` /
        `files_deleted` and lists the now-stale ancestor summaries
        (`propagation_required`, `ancestor_paths`) — rewrite them next or
        they keep describing nodes that no longer exist. `referrer_paths`
        names other nodes whose summaries still point at the deleted path.
        The ancestor documents are left out unless ancestors='content'.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        bad = _ancestors_error(ancestors)
        if bad:
            return bad
        started = time.monotonic()
        result = ops.delete_entity(root, path)
        _record("delete", result, started)
        return _strip_ancestors(result, ancestors)

    @_tool("kvault_move_entity")
    def kvault_move_entity(
        source_path: Optional[str] = None,
        target_path: Optional[str] = None,
        new_root: bool = False,
        ancestors: str = "paths",
        old_path: Optional[str] = None,
        new_path: Optional[str] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Move an entity to a new path.

        BOTH ancestor chains are stale afterwards (`ancestors_source`,
        `ancestors_target`): the source chain still describes the moved
        subtree, the target chain doesn't describe it yet. Rewrite both.
        `referrer_paths` names other nodes whose summaries still point at
        the old path; the `propagate` note carries each reference's `now_at`.
        The ancestor documents (often 30-45 KB) are left out unless
        ancestors='content'; their paths are always in `ancestor_paths`.
        `old_path`/`new_path` are accepted as aliases of source_path/target_path.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        source, target = source_path or old_path, target_path or new_path
        if not source or not target:
            return error_response(
                ErrorCode.VALIDATION_ERROR, "move needs source_path and target_path"
            )
        bad = _ancestors_error(ancestors)
        if bad:
            return bad
        started = time.monotonic()
        result = ops.move_entity(root, source, target, new_root=new_root)
        _record("move", result, started)
        return _strip_ancestors(result, ancestors)

    @_tool("kvault_move_entities")
    def kvault_move_entities(
        moves: List[Dict[str, str]],
        new_root: bool = False,
        dry_run: bool = False,
        ancestors: str = "paths",
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
        bad = _ancestors_error(ancestors)
        if bad:
            return bad
        result = ops.move_entities(root, moves, new_root=new_root, dry_run=dry_run)
        if not dry_run:
            _record("move-batch", result, started)
        return _strip_ancestors(result, ancestors)

    @_tool("kvault_read_summary")
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

    @_legacy_tool("kvault_write_summary")
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
        instead. A typo'd path CREATES a new subtree (`created` note). `meta`
        merges onto the existing frontmatter; a null value deletes that key
        (`removed` note).
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        started = time.monotonic()
        result = ops.write_summary(root, path, content, meta=meta)
        _record("write-summary", result, started)
        return result

    @_tool("kvault_mark")
    def kvault_mark(
        path: str,
        distinct_from: Optional[List[str]] = None,
        max_children: Optional[int] = None,
        series_ok: Optional[bool] = None,
        verify_by: Optional[str] = None,
        clear: bool = False,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a decision on a node so check, plan, and the guards honor it.

        Use it when a person corrects you: `distinct_from` (these are different
        things; the SIBLINGS and DUPLICATE findings and the create guard stop
        for that pair), `max_children` (this parent's own ceiling), `series_ok`
        (a deliberate chronology). Use `verify_by` whenever you record a fact
        that goes stale (pending, in review, awaiting a reply, deployed to
        staging): YYYY-MM-DD, "+14d" or "+2w", or "none" to clear once the
        fact is settled; check reports STALE once it passes. `clear` drops
        the structure decisions, not verify_by. A mark keeps the node's
        `updated` date and journals itself. Written through the normal write
        path, so it is logged.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        started = time.monotonic()
        result = ops.mark_node(
            root,
            path,
            distinct_from=distinct_from,
            max_children=max_children,
            series_ok=series_ok,
            clear=clear,
            verify_by=verify_by,
        )
        _record("mark", result, started)
        return result

    @_tool("kvault_capture")
    def kvault_capture(
        content: str,
        source: str,
        source_ref: Optional[str] = None,
        tags: Optional[List[str]] = None,
        allow_suspicious: bool = False,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Capture one coherent piece of evidence verbatim, before writing from it.

        The returned event id is the hand-off: promote it with
        kvault_write_node(event_ids=[...]), which stamps `journal:<id>`
        provenance, or close it with kvault_events(action="resolve"). One
        source record is one event. Idempotent: the same (source,
        source_ref, content) returns the existing event, and a reused
        source_ref with different content is refused as a conflict. Content
        that looks shell-mangled is refused unless allow_suspicious=true
        after you verified the wording.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        started = time.monotonic()
        result = ev.capture_event(
            root,
            body=content,
            source=source,
            source_ref=source_ref,
            tags=tags,
            allow_suspicious=allow_suspicious,
        )
        _record("capture", result, started)
        return result

    @_tool("kvault_events")
    def kvault_events(
        action: EventAction = "list",
        event_id: Optional[str] = None,
        status: Optional[EventStatus] = "pending",
        limit: int = 10,
        since: Optional[str] = None,
        outcome: Optional[EventOutcome] = None,
        note: Optional[str] = None,
        reason: Optional[str] = None,
        superseded_by: Optional[str] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List, show, resolve or retract captured events.

        - list: newest first, `status` pending (default), resolved, retracted
          or null for all; `limit` (0 = all; check total_matched); `since`
          YYYY-MM-DD.
        - show: one event with its full body (`event_id`).
        - resolve: close an event that needs no node write, with `outcome`
          (journal_only, duplicate, no_op, rejected) and a factual `note`.
          Promote into a node with kvault_write_node(event_ids=[...]) instead.
        - retract: the captured text is wrong evidence (`reason`, optional
          `superseded_by`); works on resolved events too, so nodes that cite
          it get repaired.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        if action == "list":
            return ev.list_events(root, status=status or None, limit=limit, since=since)
        if not event_id:
            return error_response(ErrorCode.VALIDATION_ERROR, f"action={action} needs event_id")
        if action == "show":
            return ev.get_event(root, event_id)
        started = time.monotonic()
        if action == "resolve":
            if not outcome:
                return error_response(
                    ErrorCode.VALIDATION_ERROR,
                    "action=resolve needs outcome: journal_only, duplicate, no_op or rejected",
                )
            result = ev.resolve_event(root, event_id, outcome, note=note)
            _record("events-resolve", result, started)
            return result
        if not reason:
            return error_response(ErrorCode.VALIDATION_ERROR, "action=retract needs reason")
        result = ev.retract_event(root, event_id, reason, superseded_by=superseded_by)
        _record("events-retract", result, started)
        return result

    @_tool("kvault_prepare_summary_update")
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

    @_tool("kvault_write_parent_summary")
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

    # Each entry carries a full rollup body; keep a call to <= 10 entries over
    # a remote bridge (a 40-entry payload has stressed one). kvault stamps
    # `updated` on every rewritten summary (0.15.2).
    @_tool("kvault_update_summaries")
    def kvault_update_summaries(
        updates: List[SummaryUpdate], kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Batch-update existing summaries: `{path, content | patches, meta}` per item.

        `content` replaces the body; `patches` (`[{old_str, new_str}]`, each
        matching exactly once) edits it. `meta` merges onto the frontmatter
        (null deletes a key). Paths without a summary are refused.

        CAUTION: `success: true` means the BATCH ran, not that every item
        succeeded — check `partial`, `failed`, and `errors[]`. Per-item
        decisions are collapsed by code in `notes` ({code, count, examples}).
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        started = time.monotonic()
        result = ops.update_summaries(root, [u.model_dump(exclude_unset=True) for u in updates])
        _record("update-summaries", result, started)
        return result

    @_tool("kvault_get_parent_summaries")
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

    @_legacy_tool("kvault_get_ancestors")
    def kvault_get_ancestors(
        path: str, ancestors: str = "content", kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Alias for kvault_get_parent_summaries."""
        return kvault_get_parent_summaries(path=path, ancestors=ancestors, kg_root=kg_root)

    @_legacy_tool("kvault_propagate_all")
    def kvault_propagate_all(
        path: str, ancestors: str = "content", kg_root: Optional[str] = None
    ) -> Dict[str, Any]:
        """Compatibility alias returning all summary propagation targets."""
        return kvault_get_parent_summaries(path=path, ancestors=ancestors, kg_root=kg_root)

    @_tool("kvault_write_journal")
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

    @_tool("kvault_generate_daily_artifact")
    def kvault_generate_daily_artifact(
        artifact_date: Optional[str] = None,
        force: bool = False,
        include_content: bool = False,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a daily artifact markdown file.

        The result names the file and its size; the markdown itself (often
        tens of KB) comes back only with include_content=true.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            parsed_date = parse_iso_date(artifact_date)
            result = generate_daily_artifact(root, artifact_date=parsed_date, force=force)
        except ValueError as exc:
            return error_response(ErrorCode.VALIDATION_ERROR, str(exc))
        return _serialize_daily_result(root, result, include_content=include_content)

    @_tool("kvault_check")
    def kvault_check(
        threshold_minutes: int = 5,
        summary_quality: bool = True,
        pending_max_age: int = 7,
        max_children: int = DEFAULT_MAX_CHILDREN,
        max_findings: int = DEFAULT_MAX_FINDINGS,
        summary_max_words: Optional[int] = None,
        summary_max_dated_sections: int = DEFAULT_MAX_DATED_SECTIONS,
        codes: Optional[List[str]] = None,
        kg_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the maintenance checks (the CLI's `kvault check`, one document).

        `success` is false only for hard findings (PROPAGATE, LOG, WRITE,
        BRANCH). `findings` is the unified list, hard first; SUMMARY,
        PENDING, RETRACTED, GHOST, SERIES, SIBLINGS, DUPLICATE, DANGLING,
        LOOSE, JOURNAL and STALE are warn-only maintenance work. Lists are capped at `max_findings` per
        code (0 = all) with the hidden counts in `truncated`. `codes` (e.g.
        ["SIBLINGS"]) runs and reports only those checks; with
        max_findings=0 that is one code's full list without the rest of
        the document.
        `kvault_validate_kb` checks integrity only; this is the one that
        says whether the tree is rotting.
        """
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        try:
            selected = normalize_codes(codes)
        except ValueError as exc:
            return error_response(ErrorCode.VALIDATION_ERROR, str(exc))
        return run_checks(
            root,
            threshold_minutes=threshold_minutes,
            summary_quality=summary_quality,
            pending_max_age=pending_max_age,
            max_children=max_children,
            max_findings=max_findings,
            max_words=summary_max_words,
            max_dated_sections=summary_max_dated_sections,
            codes=selected,
        )

    @_tool("kvault_plan")
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

    @_tool("kvault_validate_kb")
    def kvault_validate_kb(kg_root: Optional[str] = None) -> Dict[str, Any]:
        """Validate KB integrity (frontmatter, placeholders, ghost directories)."""
        root, err = _tool_root(bound_root, kg_root)
        if err:
            return err
        assert root is not None
        return success_response(ops.validate_kb(root))

    @_legacy_tool("kvault_log_phase")
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

    @_tool("kvault_log_tail")
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

    _forbid_unknown_arguments(server)
    return server


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--kb-root",
    type=click.Path(path_type=Path),
    default=None,
    help=f"Knowledge base root. May also be set with {KVAULT_KB_ROOT_ENV}.",
)
@click.option(
    "--legacy-tools",
    is_flag=True,
    default=None,
    help=f"Also register the epoch-1 tools (or set {KVAULT_MCP_LEGACY_TOOLS_ENV}=1).",
)
def main(kb_root: Optional[Path], legacy_tools: Optional[bool]) -> None:
    """Run the kvault MCP compatibility server over stdio."""
    server = create_server(resolve_bound_root(kb_root), legacy_tools=legacy_tools or None)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
