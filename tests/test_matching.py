"""Tests for kvault.matching strategies."""

import json
import pytest
from pathlib import Path

from kvault.matching.base import (
    EntityIndexEntry,
    MatchCandidate,
    register_strategy,
    get_strategy,
    list_strategies,
    load_strategies,
)
from kvault.matching.alias import AliasMatchStrategy
from kvault.matching.fuzzy import FuzzyNameMatchStrategy
from kvault.matching.domain import EmailDomainMatchStrategy


def _make_index_entry(
    entity_id, name, aliases=None, email_domains=None, path="", contacts=None
):
    """Helper to create an EntityIndexEntry."""
    return EntityIndexEntry(
        id=entity_id,
        name=name,
        entity_type="person",
        path=path or f"people/{entity_id}",
        aliases=aliases or [],
        email_domains=email_domains or [],
        contacts=contacts or [],
    )


def _make_index(*entries):
    """Build an index dict from entries."""
    return {e.id: e for e in entries}


# ============================================================================
# MatchCandidate
# ============================================================================


class TestMatchCandidate:
    def test_valid_score(self):
        mc = MatchCandidate("id", "name", "path", "alias", 0.5)
        assert mc.match_score == 0.5

    def test_score_bounds(self):
        MatchCandidate("id", "name", "path", "alias", 0.0)
        MatchCandidate("id", "name", "path", "alias", 1.0)

    def test_invalid_score_raises(self):
        with pytest.raises(ValueError):
            MatchCandidate("id", "name", "path", "alias", 1.5)
        with pytest.raises(ValueError):
            MatchCandidate("id", "name", "path", "alias", -0.1)


# ============================================================================
# Strategy Registry
# ============================================================================


