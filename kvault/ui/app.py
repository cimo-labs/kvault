"""Starlette application factory for the kvault web UI."""

from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from kvault.ui.markdown import render_markdown
from kvault.ui import routes

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATE_DIR = Path(__file__).parent / "templates"


def create_app(kg_root: Path) -> Starlette:
    """Create a Starlette app bound to *kg_root*."""
    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
    templates.env.filters["markdown"] = render_markdown
    templates.env.globals["kg_root"] = str(kg_root)

    def _route(path: str, endpoint_name: str, **kw):
        handler = routes.make_handler(endpoint_name, kg_root, templates)
        return Route(path, handler, **kw)

    app = Starlette(
        debug=False,
        routes=[
            _route("/", "home"),
            _route("/browse", "browse"),
            _route("/entity/{path:path}", "entity"),
            _route("/summary/{path:path}", "summary"),
            _route("/search", "search"),
            # htmx partials
            _route("/htmx/tree-children", "htmx_tree_children"),
            _route("/htmx/search", "htmx_search"),
            _route("/htmx/entity-content", "htmx_entity_content"),
            # static files
            Mount("/static", app=StaticFiles(directory=str(_STATIC_DIR)), name="static"),
        ],
    )
    return app
