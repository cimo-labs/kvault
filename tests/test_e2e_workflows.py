"""End-to-end workflow tests for kvault MCP handlers.

Tests complete user workflows (the 5-step pipeline) against the sample KB fixture.
Following CJE's testing philosophy: test what users actually do, with real-ish data.
"""

import pytest
from pathlib import Path
from tests.conftest import SAMPLE_KB_ENTITY_COUNT

from kvault.mcp.server import (
    handle_kvault_init,
    handle_kvault_write_entity,
    handle_kvault_read_entity,
    handle_kvault_delete_entity,
    handle_kvault_move_entity,
    handle_kvault_propagate_all,
    handle_kvault_write_journal,
    handle_kvault_write_summary,
    handle_kvault_list_entities,
    handle_kvault_validate_kb,
)

# ============================================================================
# Create Workflow
# ============================================================================


class TestCreateWorkflow:
    """Test the full create entity workflow: write → propagate → journal."""

    def test_create_new_entity_full_workflow(self, initialized_kb):
        """Complete workflow: create → propagate → journal."""
        # 1. Write new entity
        result = handle_kvault_write_entity(
            path="people/work/dave_wilson",
            meta={
                "source": "conference",
                "aliases": ["Dave Wilson", "dave@newcorp.com"],
                "email": "dave@newcorp.com",
                "relationship_type": "colleague",
            },
            content="# Dave Wilson\n\nMet at AI conference. Works on eval systems.\n",
            create=True,
        )
        assert result["success"]

        # 2. Propagate — get ancestors and verify chain
        result = handle_kvault_propagate_all("people/work/dave_wilson")
        assert result["success"]
        assert result["count"] >= 2  # people/work + people + root

        # 3. Journal — log the action
        result = handle_kvault_write_journal(
            actions=[
                {
                    "action_type": "create",
                    "path": "people/work/dave_wilson",
                    "reasoning": "Met at conference",
                }
            ],
            source="conference",
        )
        assert result["success"]
        assert result["actions_logged"] == 1

        # 4. Verify — entity is readable
        entity = handle_kvault_read_entity("people/work/dave_wilson")
        assert entity is not None
        assert "Dave Wilson" in entity["content"]

    def test_create_sets_name_from_alias(self, empty_kb):
        """write_entity should auto-set 'name' from first non-email alias."""
        handle_kvault_write_entity(
            path="people/test_person",
            meta={"source": "test", "aliases": ["test@example.com", "Test Person"]},
            content="# Test Person\n",
            create=True,
        )

        entity = handle_kvault_read_entity("people/test_person")
        assert entity["meta"]["name"] == "Test Person"

    def test_read_entity_includes_parent_summary(self, initialized_kb):
        """read_entity should include parent summary for sibling context."""
        entity = handle_kvault_read_entity("people/friends/alice_smith")
        assert entity is not None
        assert "parent_summary" in entity
        assert "parent_path" in entity
        assert entity["parent_path"] == "people/friends"


# ============================================================================
# Update Workflow
# ============================================================================


class TestUpdateWorkflow:
    """Test updating existing entities."""

    def test_update_existing_entity(self, initialized_kb):
        """Update entity content while preserving metadata."""
        result = handle_kvault_write_entity(
            path="people/friends/alice_smith",
            meta={"source": "manual", "aliases": ["Alice Smith", "alice@acme.com", "Ali"]},
            content="# Alice Smith\n\nUpdated: now VP of Data Science at Acme.\n",
            create=False,
        )
        assert result["success"]

        # Verify content updated
        entity = handle_kvault_read_entity("people/friends/alice_smith")
        assert "VP of Data Science" in entity["content"]

    def test_update_nonexistent_returns_error(self, initialized_kb):
        """Updating a nonexistent entity should fail with NOT_FOUND."""
        result = handle_kvault_write_entity(
            path="people/nobody",
            meta={"source": "manual", "aliases": []},
            content="# Nobody\n",
            create=False,
        )
        assert not result.get("success")
        assert result["error_code"] == "not_found"


# ============================================================================
# Delete Workflow
# ============================================================================


