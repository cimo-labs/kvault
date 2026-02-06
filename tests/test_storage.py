"""Tests for SimpleStorage."""

import json
import pytest
from pathlib import Path

from kvault.core.storage import SimpleStorage, normalize_entity_id


class TestNormalizeEntityId:
    """Tests for normalize_entity_id function."""

    def test_basic_normalization(self):
        """Test basic name normalization."""
        assert normalize_entity_id("Alice Smith") == "alice_smith"
        assert normalize_entity_id("UPPERCASE NAME") == "uppercase_name"

    def test_special_characters(self):
        """Test handling of special characters."""
        assert normalize_entity_id("R&L Carriers") == "rl_carriers"
        assert normalize_entity_id("Universal Robots A/S") == "universal_robots_as"
        assert normalize_entity_id("O'Brien Inc.") == "obrien_inc"

    def test_existing_underscores(self):
        """Test handling of existing underscores."""
        assert normalize_entity_id("alice_smith") == "alice_smith"
        assert normalize_entity_id("multiple__underscores") == "multiple_underscores"

    def test_edge_cases(self):
        """Test edge cases."""
        assert normalize_entity_id("  spaces  ") == "spaces"
        assert normalize_entity_id("123 Company") == "123_company"


class TestSimpleStorage:
    """Tests for SimpleStorage class."""

    def test_create_entity(self, tmp_path):
        """Test creating a new entity."""
        storage = SimpleStorage(tmp_path)

        meta = {
            "created": "2026-01-05",
            "last_updated": "2026-01-05",
            "sources": ["test"],
            "aliases": ["Alice"],
        }
        summary = "# Alice Smith\n\nTest entity."

        path = storage.create_entity("people/alice", meta, summary)

        assert path.exists()
        assert (path / "_meta.json").exists()
        assert (path / "_summary.md").exists()

    def test_create_entity_sets_defaults(self, tmp_path):
        """Test that create_entity sets default values."""
        storage = SimpleStorage(tmp_path)

        storage.create_entity("test/entity", {}, "# Test")

        meta = storage.read_meta("test/entity")
        assert "created" in meta
        assert "last_updated" in meta
        assert "sources" in meta
        assert "aliases" in meta

    def test_create_entity_rejects_duplicate(self, tmp_path):
        """Test that creating duplicate entity raises error."""
        storage = SimpleStorage(tmp_path)

        storage.create_entity("test/entity", {}, "# Test")

        with pytest.raises(ValueError, match="already exists"):
            storage.create_entity("test/entity", {}, "# Test 2")

    def test_read_meta(self, tmp_path):
        """Test reading metadata."""
        storage = SimpleStorage(tmp_path)

        meta = {
            "created": "2026-01-05",
            "last_updated": "2026-01-05",
            "sources": ["source1"],
            "aliases": ["Alias1"],
            "custom_field": "custom_value",
        }
        storage.create_entity("test/entity", meta, "# Test")

        read_meta = storage.read_meta("test/entity")
        assert read_meta["sources"] == ["source1"]
        assert read_meta["custom_field"] == "custom_value"

    def test_read_meta_nonexistent(self, tmp_path):
        """Test reading metadata for nonexistent entity."""
        storage = SimpleStorage(tmp_path)
        assert storage.read_meta("nonexistent/entity") is None

    def test_read_summary(self, tmp_path):
        """Test reading summary."""
        storage = SimpleStorage(tmp_path)

        summary = "# Test Entity\n\nThis is a test."
        storage.create_entity("test/entity", {}, summary)

        read_summary = storage.read_summary("test/entity")
        assert read_summary == summary

    def test_read_summary_nonexistent(self, tmp_path):
        """Test reading summary for nonexistent entity."""
        storage = SimpleStorage(tmp_path)
        assert storage.read_summary("nonexistent/entity") is None

    def test_write_meta_requires_fields(self, tmp_path):
        """Test that write_meta requires all fields."""
        storage = SimpleStorage(tmp_path)

        # Create entity first
        storage.create_entity("test/entity", {}, "# Test")

        # Try to write incomplete meta
        with pytest.raises(ValueError, match="Missing required fields"):
            storage.write_meta("test/entity", {"created": "2026-01-05"})

    def test_entity_exists(self, tmp_path):
        """Test checking entity existence."""
        storage = SimpleStorage(tmp_path)

        assert not storage.entity_exists("test/entity")

        storage.create_entity("test/entity", {}, "# Test")

        assert storage.entity_exists("test/entity")

    def test_update_entity(self, tmp_path):
        """Test updating an entity."""
        storage = SimpleStorage(tmp_path)

        storage.create_entity(
            "test/entity",
            {"created": "2026-01-01", "last_updated": "2026-01-01", "sources": [], "aliases": []},
            "# Original",
        )

        storage.update_entity("test/entity", meta={"sources": ["new_source"]})

        meta = storage.read_meta("test/entity")
        assert "new_source" in meta["sources"]
        assert meta["last_updated"] != "2026-01-01"  # Should be updated

    def test_update_entity_summary_only(self, tmp_path):
        """Test updating only the summary."""
        storage = SimpleStorage(tmp_path)

        storage.create_entity("test/entity", {}, "# Original")

        storage.update_entity("test/entity", summary="# Updated")

        assert storage.read_summary("test/entity") == "# Updated"

    def test_update_entity_nonexistent(self, tmp_path):
        """Test updating nonexistent entity raises error."""
        storage = SimpleStorage(tmp_path)

        with pytest.raises(ValueError, match="doesn't exist"):
            storage.update_entity("nonexistent/entity", meta={"sources": []})

    def test_delete_entity(self, tmp_path):
        """Test deleting an entity."""
        storage = SimpleStorage(tmp_path)

        storage.create_entity("test/entity", {}, "# Test")
        assert storage.entity_exists("test/entity")

        storage.delete_entity("test/entity")
        assert not storage.entity_exists("test/entity")

    def test_list_entities(self, tmp_path):
        """Test listing entities in a category."""
        storage = SimpleStorage(tmp_path)

        storage.create_entity("people/alice", {}, "# Alice")
        storage.create_entity("people/bob", {}, "# Bob")
        storage.create_entity("orgs/acme", {}, "# Acme")

        people = storage.list_entities("people")
        assert len(people) == 2
        assert "people/alice" in people
        assert "people/bob" in people

    def test_list_all_entities(self, tmp_path):
        """Test listing all entities."""
        storage = SimpleStorage(tmp_path)

        storage.create_entity("people/alice", {}, "# Alice")
        storage.create_entity("orgs/acme", {}, "# Acme")

        all_entities = storage.list_all_entities()
        assert len(all_entities) == 2

    def test_get_ancestors(self, tmp_path):
        """Test getting ancestor paths."""
        storage = SimpleStorage(tmp_path)

        ancestors = storage.get_ancestors("people/collaborators/alice_smith")
        assert ancestors == ["people/collaborators", "people"]

        ancestors = storage.get_ancestors("people/alice")
        assert ancestors == ["people"]

        ancestors = storage.get_ancestors("alice")
        assert ancestors == []

    def test_get_children(self, tmp_path):
        """Test getting child paths."""
        storage = SimpleStorage(tmp_path)

        storage.create_entity("people/alice", {}, "# Alice")
        storage.create_entity("people/bob", {}, "# Bob")

        children = storage.get_children("people")
        assert "people/alice" in children
        assert "people/bob" in children

    def test_get_entity_name(self, tmp_path):
        """Test getting entity display name."""
        storage = SimpleStorage(tmp_path)

        meta = {
            "created": "2026-01-05",
            "last_updated": "2026-01-05",
            "sources": [],
            "aliases": [],
            "topic": "Alice Smith",
        }
        storage.create_entity("people/alice", meta, "# Alice")

        name = storage.get_entity_name("people/alice")
        assert name == "Alice Smith"
