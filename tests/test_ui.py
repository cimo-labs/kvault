"""Integration tests for the kvault web UI."""

import pytest

starlette = pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402

from kvault.ui.app import create_app  # noqa: E402


@pytest.fixture
def client(initialized_kb):
    """Starlette test client bound to the initialized sample KB."""
    app = create_app(initialized_kb)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Home / dashboard
# ---------------------------------------------------------------------------


class TestHome:
    def test_home_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_home_contains_entity_count(self, client):
        resp = client.get("/")
        assert "5 entities" in resp.text

    def test_home_contains_categories(self, client):
        resp = client.get("/")
        assert "people" in resp.text
        assert "projects" in resp.text


# ---------------------------------------------------------------------------
# Browse
# ---------------------------------------------------------------------------


class TestBrowse:
    def test_browse_returns_200(self, client):
        resp = client.get("/browse")
        assert resp.status_code == 200

    def test_browse_shows_root_summary_by_default(self, client):
        resp = client.get("/browse")
        assert resp.status_code == 200
        assert "Test Knowledge Base" in resp.text

    def test_browse_uses_tree_layout(self, client):
        resp = client.get("/browse")
        assert resp.status_code == 200
        assert 'id="tree-top"' in resp.text

    def test_browse_shows_top_level(self, client):
        resp = client.get("/browse")
        assert "people" in resp.text.lower()
        assert "projects" in resp.text.lower()

    def test_browse_uses_canonical_links(self, client):
        resp = client.get("/browse")
        assert "/browse?path=people" in resp.text

    def test_browse_selected_path_renders_content(self, client):
        resp = client.get("/browse?path=people/friends/alice_smith")
        assert resp.status_code == 200
        assert "Alice Smith" in resp.text

    def test_browse_selected_path_marks_active_tree_link(self, client):
        resp = client.get("/browse?path=people")
        assert resp.status_code == 200
        assert 'data-path="people"' in resp.text
        assert "tree-link is-active" in resp.text

    def test_browse_invalid_selected_path_returns_404(self, client):
        resp = client.get("/browse?path=../../etc/passwd")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Entity and Summary deep links (redirect to canonical browse URL)
# ---------------------------------------------------------------------------


class TestLegacyDeepLinks:
    def test_entity_redirects_to_browse(self, client):
        resp = client.get("/entity/people/friends/alice_smith", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/browse?path=people/friends/alice_smith"

    def test_summary_redirects_to_browse(self, client):
        resp = client.get("/summary/people", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/browse?path=people"

    def test_entity_not_found(self, client):
        resp = client.get("/entity/nonexistent/path")
        assert resp.status_code == 404

    def test_entity_path_traversal_blocked(self, client):
        resp = client.get("/entity/../../etc/passwd")
        assert resp.status_code == 404

    def test_summary_not_found(self, client):
        resp = client.get("/summary/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_page_returns_200(self, client):
        resp = client.get("/search")
        assert resp.status_code == 200

    def test_search_with_query(self, client):
        resp = client.get("/search?q=alice")
        assert resp.status_code == 200
        assert "Alice" in resp.text

    def test_search_no_results(self, client):
        resp = client.get("/search?q=zzzznonexistent")
        assert resp.status_code == 200
        assert "No results" in resp.text


# ---------------------------------------------------------------------------
# htmx partials
# ---------------------------------------------------------------------------


class TestHtmxPartials:
    def test_tree_children(self, client):
        resp = client.get("/htmx/tree-children?path=people")
        assert resp.status_code == 200
        assert "friends" in resp.text.lower()

    def test_tree_children_traversal_blocked(self, client):
        resp = client.get("/htmx/tree-children?path=../etc")
        assert resp.status_code == 200
        assert resp.text.strip() == ""

    def test_tree_children_absolute_path_blocked(self, client):
        resp = client.get("/htmx/tree-children?path=/")
        assert resp.status_code == 200
        assert resp.text.strip() == ""

    def test_tree_children_hides_internal_dirs(self, client):
        resp = client.get("/htmx/tree-children?path=.")
        assert resp.status_code == 200
        assert ".kvault" not in resp.text
        assert "journal" not in resp.text.lower()
        assert "sources" not in resp.text.lower()
        assert "tests" not in resp.text.lower()
        assert "scripts" not in resp.text.lower()

    def test_tree_children_marks_selected_link(self, client):
        resp = client.get("/htmx/tree-children?path=people&selected=people/friends")
        assert resp.status_code == 200
        assert 'data-path="people/friends"' in resp.text
        assert "tree-link is-active" in resp.text

    def test_search_partial(self, client):
        resp = client.get("/htmx/search?q=alice")
        assert resp.status_code == 200
        assert "Alice" in resp.text

    def test_entity_content_partial(self, client):
        resp = client.get("/htmx/entity-content?path=people/friends/alice_smith")
        assert resp.status_code == 200
        assert "Alice Smith" in resp.text

    def test_entity_content_partial_includes_parent_summary(self, client):
        resp = client.get("/htmx/entity-content?path=people/friends/alice_smith")
        assert resp.status_code == 200
        assert "Parent Summary" in resp.text

    def test_entity_content_partial_includes_children_for_summary(self, client):
        resp = client.get("/htmx/entity-content?path=people")
        assert resp.status_code == 200
        assert "Children" in resp.text
        assert "Friends" in resp.text

    def test_entity_content_rewrites_relative_markdown_links(self, client):
        resp = client.get("/htmx/entity-content?path=people")
        assert resp.status_code == 200
        assert "/browse?path=people/friends" in resp.text

    def test_entity_content_traversal_blocked(self, client):
        resp = client.get("/htmx/entity-content?path=../../etc/passwd")
        assert resp.status_code == 200
        assert resp.text.strip() == ""

    def test_entity_content_absolute_path_blocked(self, client):
        resp = client.get("/htmx/entity-content?path=/tmp")
        assert resp.status_code == 200
        assert resp.text.strip() == ""


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------


class TestStatic:
    def test_htmx_js(self, client):
        resp = client.get("/static/htmx.min.js")
        assert resp.status_code == 200

    def test_style_css(self, client):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "#browse-wrap" in resp.text
        assert "@media (max-width: 900px)" in resp.text
        assert "a.tree-link.is-active" in resp.text
