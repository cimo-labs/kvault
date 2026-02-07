"""End-to-end workflow tests for kvault MCP handlers.

Tests complete user workflows (the 6-step pipeline) against the sample KB fixture.
Following CJE's testing philosophy: test what users actually do, with real-ish data.
"""

import pytest
from pathlib import Path
from tests.conftest import SAMPLE_KB_ENTITY_COUNT

from kvault.mcp.server import (
    handle_kvault_init,
    handle_kvault_rebuild_index,
    handle_kvault_research,
    handle_kvault_write_entity,
    handle_kvault_read_entity,
    handle_kvault_delete_entity,
    handle_kvault_move_entity,
    handle_kvault_propagate_all,
    handle_kvault_write_journal,
    handle_kvault_write_summary,
    handle_kvault_search,
    handle_kvault_find_by_alias,
    handle_kvault_list_entities,
    handle_kvault_validate_kb,
)


# ============================================================================
# Create Workflow
# ============================================================================


class TestCreateWorkflow:
    """Test the full create entity workflow: research → write → propagate → journal → rebuild."""

    def test_create_new_entity_full_workflow(self, initialized_kb):
        """Complete 6-step workflow: research unknown → create → propagate → journal → rebuild → search."""
        # 1. Research — no match expected
        result = handle_kvault_research("Dave Wilson")
        assert result["suggested_action"] == "create"

        # 2. Write new entity
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

        # 3. Propagate — get ancestors and verify chain
        result = handle_kvault_propagate_all("people/work/dave_wilson")
        assert result["success"]
        assert result["count"] >= 2  # people/work + people + root

        # 4. Journal — log the action
        result = handle_kvault_write_journal(
            actions=[{"action_type": "create", "path": "people/work/dave_wilson", "reasoning": "Met at conference"}],
            source="conference",
        )
        assert result["success"]
        assert result["actions_logged"] == 1

        # 5. Rebuild — update index
        result = handle_kvault_rebuild_index()
        assert result["entity_count"] == SAMPLE_KB_ENTITY_COUNT + 1

        # 6. Verify — entity is searchable
        results = handle_kvault_search("Dave Wilson")
        assert any("dave_wilson" in r["path"] for r in results)

        # Also verify by alias
        result = handle_kvault_find_by_alias("dave@newcorp.com")
        assert result is not None
        assert "dave_wilson" in result["path"]

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

    def test_create_with_auto_rebuild(self, empty_kb):
        """auto_rebuild=True should update the index immediately."""
        handle_kvault_write_entity(
            path="people/auto_rebuild_test",
            meta={"source": "test", "aliases": ["Auto Test"]},
            content="# Auto Test\n",
            create=True,
            auto_rebuild=True,
        )

        # Should be searchable immediately without manual rebuild
        results = handle_kvault_search("Auto Test")
        assert len(results) >= 1


# ============================================================================
# Dedup Workflow
# ============================================================================


class TestDedupWorkflow:
    """Test deduplication: research finds existing entity, suggests update instead of create."""

    def test_research_finds_existing_prevents_duplicate(self, initialized_kb):
        """Researching an existing entity should suggest 'update', not 'create'."""
        result = handle_kvault_research("Alice Smith")
        assert result["suggested_action"] == "update"
        assert result["suggested_target"] is not None
        assert "alice_smith" in result["suggested_target"]

    def test_fuzzy_match_suggests_update_or_review(self, initialized_kb):
        """Fuzzy typo should still find the existing entity."""
        result = handle_kvault_research("Alice Smth")  # typo
        assert len(result["matches"]) > 0
        top = result["matches"][0]
        assert top["score"] >= 0.85
        assert "alice" in top["path"]

    def test_email_domain_match_found(self, initialized_kb):
        """Research with email should find entities at same domain."""
        result = handle_kvault_research("Someone", email="ceo@acme.com")
        matches = result["matches"]
        # Should find alice_smith (alice@acme.com → acme.com domain)
        domain_matches = [m for m in matches if m["match_type"] == "email_domain"]
        assert len(domain_matches) >= 1

    def test_unicode_dedup(self, initialized_kb):
        """José García should match Jose Garcia (accent normalization)."""
        result = handle_kvault_research("Jose Garcia")
        assert len(result["matches"]) > 0
        top = result["matches"][0]
        assert "jose" in top["path"]
        assert top["score"] >= 0.95


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

    def test_delete_removes_from_disk_and_index(self, initialized_kb):
        """Deleting an entity should remove files and update index."""
        # Verify entity exists
        entity = handle_kvault_read_entity("people/work/bob_jones")
        assert entity is not None

        # Delete
        result = handle_kvault_delete_entity("people/work/bob_jones", auto_rebuild=True)
        assert result["success"]

        # Verify gone from disk
        assert not (initialized_kb / "people" / "work" / "bob_jones").exists()

        # Verify gone from search
        results = handle_kvault_search("Bob Jones")
        bob_results = [r for r in results if "bob_jones" in r["path"]]
        assert len(bob_results) == 0

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

    def test_move_updates_path_and_index(self, initialized_kb):
        """Moving an entity should update filesystem and index."""
        result = handle_kvault_move_entity(
            "people/work/bob_jones",
            "people/friends/bob_jones",
            auto_rebuild=True,
        )
        assert result["success"]

        # Old path gone
        assert not (initialized_kb / "people" / "work" / "bob_jones").exists()

        # New path exists
        assert (initialized_kb / "people" / "friends" / "bob_jones" / "_summary.md").exists()

        # Searchable at new path
        results = handle_kvault_search("Bob Jones")
        bob_results = [r for r in results if "bob_jones" in r["path"]]
        assert len(bob_results) >= 1
        assert "friends" in bob_results[0]["path"]

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

    def test_validate_after_create_without_rebuild(self, initialized_kb):
        """Creating an entity without rebuilding should be caught by validate."""
        handle_kvault_write_entity(
            path="people/friends/new_person",
            meta={"source": "test", "aliases": ["New Person"]},
            content="# New Person\n",
            create=True,
        )
        # Don't rebuild — validate should catch it
        # (Note: validate checks filesystem vs index, so new entity should be flagged)
        # Actually, the new entity has frontmatter, so rebuild would index it.
        # But since we didn't rebuild, it won't be in the index.
        result = handle_kvault_validate_kb()
        index_missing = [i for i in result.get("issues", []) if i["type"] == "index_missing"]
        flagged_paths = [i["path"] for i in index_missing]
        assert "people/friends/new_person" in flagged_paths
