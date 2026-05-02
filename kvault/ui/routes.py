"""Route handlers for the kvault web UI.

All handlers are read-only.  They delegate to the stateless operations layer
(``kvault.core.operations``) and the storage helpers.
"""

import inspect
from pathlib import Path
import posixpath
import re
from typing import Any, Awaitable, Callable, Optional, cast
from urllib.parse import quote, urlsplit, urlunsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.templating import Jinja2Templates

from kvault.core import operations as ops
from kvault.core.storage import SimpleStorage
from kvault.ui.markdown import render_markdown
from kvault.ui.search import search_entities

_HREF_ATTR_RE = re.compile(r'href="([^"]+)"')
_HIDDEN_ROOT_CATEGORIES = {"journal", "sources", "tests", "scripts"}


def _template_response(
    templates: Jinja2Templates,
    request: Request,
    name: str,
    context: dict,
) -> Response:
    """Return a TemplateResponse across Starlette's old/new call signatures."""
    clean_context = dict(context)
    clean_context.pop("request", None)
    parameters = list(inspect.signature(templates.TemplateResponse).parameters)
    if parameters and parameters[0] == "request":
        return templates.TemplateResponse(request, name, clean_context)
    legacy_template_response = cast(Any, templates.TemplateResponse)
    return legacy_template_response(name, {"request": request, **clean_context})


def _is_hidden_or_internal(name: str, *, at_root: bool = False) -> bool:
    """Return True for hidden/internal folder names that should not be shown in UI."""
    if name.startswith("."):
        return True
    if at_root and name in _HIDDEN_ROOT_CATEGORIES:
        return True
    return False


def _is_safe_ui_path(path: str, kg_root: Path) -> bool:
    """Return True when a user-supplied path is a safe KB-relative path."""
    if not path:
        return False
    candidate = Path(path)
    if candidate.is_absolute():
        return False
    if ".." in candidate.parts:
        return False
    return ops.validate_within_root(kg_root, path)


def _browse_url_for_path(path: str) -> str:
    """Return browse URL for a selected path."""
    normalized_path = (path or ".").strip()
    if normalized_path not in {"", "."}:
        return f"/browse?path={quote(normalized_path, safe='/')}"
    return "/browse"


def _rewrite_internal_markdown_links(rendered_html: str, current_path: str) -> str:
    """Rewrite KB-internal markdown links to canonical /browse URLs."""

    def _replace(match: re.Match) -> str:
        href = match.group(1)
        # Keep external/special links unchanged.
        if href.startswith(("http://", "https://", "mailto:", "tel:", "#")):
            return match.group(0)

        parsed = urlsplit(href)
        path_part = parsed.path or ""
        if not path_part:
            return match.group(0)

        if path_part.startswith("/"):
            # Preserve explicit application routes.
            if path_part.startswith(
                ("/browse", "/search", "/static", "/htmx", "/entity", "/summary")
            ):
                return match.group(0)
            resolved = path_part.strip("/")
        else:
            base = "" if current_path in {"", "."} else current_path.strip("/")
            resolved = posixpath.normpath(f"{base}/{path_part}").strip("/")
            if resolved == ".":
                resolved = ""

        rewritten = _browse_url_for_path(resolved or ".")

        # Preserve query/fragment from original markdown link.
        rewritten_parts = urlsplit(rewritten)
        final_query = rewritten_parts.query
        if parsed.query:
            final_query = f"{final_query}&{parsed.query}" if final_query else parsed.query
        final_href = urlunsplit(
            (
                rewritten_parts.scheme,
                rewritten_parts.netloc,
                rewritten_parts.path,
                final_query,
                parsed.fragment,
            )
        )
        return f'href="{final_href}"'

    return _HREF_ATTR_RE.sub(_replace, rendered_html)


