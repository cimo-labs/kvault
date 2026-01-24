"""Tests for EntityIndex."""

import json
import pytest
from pathlib import Path

from kgraph.core.index import EntityIndex, IndexEntry


class TestEntityIndex:
    """Tests for EntityIndex class."""

    def test_init_creates_database(self, tmp_path):
        """Test that initialization creates database file."""
        db_path = tmp_path / "test.db"
        index = EntityIndex(db_path)
        assert db_path.exists()

    def test_add_and_get(self, tmp_path):
        """Test adding and retrieving an entity."""
        index = EntityIndex(tmp_path / "test.db")

        index.add(
            path="people/collaborators/alice_smith",
            name="Alice Smith",
            aliases=["Alice", "alice@anthropic.com"],
            category="people",
        )

        entry = index.get("people/collaborators/alice_smith")
        assert entry is not None
        assert entry.name == "Alice Smith"
        assert entry.category == "people"
        assert "Alice" in entry.aliases
        assert "alice@anthropic.com" in entry.aliases
        assert "anthropic.com" in entry.email_domains

    def test_add_updates_existing(self, tmp_path):
        """Test that add updates existing entries."""
        index = EntityIndex(tmp_path / "test.db")

        index.add("test/entity", "Original Name", [], "test")
        index.add("test/entity", "Updated Name", ["alias"], "test")

        entry = index.get("test/entity")
        assert entry.name == "Updated Name"
        assert "alias" in entry.aliases

    def test_remove(self, tmp_path):
        """Test removing an entity."""
        index = EntityIndex(tmp_path / "test.db")

        index.add("test/entity", "Test", [], "test")
        assert index.get("test/entity") is not None

        index.remove("test/entity")
        assert index.get("test/entity") is None

    def test_search_fts(self, tmp_path):
        """Test full-text search."""
        index = EntityIndex(tmp_path / "test.db")

        index.add("people/alice", "Alice Smith", ["alice@example.com"], "people")
        index.add("people/bob", "Bob Jones", ["bob@example.com"], "people")
        index.add("orgs/acme", "Acme Corporation", [], "orgs")

        # Search by name
        results = index.search("Alice")
        assert len(results) == 1
        assert results[0].name == "Alice Smith"

        # Search by alias
        results = index.search("bob@example.com")
        assert len(results) == 1
        assert results[0].name == "Bob Jones"

    def test_search_with_category_filter(self, tmp_path):
        """Test search with category filter."""
        index = EntityIndex(tmp_path / "test.db")

        index.add("people/alice", "Alice Smith", [], "people")
        index.add("orgs/alice", "Alice Corp", [], "orgs")

        results = index.search("Alice", category="people")
        assert len(results) == 1
        assert results[0].category == "people"

    def test_find_by_alias(self, tmp_path):
        """Test exact alias lookup."""
        index = EntityIndex(tmp_path / "test.db")

        index.add("people/alice", "Alice Smith", ["Alice", "alice@example.com"], "people")

        # Exact match
        entry = index.find_by_alias("Alice")
        assert entry is not None
        assert entry.name == "Alice Smith"

        # Case insensitive
        entry = index.find_by_alias("ALICE")
        assert entry is not None

        # Email alias
        entry = index.find_by_alias("alice@example.com")
        assert entry is not None

        # No match
        entry = index.find_by_alias("Bob")
        assert entry is None

    def test_find_by_email_domain(self, tmp_path):
        """Test email domain lookup."""
        index = EntityIndex(tmp_path / "test.db")

        index.add("people/alice", "Alice", ["alice@anthropic.com"], "people")
        index.add("people/bob", "Bob", ["bob@anthropic.com"], "people")
        index.add("people/charlie", "Charlie", ["charlie@google.com"], "people")

        results = index.find_by_email_domain("anthropic.com")
        assert len(results) == 2
        names = [r.name for r in results]
        assert "Alice" in names
        assert "Bob" in names

    def test_list_all(self, tmp_path):
        """Test listing all entities."""
        index = EntityIndex(tmp_path / "test.db")

        index.add("people/alice", "Alice", [], "people")
        index.add("people/bob", "Bob", [], "people")
        index.add("orgs/acme", "Acme", [], "orgs")

        all_entities = index.list_all()
        assert len(all_entities) == 3

        people = index.list_all(category="people")
        assert len(people) == 2

    def test_count(self, tmp_path):
        """Test counting entities."""
        index = EntityIndex(tmp_path / "test.db")

        index.add("a", "A", [], "cat1")
        index.add("b", "B", [], "cat1")
        index.add("c", "C", [], "cat2")

        assert index.count() == 3
        assert index.count(category="cat1") == 2
        assert index.count(category="cat2") == 1

    def test_rebuild_from_filesystem(self, tmp_path):
        """Test rebuilding index from filesystem."""
        # Create knowledge graph structure
        kg_root = tmp_path / "kg"

        # Create entity with _meta.json
        entity_dir = kg_root / "people" / "alice_smith"
        entity_dir.mkdir(parents=True)
        meta = {
            "topic": "Alice Smith",
            "aliases": ["Alice", "alice@example.com"],
        }
        (entity_dir / "_meta.json").write_text(json.dumps(meta))

        # Create index and rebuild
        index = EntityIndex(tmp_path / "test.db")
        count = index.rebuild(kg_root)

        assert count == 1
        entry = index.get("people/alice_smith")
        assert entry is not None
        assert entry.name == "Alice Smith"
        assert entry.category == "people"

    def test_extract_email_domains(self, tmp_path):
        """Test email domain extraction from aliases."""
        index = EntityIndex(tmp_path / "test.db")

        index.add(
            "test/entity",
            "Test",
            ["name@example.com", "other@company.org", "not-an-email"],
            "test",
        )

        entry = index.get("test/entity")
        assert "example.com" in entry.email_domains
        assert "company.org" in entry.email_domains
        assert len(entry.email_domains) == 2
