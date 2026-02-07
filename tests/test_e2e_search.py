"""End-to-end search tests for kvault.

Tests search, alias lookup, email domain matching, and research
against the sample KB fixture with known entities.

Following CJE's testing philosophy: real data, complete workflows, clear intent.
"""

import pytest
from tests.conftest import SAMPLE_KB_ENTITY_COUNT

from kvault.mcp.server import (
    handle_kvault_search,
    handle_kvault_research,
    handle_kvault_list_entities,
)


# ============================================================================
# Full-Text Search
# ============================================================================


class TestFTSSearch:
    """Full-text search against the sample KB."""

    def test_search_by_name(self, initialized_kb):
        """Search by entity name returns correct results."""
        results = handle_kvault_search("Alice Smith")
        assert len(results) >= 1
        assert any("alice_smith" in r["path"] for r in results)

    def test_search_by_partial_name(self, initialized_kb):
        """Search by partial name works."""
        results = handle_kvault_search("Sarah")
        assert len(results) >= 1
        assert any("sarah_chen" in r["path"] for r in results)

    def test_search_by_email_in_aliases(self, initialized_kb):
        """Search for an email address that's stored as an alias."""
        results = handle_kvault_search("alice@acme.com")
        assert len(results) >= 1
        assert any("alice_smith" in r["path"] for r in results)

    def test_search_by_content_keyword(self, initialized_kb):
        """Search matches against content keywords."""
        results = handle_kvault_search("Anthropic")
        assert len(results) >= 1
        # Sarah Chen works at Anthropic
        assert any("sarah_chen" in r["path"] for r in results)

    def test_search_with_special_chars_safe(self, initialized_kb):
        """Special characters in queries don't crash."""
        # These should not raise exceptions
        for query in ['"quoted"', "alice@acme.com", "O'Brien", "test; DROP TABLE"]:
            results = handle_kvault_search(query)
            assert isinstance(results, list)

    def test_search_empty_query(self, initialized_kb):
        """Empty query returns empty results (not crash)."""
        results = handle_kvault_search("")
        assert isinstance(results, list)

    def test_search_with_category_filter(self, initialized_kb):
        """Category filter narrows results."""
        # Search in people only
        results = handle_kvault_search("kvault", category="people")
        kvault_results = [r for r in results if "projects" in r["path"]]
        assert len(kvault_results) == 0  # kvault is in projects, not people

    def test_search_respects_limit(self, initialized_kb):
        """Limit parameter caps result count."""
        results = handle_kvault_search("Smith", limit=1)
        assert len(results) <= 1

    def test_search_nonexistent_returns_empty(self, initialized_kb):
        """Searching for something that doesn't exist returns empty list."""
        results = handle_kvault_search("xyzzy_totally_unknown_entity")
        assert len(results) == 0


# ============================================================================
# Alias Lookup
# ============================================================================


class TestAliasLookup:
    """Alias matching via unified search — email/name queries auto-detected."""

    def test_search_by_exact_name(self, initialized_kb):
        """Search by exact name finds the entity."""
        results = handle_kvault_search("Alice Smith")
        assert len(results) >= 1
        assert "alice_smith" in results[0]["path"]

    def test_search_by_name_case_insensitive(self, initialized_kb):
        """Search is case-insensitive."""
        results = handle_kvault_search("alice smith")
        assert len(results) >= 1
        assert "alice_smith" in results[0]["path"]

    def test_search_by_email_alias(self, initialized_kb):
        """Search by email finds exact alias match."""
        results = handle_kvault_search("sarah@anthropic.com")
        assert len(results) >= 1
        assert "sarah_chen" in results[0]["path"]

    def test_search_by_short_alias(self, initialized_kb):
        """Search by short alias (Ali for Alice Smith)."""
        results = handle_kvault_search("Ali")
        assert any("alice_smith" in r["path"] for r in results)

    def test_search_no_match(self, initialized_kb):
        """Non-existent query returns empty."""
        results = handle_kvault_search("Completely Unknown Person zzzzz")
        assert len(results) == 0

    def test_search_unicode(self, initialized_kb):
        """Search by Unicode name (José García)."""
        results = handle_kvault_search("José García")
        assert len(results) >= 1
        assert "jose_garcia" in results[0]["path"]

    def test_search_accent_insensitive(self, initialized_kb):
        """Search 'Jose Garcia' finds 'José García'."""
        results = handle_kvault_search("Jose Garcia")
        assert any("jose_garcia" in r["path"] for r in results)