def _list_visible_children(storage: SimpleStorage, path: str) -> list[str]:
    """List immediate child paths, excluding hidden/internal folders."""
    at_root = path in {"", "."}
    visible: list[str] = []
    for child in storage.get_children(path):
        child_name = Path(child).name
        if _is_hidden_or_internal(child_name, at_root=at_root):
            continue
        visible.append(child)
    return sorted(visible)


def _build_child_item(storage: SimpleStorage, kg_root: Path, child_path: str) -> dict:
    """Build tree item metadata used by browse/sidebar templates."""
    sub_children = _list_visible_children(storage, child_path)
    has_children = len(sub_children) > 0
    has_summary = (kg_root / child_path / "_summary.md").exists()
    is_entity = has_summary and not has_children and len(Path(child_path).parts) >= 2
    return {
        "path": child_path,
        "name": Path(child_path).name,
        "has_children": has_children,
        "is_entity": is_entity,
        "browse_url": _browse_url_for_path(child_path),
    }


def _build_tree_nodes(storage: SimpleStorage, kg_root: Path, parent_path: str = ".") -> list[dict]:
    """Recursively build all visible tree nodes from *parent_path*."""
    nodes: list[dict] = []
    for child_path in _list_visible_children(storage, parent_path):
        child_item = _build_child_item(storage, kg_root, child_path)
        child_item["children"] = _build_tree_nodes(storage, kg_root, child_path)
        nodes.append(child_item)
    return nodes


def _build_entity_context(kg_root: Path, path: str) -> Optional[dict]:
    """Build rendered context for an entity/summary path."""
    data = ops.read_entity(kg_root, path)
    if not data:
        data = ops.read_summary(kg_root, path)
    if not data:
        return None

    rendered_content = render_markdown(data.get("content", ""))
    rendered_content = _rewrite_internal_markdown_links(rendered_content, path)
    parent_rendered = ""
    if data.get("parent_summary"):
        from kvault.core.frontmatter import parse_frontmatter

        _, parent_body = parse_frontmatter(data["parent_summary"])
        parent_rendered = render_markdown(parent_body)
        parent_base_path = data.get("parent_path", path)
        parent_rendered = _rewrite_internal_markdown_links(parent_rendered, parent_base_path)

    storage = SimpleStorage(kg_root)
    children = [
        _build_child_item(storage, kg_root, c) for c in _list_visible_children(storage, path)
    ]
    return {
        "entity": data,
        "rendered_content": rendered_content,
        "parent_rendered": parent_rendered,
        "breadcrumbs": _build_breadcrumbs(path),
        "children": children,
        "selected_path": path,
    }


# ---------------------------------------------------------------------------
# Handler factory
# ---------------------------------------------------------------------------


def make_handler(
    name: str, kg_root: Path, templates: Jinja2Templates
) -> Callable[[Request], Awaitable[Response]]:
    """Return a request handler closure for the given endpoint *name*."""
    handlers = {
        "home": _home,
        "browse": _browse,
        "entity": _entity,
        "summary": _summary,
        "search": _search,
        "htmx_tree_children": _htmx_tree_children,
        "htmx_search": _htmx_search,
        "htmx_entity_content": _htmx_entity_content,
    }
    fn = handlers[name]

    async def _handler(request: Request) -> Response:
        return fn(request, kg_root, templates)

    return _handler


# ---------------------------------------------------------------------------
# Full-page routes
# ---------------------------------------------------------------------------


def _home(request: Request, kg_root: Path, templates: Jinja2Templates) -> Response:
    info = ops.get_kb_info(kg_root)
    health = ops.validate_kb(kg_root)
    storage = SimpleStorage(kg_root)
    categories = []
    for child in _list_visible_children(storage, "."):
        child_name = Path(child).name
        sub_children = _list_visible_children(storage, child)
        categories.append({"path": child, "name": child_name, "count": len(sub_children)})
    return _template_response(
        templates,
        request,
        "home.html",
        {
            "info": info,
            "health": health,
            "categories": categories,
        },
    )


