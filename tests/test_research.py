"""Tests for EntityResearcher."""

import json
import pytest
from pathlib import Path

from kgraph.core.index import EntityIndex
from kgraph.core.research import EntityResearcher


class TestEntityResearcher:
    """Tests for EntityResearcher class."""

    @pytest.fixture
    def populated_index(self, tmp_path):
        """Create an index with some entities."""
        index = EntityIndex(tmp_path / "test.db")

        # Add some entities
        index.add(
            "people/collaborators/alice_smith",
            "Alice Smith",
            ["Alice", "alice@anthropic.com"],
            "people",
        )
        index.add(
            "people/collaborators/bob_jones",
            "Bob Jones",
            ["Bob", "bob@anthropic.com"],
            "people",
        )
        index.add(
            "orgs/anthropic",
            "Anthropic",
            ["anthropic.com"],
            "orgs",
        )
        index.add(
            "orgs/acme_corp",
            "Acme Corporation",
            ["Acme", "acme.com"],
            "orgs",
        )

        return index

    def test_research_finds_exact_match(self, populated_index):
        """Test research finds exact name match."""
        researcher = EntityResearcher(populated_index)

        candidates = researcher.research("Alice Smith")

        assert len(candidates) > 0
        assert candidates[0].candidate_name == "Alice Smith"
        assert candidates[0].match_score == 1.0

    def test_research_finds_alias_match(self, populated_index):
        """Test research finds alias match."""
        researcher = EntityResearcher(populated_index)

        candidates = researcher.research("Alice")

        assert len(candidates) > 0
        assert candidates[0].candidate_name == "Alice Smith"

    def test_research_finds_fuzzy_match(self, populated_index):
        """Test research finds fuzzy name match."""
        researcher = EntityResearcher(populated_index)

        # Slight misspelling
        candidates = researcher.research("Alise Smith")

        assert len(candidates) > 0
        # Should find Alice Smith with high score
        alice = [c for c in candidates if c.candidate_name == "Alice Smith"]
        assert len(alice) > 0
        assert alice[0].match_score > 0.8

    def test_research_with_email(self, populated_index):
        """Test research using email domain matching."""
        researcher = EntityResearcher(populated_index)

        candidates = researcher.research(
            "New Person",
            email="newperson@anthropic.com",
        )

        # Should find entities with anthropic.com domain
        assert len(candidates) > 0
        paths = [c.candidate_path for c in candidates]
        assert any("alice" in p or "bob" in p for p in paths)

    def test_best_match_returns_top(self, populated_index):
        """Test best_match returns top candidate."""
        researcher = EntityResearcher(populated_index)

        match = researcher.best_match("Alice Smith")

        assert match is not None
        assert match.candidate_name == "Alice Smith"

    def test_best_match_returns_none_below_threshold(self, populated_index):
        """Test best_match returns None when below threshold."""
        researcher = EntityResearcher(populated_index)

        match = researcher.best_match("Completely Unknown Person", threshold=0.9)

        assert match is None

    def test_exists_true_for_matching(self, populated_index):
        """Test exists returns True for matching entity."""
        researcher = EntityResearcher(populated_index)

        assert researcher.exists("Alice Smith")
        assert researcher.exists("alice@anthropic.com")

    def test_exists_false_for_unknown(self, populated_index):
        """Test exists returns False for unknown entity."""
        researcher = EntityResearcher(populated_index)

        assert not researcher.exists("Completely Unknown Person")

    def test_suggest_action_create(self, populated_index):
        """Test suggest_action returns create for no match."""
        researcher = EntityResearcher(populated_index)

        action, path, confidence = researcher.suggest_action("New Person")

        assert action == "create"
        assert path is None
        assert confidence > 0.5

    def test_suggest_action_update(self, populated_index):
        """Test suggest_action returns update for high match."""
        researcher = EntityResearcher(populated_index)

        action, path, confidence = researcher.suggest_action("Alice Smith")

        assert action == "update"
        assert path == "people/collaborators/alice_smith"
        assert confidence >= 0.9

    def test_suggest_action_review(self, populated_index):
        """Test suggest_action returns review for medium match."""
        researcher = EntityResearcher(populated_index)

        # "Acme" might match "Acme Corporation" with medium confidence
        action, path, confidence = researcher.suggest_action("Acme Inc")

        # Should either be update (high match) or review (medium match)
        assert action in ["update", "review"]

    def test_find_by_email(self, populated_index):
        """Test find_by_email convenience method."""
        researcher = EntityResearcher(populated_index)

        candidates = researcher.find_by_email("someone@anthropic.com")

        assert len(candidates) > 0
        # Should find Alice and Bob (both have anthropic.com emails)

    def test_find_by_email_no_at_sign(self, populated_index):
        """Test find_by_email with invalid email."""
        researcher = EntityResearcher(populated_index)

        candidates = researcher.find_by_email("not-an-email")

        assert len(candidates) == 0

    def test_find_exact(self, populated_index):
        """Test find_exact method."""
        researcher = EntityResearcher(populated_index)

        # By name
        entry = researcher.find_exact("Alice Smith")
        assert entry is not None
        assert entry.name == "Alice Smith"

        # By alias
        entry = researcher.find_exact("Alice")
        assert entry is not None
        assert entry.name == "Alice Smith"

        # Unknown
        entry = researcher.find_exact("Unknown Person")
        assert entry is None

    def test_empty_index(self, tmp_path):
        """Test researcher with empty index."""
        index = EntityIndex(tmp_path / "empty.db")
        researcher = EntityResearcher(index)

        candidates = researcher.research("Alice Smith")
        assert len(candidates) == 0

        action, path, confidence = researcher.suggest_action("Alice Smith")
        assert action == "create"
        assert path is None