class TestDeleteWorkflow:
    """Test entity deletion."""

    def test_delete_removes_from_disk(self, initialized_kb):
        """Deleting an entity should remove files from disk."""
        # Verify entity exists
        entity = handle_kvault_read_entity("people/work/bob_jones")
        assert entity is not None

        # Delete
        result = handle_kvault_delete_entity("people/work/bob_jones")
        assert result["success"]

        # Verify gone from disk
        assert not (initialized_kb / "people" / "work" / "bob_jones").exists()

        # Verify gone from read
        entity = handle_kvault_read_entity("people/work/bob_jones")
        assert entity is None

    def test_delete_nonexistent_returns_error(self, initialized_kb):
        """Deleting a nonexistent entity should fail."""
        result = handle_kvault_delete_entity("people/nobody")
        assert not result.get("success")
        assert result["error_code"] == "not_found"


# ============================================================================
# Move Workflow
# ============================================================================


class TestMoveWorkflow:
    """Test entity move/rename."""

    def test_move_updates_path(self, initialized_kb):
        """Moving an entity should update filesystem."""
        result = handle_kvault_move_entity(
            "people/work/bob_jones",
            "people/friends/bob_jones",
        )
        assert result["success"]

        # Old path gone
        assert not (initialized_kb / "people" / "work" / "bob_jones").exists()

        # New path exists
        assert (initialized_kb / "people" / "friends" / "bob_jones" / "_summary.md").exists()

        # Readable at new path
        entity = handle_kvault_read_entity("people/friends/bob_jones")
        assert entity is not None

    def test_move_to_existing_blocked(self, initialized_kb):
        """Moving to an existing entity path should fail."""
        result = handle_kvault_move_entity(
            "people/friends/alice_smith",
            "people/work/sarah_chen",
        )
        assert not result.get("success")
        assert result["error_code"] == "already_exists"

    def test_move_preserves_content(self, initialized_kb):
        """Content and metadata should survive a move."""
        # Read original
        original = handle_kvault_read_entity("people/work/bob_jones")
        original_content = original["content"]

        # Move
        handle_kvault_move_entity(
            "people/work/bob_jones",
            "people/friends/bob_jones",
        )

        # Read at new location
        moved = handle_kvault_read_entity("people/friends/bob_jones")
        assert moved is not None
        assert moved["content"] == original_content


# ============================================================================
# Propagation Workflow
# ============================================================================


class TestPropagationWorkflow:
    """Test summary propagation."""

    def test_propagate_returns_correct_ancestors(self, initialized_kb):
        """propagate_all should return the full ancestor chain."""
        result = handle_kvault_propagate_all("people/friends/alice_smith")
        assert result["success"]

        # Should include: people/friends, people, root (.)
        ancestor_paths = [a["path"] for a in result["ancestors"]]
        assert "people/friends" in ancestor_paths
        assert "people" in ancestor_paths
        # Root is represented as "."
        assert "." in ancestor_paths

    def test_write_summary_updates_category(self, initialized_kb):
        """Writing a category summary should succeed."""
        result = handle_kvault_write_summary(
            path="people/friends",
            content="# Friends\n\nUpdated: Alice, José, and new contacts.\n",
        )
        assert result["success"]


# ============================================================================
# Validation Workflow
# ============================================================================


class TestValidationWorkflow:
    """Test KB validation after various operations."""

    def test_clean_kb_validates(self, initialized_kb):
        """A properly indexed sample KB should have no index_missing issues."""
        result = handle_kvault_validate_kb()
        index_missing = [i for i in result.get("issues", []) if i["type"] == "index_missing"]
        # Subcategory dirs (friends/, work/) should NOT be flagged
        assert len(index_missing) == 0, f"Unexpected index_missing: {index_missing}"

    def test_new_entity_immediately_valid(self, initialized_kb):
        """Creating an entity should pass validation immediately (no rebuild needed)."""
        handle_kvault_write_entity(
            path="people/friends/new_person",
            meta={"source": "test", "aliases": ["New Person"]},
            content="# New Person\n",
            create=True,
        )
        # No index means no stale-state issues — new entity is immediately valid
        result = handle_kvault_validate_kb()
        assert result["valid"] is True or all(
            i["severity"] == "info" for i in result.get("issues", [])
        )