class TestStrategyRegistry:
    def test_builtin_strategies_registered(self):
        names = list_strategies()
        assert "alias" in names
        assert "fuzzy_name" in names
        assert "email_domain" in names

    def test_get_strategy(self):
        cls = get_strategy("alias")
        assert cls is AliasMatchStrategy

    def test_get_unknown_strategy(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            get_strategy("nonexistent")

    def test_load_strategies(self):
        strategies = load_strategies(["alias", "fuzzy_name"])
        assert len(strategies) == 2
        assert isinstance(strategies[0], AliasMatchStrategy)
        assert isinstance(strategies[1], FuzzyNameMatchStrategy)


# ============================================================================
# AliasMatchStrategy
# ============================================================================


class TestAliasMatchStrategy:
    def setup_method(self):
        self.strategy = AliasMatchStrategy()

    def test_exact_alias_match(self):
        index = _make_index(
            _make_index_entry("alice", "Alice Smith", aliases=["Ali", "alice@example.com"]),
        )
        matches = self.strategy.find_matches({"name": "Ali"}, index)
        assert len(matches) == 1
        assert matches[0].candidate_id == "alice"
        assert matches[0].match_score == 1.0

    def test_case_insensitive(self):
        index = _make_index(
            _make_index_entry("alice", "Alice Smith", aliases=["ALICE"]),
        )
        matches = self.strategy.find_matches({"name": "alice"}, index)
        assert len(matches) == 1

    def test_exact_name_match(self):
        index = _make_index(
            _make_index_entry("alice", "Alice Smith"),
        )
        matches = self.strategy.find_matches({"name": "Alice Smith"}, index)
        assert len(matches) == 1
        assert matches[0].match_details["source"] == "exact_name"

    def test_no_match(self):
        index = _make_index(
            _make_index_entry("alice", "Alice Smith", aliases=["Ali"]),
        )
        matches = self.strategy.find_matches({"name": "Bob"}, index)
        assert len(matches) == 0

    def test_empty_name(self):
        index = _make_index(
            _make_index_entry("alice", "Alice Smith"),
        )
        matches = self.strategy.find_matches({"name": ""}, index)
        assert len(matches) == 0

    def test_with_aliases_file(self, tmp_path):
        aliases_file = tmp_path / "aliases.json"
        aliases_data = {
            "alice": {"aliases": ["A. Smith", "Ali"]},
        }
        aliases_file.write_text(json.dumps(aliases_data))

        strategy = AliasMatchStrategy(aliases_path=aliases_file)
        index = _make_index(
            _make_index_entry("alice", "Alice Smith"),
        )
        matches = strategy.find_matches({"name": "A. Smith"}, index)
        assert len(matches) == 1
        assert matches[0].match_details["source"] == "aliases_file"

    def test_no_duplicate_matches(self):
        """Entity matched via aliases file should not also match via index aliases."""
        index = _make_index(
            _make_index_entry("alice", "Alice Smith", aliases=["Ali"]),
        )
        matches = self.strategy.find_matches({"name": "Ali"}, index)
        assert len(matches) == 1

    def test_score_range(self):
        assert self.strategy.score_range == (1.0, 1.0)

    def test_name_property(self):
        assert self.strategy.name == "alias"


# ============================================================================
# FuzzyNameMatchStrategy
# ============================================================================


class TestFuzzyNameMatchStrategy:
    def setup_method(self):
        self.strategy = FuzzyNameMatchStrategy(threshold=0.85)

    def test_similar_names_match(self):
        index = _make_index(
            _make_index_entry("alice", "Alice Smith"),
        )
        matches = self.strategy.find_matches({"name": "Alice Smth"}, index)
        assert len(matches) == 1
        assert matches[0].match_score >= 0.85

    def test_dissimilar_names_no_match(self):
        index = _make_index(
            _make_index_entry("alice", "Alice Smith"),
        )
        matches = self.strategy.find_matches({"name": "Bob Jones"}, index)
        assert len(matches) == 0

    def test_underscore_space_symmetric(self):
        """port_group_usa and Port Group USA should match."""
        index = _make_index(
            _make_index_entry("port", "port_group_usa"),
        )
        matches = self.strategy.find_matches({"name": "Port Group USA"}, index)
        assert len(matches) == 1
        assert matches[0].match_score > 0.9

    def test_suffix_removal(self):
        """Company suffixes should be stripped."""
        index = _make_index(
            _make_index_entry("acme", "Acme Corp."),
        )
        matches = self.strategy.find_matches({"name": "Acme"}, index)
        assert len(matches) == 1

    def test_unicode_normalization(self):
        """Accented characters should be normalized: José ≈ Jose."""
        index = _make_index(
            _make_index_entry("jose", "José García"),
        )
        matches = self.strategy.find_matches({"name": "Jose Garcia"}, index)
        assert len(matches) == 1
        assert matches[0].match_score > 0.95

    def test_matches_against_aliases(self):
        index = _make_index(
            _make_index_entry("alice", "Alice Smith", aliases=["A. Smith"]),
        )
        matches = self.strategy.find_matches({"name": "A. Smith"}, index)
        assert len(matches) == 1

    def test_sorted_by_score(self):
        index = _make_index(
            _make_index_entry("alice", "Alice Smith"),
            _make_index_entry("alicia", "Alicia Smith"),
        )
        matches = self.strategy.find_matches({"name": "Alice Smith"}, index)
        if len(matches) > 1:
            assert matches[0].match_score >= matches[1].match_score

    def test_empty_name(self):
        index = _make_index(_make_index_entry("alice", "Alice"))
        matches = self.strategy.find_matches({"name": ""}, index)
        assert len(matches) == 0

    def test_custom_threshold(self):
        strategy = FuzzyNameMatchStrategy(threshold=0.99)
        index = _make_index(
            _make_index_entry("alice", "Alice Smith"),
        )
        matches = strategy.find_matches({"name": "Alice Smth"}, index)
        assert len(matches) == 0  # Below 0.99 threshold

    def test_score_range(self):
        lo, hi = self.strategy.score_range
        assert lo == 0.85
        assert hi == 0.99

    def test_name_property(self):
        assert self.strategy.name == "fuzzy_name"


# ============================================================================
# EmailDomainMatchStrategy
# ============================================================================


class TestEmailDomainMatchStrategy:
    def setup_method(self):
        self.strategy = EmailDomainMatchStrategy()

    def test_matching_domain(self):
        index = _make_index(
            _make_index_entry(
                "acme", "Acme Corp", email_domains=["acme.com"],
                contacts=[{"email": "ceo@acme.com"}],
            ),
        )
        entity = {"contacts": [{"email": "sales@acme.com"}]}
        matches = self.strategy.find_matches(entity, index)
        assert len(matches) == 1
        assert matches[0].candidate_id == "acme"

    def test_generic_domains_ignored(self):
        index = _make_index(
            _make_index_entry(
                "alice", "Alice", email_domains=["gmail.com"],
                contacts=[{"email": "alice@gmail.com"}],
            ),
        )
        entity = {"contacts": [{"email": "bob@gmail.com"}]}
        matches = self.strategy.find_matches(entity, index)
        assert len(matches) == 0

    def test_no_contacts(self):
        index = _make_index(
            _make_index_entry("acme", "Acme", email_domains=["acme.com"]),
        )
        matches = self.strategy.find_matches({"contacts": []}, index)
        assert len(matches) == 0
        matches = self.strategy.find_matches({}, index)
        assert len(matches) == 0

    def test_score_in_range(self):
        index = _make_index(
            _make_index_entry("acme", "Acme", email_domains=["acme.com"]),
        )
        entity = {"contacts": [{"email": "sales@acme.com"}]}
        matches = self.strategy.find_matches(entity, index)
        assert len(matches) == 1
        assert 0.85 <= matches[0].match_score <= 0.95

    def test_custom_generic_domains(self):
        strategy = EmailDomainMatchStrategy(generic_domains={"acme.com"})
        index = _make_index(
            _make_index_entry("acme", "Acme", email_domains=["acme.com"]),
        )
        entity = {"contacts": [{"email": "sales@acme.com"}]}
        matches = strategy.find_matches(entity, index)
        assert len(matches) == 0

    def test_multiple_domain_overlap(self):
        """Higher overlap ratio should give higher score."""
        index = _make_index(
            _make_index_entry(
                "acme", "Acme",
                email_domains=["acme.com", "acme.co.uk"],
            ),
        )
        entity = {"contacts": [
            {"email": "a@acme.com"},
            {"email": "b@acme.co.uk"},
        ]}
        matches = self.strategy.find_matches(entity, index)
        assert len(matches) == 1
        assert matches[0].match_score > 0.90  # Higher due to full overlap

    def test_score_range(self):
        lo, hi = self.strategy.score_range
        assert lo == 0.85
        assert hi == 0.95

    def test_name_property(self):
        assert self.strategy.name == "email_domain"


# ============================================================================
# EntityIndexEntry
# ============================================================================


class TestEntityIndexEntry:
    def test_from_entity_data(self):
        data = {
            "topic": "Acme Corp",
            "aliases": ["Acme"],
            "contacts": [{"email": "ceo@acme.com"}],
            "industry": "tech",
        }
        entry = EntityIndexEntry.from_entity_data("acme", data, "company", "tier1")
        assert entry.name == "Acme Corp"
        assert entry.aliases == ["Acme"]
        assert entry.email_domains == ["acme.com"]
        assert entry.industry == "tech"
        assert entry.tier == "tier1"

    def test_from_entity_data_name_fallback(self):
        data = {"name": "Alice"}
        entry = EntityIndexEntry.from_entity_data("alice", data, "person")
        assert entry.name == "Alice"

    def test_from_entity_data_id_fallback(self):
        data = {}
        entry = EntityIndexEntry.from_entity_data("alice_smith", data, "person")
        assert entry.name == "alice_smith"