def _browse(request: Request, kg_root: Path, templates: Jinja2Templates) -> Response:
    storage = SimpleStorage(kg_root)
    tree_nodes = _build_tree_nodes(storage, kg_root, ".")

    requested_path = request.query_params.get("path", "").strip()
    selected_path = requested_path or "."
    if not _is_safe_ui_path(selected_path, kg_root):
        return HTMLResponse("Not found", status_code=404)
    selected_context = _build_entity_context(kg_root, selected_path) or {}
    if not selected_context:
        return HTMLResponse("Not found", status_code=404)

    return _template_response(
        templates,
        request,
        "browse.html",
        {
            "selected_path": selected_path,
            "tree_nodes": tree_nodes,
            **selected_context,
        },
    )


def _entity(request: Request, kg_root: Path, templates: Jinja2Templates) -> Response:
    entity_path = request.path_params["path"]
    if not _is_safe_ui_path(entity_path, kg_root):
        return HTMLResponse("Not found", status_code=404)
    if not ops.read_entity(kg_root, entity_path):
        return HTMLResponse("Entity not found", status_code=404)
    return RedirectResponse(_browse_url_for_path(entity_path), status_code=307)


def _summary(request: Request, kg_root: Path, templates: Jinja2Templates) -> Response:
    summary_path = request.path_params["path"]
    if not _is_safe_ui_path(summary_path, kg_root):
        return HTMLResponse("Not found", status_code=404)
    if not ops.read_summary(kg_root, summary_path):
        return HTMLResponse("Summary not found", status_code=404)
    return RedirectResponse(_browse_url_for_path(summary_path), status_code=307)


def _search(request: Request, kg_root: Path, templates: Jinja2Templates) -> Response:
    query = request.query_params.get("q", "")
    results = search_entities(kg_root, query) if query else []
    return _template_response(
        templates,
        request,
        "search.html",
        {"query": query, "results": results},
    )


# ---------------------------------------------------------------------------
# htmx partial routes
# ---------------------------------------------------------------------------


def _htmx_tree_children(request: Request, kg_root: Path, templates: Jinja2Templates) -> Response:
    path = request.query_params.get("path", "").strip()
    if not _is_safe_ui_path(path, kg_root):
        return HTMLResponse("")
    selected_path = request.query_params.get("selected", "").strip()
    if selected_path and not _is_safe_ui_path(selected_path, kg_root):
        selected_path = ""
    storage = SimpleStorage(kg_root)
    child_items = [
        _build_child_item(storage, kg_root, c) for c in _list_visible_children(storage, path)
    ]
    return _template_response(
        templates,
        request,
        "partials/tree_children.html",
        {
            "children": child_items,
            "selected_path": selected_path,
        },
    )


def _htmx_search(request: Request, kg_root: Path, templates: Jinja2Templates) -> Response:
    query = request.query_params.get("q", "")
    results = search_entities(kg_root, query) if query else []
    return _template_response(
        templates,
        request,
        "partials/search_results.html",
        {"results": results, "query": query},
    )


def _htmx_entity_content(request: Request, kg_root: Path, templates: Jinja2Templates) -> Response:
    path = request.query_params.get("path", "").strip()
    if not _is_safe_ui_path(path, kg_root):
        return HTMLResponse("")
    entity_context = _build_entity_context(kg_root, path)
    if not entity_context:
        return HTMLResponse("<p>Not found.</p>")
    return _template_response(
        templates,
        request,
        "partials/entity_content.html",
        {
            **entity_context,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_breadcrumbs(path: str) -> list:
    """Build breadcrumb list from a path like 'people/friends/alice_smith'."""
    if path in {"", "."}:
        return [{"name": "Home", "url": _browse_url_for_path(".")}]

    parts = [part for part in Path(path).parts if part != "."]
    crumbs = [{"name": "Home", "url": _browse_url_for_path(".")}]
    for i, part in enumerate(parts):
        accumulated = "/".join(parts[: i + 1])
        url = _browse_url_for_path(accumulated)
        crumbs.append({"name": part.replace("_", " ").title(), "url": url})
    return crumbs