# ============================================================================
# Email Domain Search (via unified search)
# ============================================================================


class TestEmailDomain:
    """Email domain matching via unified search."""

    def test_search_by_at_domain(self, initialized_kb):
        """Search @acme.com finds entities with that domain."""
        results = handle_kvault_search("@acme.com")
        assert len(results) >= 1
        assert any("alice_smith" in r["path"] for r in results)

    def test_search_by_domain_anthropic(self, initialized_kb):
        """Search @anthropic.com finds Anthropic employees."""
        results = handle_kvault_search("@anthropic.com")
        assert len(results) >= 1
        assert any("sarah_chen" in r["path"] for r in results)

    def test_search_unknown_domain(self, initialized_kb):
        """Search for unknown domain returns empty."""
        results = handle_kvault_search("@nonexistent-corp.com")
        assert len(results) == 0


# ============================================================================
# Research (Multi-Strategy Matching)
# ============================================================================


class TestResearch:
    """Research endpoint combining multiple matching strategies."""

    def test_research_suggests_create_for_unknown(self, initialized_kb):
        """Completely unknown person should get 'create' suggestion."""
        result = handle_kvault_research("Completely Unknown Person")
        assert result["suggested_action"] == "create"

    def test_research_suggests_update_for_exact_match(self, initialized_kb):
        """Exact alias match should get 'update' suggestion."""
        result = handle_kvault_research("Alice Smith")
        assert result["suggested_action"] == "update"
        assert "alice_smith" in result["suggested_target"]

    def test_research_finds_fuzzy_match(self, initialized_kb):
        """Fuzzy match (typo) should find the correct entity."""
        result = handle_kvault_research("Alic Smith")  # typo
        assert len(result["matches"]) > 0
        assert result["matches"][0]["score"] >= 0.85

    def test_research_unicode_normalization(self, initialized_kb):
        """Research with unaccented name should find accented entity."""
        result = handle_kvault_research("Jose Garcia")
        assert len(result["matches"]) > 0
        top = result["matches"][0]
        assert "jose" in top["path"]

    def test_research_with_email(self, initialized_kb):
        """Research with email should find entities at same domain."""
        result = handle_kvault_research("New Hire", email="intern@anthropic.com")
        matches = result["matches"]
        # Should find sarah_chen via anthropic.com domain
        assert any("sarah_chen" in m["path"] for m in matches)

    def test_research_with_phone(self, initialized_kb):
        """Research with phone number should work without crashing."""
        # Even if no match, should not error
        result = handle_kvault_research("Someone", phone="+14155551234")
        assert "suggested_action" in result


# ============================================================================
# List Entities
# ============================================================================


class TestListEntities:
    """Entity listing tests."""

    def test_list_all(self, initialized_kb):
        """List all entities returns expected count."""
        results = handle_kvault_list_entities()
        assert len(results) == SAMPLE_KB_ENTITY_COUNT

    def test_list_by_category_people(self, initialized_kb):
        """List people category returns 4 entities."""
        results = handle_kvault_list_entities(category="people")
        assert len(results) == 4  # alice, jose, sarah, bob

    def test_list_by_category_projects(self, initialized_kb):
        """List projects category returns 1 entity."""
        results = handle_kvault_list_entities(category="projects")
        assert len(results) == 1  # kvault

    def test_list_unknown_category_empty(self, initialized_kb):
        """Unknown category returns empty list."""
        results = handle_kvault_list_entities(category="nonexistent")
        assert len(results) == 0
