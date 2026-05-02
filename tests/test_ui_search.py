"""Unit tests for the kvault web UI search module."""

from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")

import kvault.ui.search as ui_search  # noqa: E402
from kvault.ui.search import search_entities, _score  # noqa: E402
from kvault.core.storage import EntityRecord  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def alice():
    return EntityRecord(
        path="people/friends/alice_smith",
        name="Alice Smith",
        aliases=["Alice Smith", "alice@acme.com", "Ali"],
        category="people",
        email_domains=["acme.com"],
        content="Data scientist at Acme Corp.",
        last_updated="2026-02-01",
    )


# ---------------------------------------------------------------------------
# _score tests
# ---------------------------------------------------------------------------


class TestScore:
    def test_exact_name_match(self, alice):
        assert _score(alice, "alice smith") == 100

    def test_name_contains(self, alice):
        assert _score(alice, "alice") == 80

    def test_exact_alias_match(self, alice):
        # "ali" also matches "Alice" in name (substring → 80), so alias exact
        # match is only dominant when name doesn't also match.
        assert _score(alice, "alice@acme.com") == 70

    def test_alias_contains(self, alice):
        assert _score(alice, "alice@") == 60

    def test_path_match(self, alice):
        assert _score(alice, "friends") == 40

    def test_content_match(self, alice):
        assert _score(alice, "acme corp") == 20

    def test_no_match(self, alice):
        assert _score(alice, "zzzzz") == 0


# ---------------------------------------------------------------------------
# search_entities integration
# ---------------------------------------------------------------------------


class TestSearchEntities:
    def setup_method(self):
        ui_search.clear_search_cache()

    def test_empty_query_returns_empty(self, initialized_kb):
        assert search_entities(initialized_kb, "") == []

    def test_whitespace_query_returns_empty(self, initialized_kb):
        assert search_entities(initialized_kb, "   ") == []

    def test_finds_entity_by_name(self, initialized_kb):
        results = search_entities(initialized_kb, "alice")
        assert len(results) >= 1
        assert results[0].name == "Alice Smith"

    def test_finds_entity_by_alias(self, initialized_kb):
        results = search_entities(initialized_kb, "ali")
        assert len(results) >= 1
        names = [r.name for r in results]
        assert "Alice Smith" in names

    def test_finds_entity_by_path(self, initialized_kb):
        results = search_entities(initialized_kb, "kvault")
        assert len(results) >= 1

    def test_respects_limit(self, initialized_kb):
        results = search_entities(initialized_kb, "a", limit=2)
        assert len(results) <= 2

    def test_results_ordered_by_relevance(self, initialized_kb):
        results = search_entities(initialized_kb, "alice")
        if len(results) > 1:
            # First result should have higher relevance (name match)
            first_score = _score(results[0], "alice")
            second_score = _score(results[1], "alice")
            assert first_score >= second_score

    def test_search_uses_cache_for_repeated_queries(self, monkeypatch, alice):
        calls = {"count": 0}

        def _fake_scan(_root):
            calls["count"] += 1
            return [alice]

        monkeypatch.setattr(ui_search, "scan_entities", _fake_scan)
        monkeypatch.setattr(ui_search, "_SEARCH_CACHE_TTL_SECONDS", 60.0)

        search_entities(Path("/tmp/kb"), "alice")
        search_entities(Path("/tmp/kb"), "ali")
        assert calls["count"] == 1

    def test_search_cache_expiry_triggers_rescan(self, monkeypatch, alice):
        calls = {"count": 0}

        def _fake_scan(_root):
            calls["count"] += 1
            return [alice]

        monkeypatch.setattr(ui_search, "scan_entities", _fake_scan)
        monkeypatch.setattr(ui_search, "_SEARCH_CACHE_TTL_SECONDS", -1.0)

        search_entities(Path("/tmp/kb"), "alice")
        search_entities(Path("/tmp/kb"), "alice")
        assert calls["count"] == 2
