"""End-to-end workflow tests for kvault operations.

Tests complete user workflows (the 2-call pipeline) against the sample KB fixture.
They focus on what users actually do, with representative data.
"""

from kvault.core import operations as ops

# ============================================================================
# Create Workflow
# ============================================================================


class TestCreateWorkflow:
    """Test the full create entity workflow: write → propagate → journal."""

    def test_create_new_entity_full_workflow(self, initialized_kb):
        """Complete workflow: create → propagate → journal."""
        # 1. Write new entity
        result = ops.write_entity(
            initialized_kb,
            path="people/work/dave_wilson",
            content="# Dave Wilson\n\nMet at AI conference. Works on eval systems.\n",
            meta={
                "source": "conference",
                "aliases": ["Dave Wilson", "dave@newcorp.com"],
                "email": "dave@newcorp.com",
                "relationship_type": "colleague",
            },
            create=True,
        )
        assert result["success"]

        # 2. Propagate — get ancestors and verify chain
        result = ops.get_ancestors(initialized_kb, "people/work/dave_wilson")
        assert result["success"]
        assert result["count"] >= 2  # people/work + people + root

        # 3. Journal — log the action
        result = ops.write_journal(
            initialized_kb,
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
        entity = ops.read_entity(initialized_kb, "people/work/dave_wilson")
        assert entity is not None
        assert "Dave Wilson" in entity["content"]

    def test_create_sets_name_from_alias(self, empty_kb):
        """write_entity should auto-set 'name' from first non-email alias."""
        ops.write_entity(
            empty_kb,
            path="people/test_person",
            content="# Test Person\n",
            meta={"source": "test", "aliases": ["test@example.com", "Test Person"]},
            create=True,
        )

        entity = ops.read_entity(empty_kb, "people/test_person")
        assert entity["meta"]["name"] == "Test Person"

    def test_read_entity_includes_parent_summary(self, initialized_kb):
        """read_entity should include parent summary for sibling context."""
        entity = ops.read_entity(initialized_kb, "people/friends/alice_smith")
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
        result = ops.write_entity(
            initialized_kb,
            path="people/friends/alice_smith",
            content="# Alice Smith\n\nUpdated: now VP of Data Science at Acme.\n",
            meta={"source": "manual", "aliases": ["Alice Smith", "alice@acme.com", "Ali"]},
            create=False,
        )
        assert result["success"]

        # Verify content updated
        entity = ops.read_entity(initialized_kb, "people/friends/alice_smith")
        assert "VP of Data Science" in entity["content"]

    def test_update_nonexistent_returns_error(self, initialized_kb):
        """Updating a nonexistent entity should fail with NOT_FOUND."""
        result = ops.write_entity(
            initialized_kb,
            path="people/nobody",
            content="# Nobody\n",
            meta={"source": "manual", "aliases": []},
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
        entity = ops.read_entity(initialized_kb, "people/work/bob_jones")
        assert entity is not None

        # Delete
        result = ops.delete_entity(initialized_kb, "people/work/bob_jones")
        assert result["success"]

        # Verify gone from disk
        assert not (initialized_kb / "people" / "work" / "bob_jones").exists()

        # Verify gone from read
        entity = ops.read_entity(initialized_kb, "people/work/bob_jones")
        assert entity is None

    def test_delete_nonexistent_returns_error(self, initialized_kb):
        """Deleting a nonexistent entity should fail."""
        result = ops.delete_entity(initialized_kb, "people/nobody")
        assert not result.get("success")
        assert result["error_code"] == "not_found"


# ============================================================================
# Move Workflow
# ============================================================================


class TestMoveWorkflow:
    """Test entity move/rename."""

    def test_move_updates_path(self, initialized_kb):
        """Moving an entity should update filesystem."""
        result = ops.move_entity(
            initialized_kb,
            "people/work/bob_jones",
            "people/friends/bob_jones",
        )
        assert result["success"]

        # Old path gone
        assert not (initialized_kb / "people" / "work" / "bob_jones").exists()

        # New path exists
        assert (initialized_kb / "people" / "friends" / "bob_jones" / "_summary.md").exists()

        # Readable at new path
        entity = ops.read_entity(initialized_kb, "people/friends/bob_jones")
        assert entity is not None

    def test_move_to_existing_blocked(self, initialized_kb):
        """Moving to an existing entity path should fail."""
        result = ops.move_entity(
            initialized_kb,
            "people/friends/alice_smith",
            "people/work/sarah_chen",
        )
        assert not result.get("success")
        assert result["error_code"] == "already_exists"

    def test_move_preserves_content(self, initialized_kb):
        """Content and metadata should survive a move."""
        # Read original
        original = ops.read_entity(initialized_kb, "people/work/bob_jones")
        original_content = original["content"]

        # Move
        ops.move_entity(
            initialized_kb,
            "people/work/bob_jones",
            "people/friends/bob_jones",
        )

        # Read at new location
        moved = ops.read_entity(initialized_kb, "people/friends/bob_jones")
        assert moved is not None
        assert moved["content"] == original_content


# ============================================================================
# Propagation Workflow
# ============================================================================


class TestPropagationWorkflow:
    """Test summary propagation."""

    def test_propagate_returns_correct_ancestors(self, initialized_kb):
        """get_ancestors should return the full ancestor chain."""
        result = ops.get_ancestors(initialized_kb, "people/friends/alice_smith")
        assert result["success"]

        # Should include: people/friends, people, root (.)
        ancestor_paths = [a["path"] for a in result["ancestors"]]
        assert "people/friends" in ancestor_paths
        assert "people" in ancestor_paths
        # Root is represented as "."
        assert "." in ancestor_paths

    def test_write_summary_updates_category(self, initialized_kb):
        """Writing a category summary should succeed."""
        result = ops.write_summary(
            initialized_kb,
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
        """A properly structured sample KB should have no index_missing issues."""
        result = ops.validate_kb(initialized_kb)
        index_missing = [i for i in result.get("issues", []) if i["type"] == "index_missing"]
        # Subcategory dirs (friends/, work/) should NOT be flagged
        assert len(index_missing) == 0, f"Unexpected index_missing: {index_missing}"

    def test_new_entity_immediately_valid(self, initialized_kb):
        """Creating an entity should pass validation immediately (no rebuild needed)."""
        ops.write_entity(
            initialized_kb,
            path="people/friends/new_person",
            content="# New Person\n",
            meta={"source": "test", "aliases": ["New Person"]},
            create=True,
        )
        result = ops.validate_kb(initialized_kb)
        assert result["valid"] is True or all(
            i["severity"] == "info" for i in result.get("issues", [])
        )


# ============================================================================
# Enhanced Write Entity (2-call workflow)
# ============================================================================


class TestEnhancedWriteEntity:
    """Test write_entity returning ancestors and auto-journaling."""

    def test_write_entity_returns_ancestors(self, initialized_kb):
        """write_entity should return ancestors with current content."""
        result = ops.write_entity(
            initialized_kb,
            path="people/work/dave_wilson",
            content="# Dave Wilson\n\nMet at AI conference.\n",
            meta={
                "source": "conference",
                "aliases": ["Dave Wilson", "dave@newcorp.com"],
            },
            create=True,
        )
        assert result["success"]
        assert "ancestors" in result
        assert len(result["ancestors"]) >= 2  # people/work, people, root
        ancestor_paths = [a["path"] for a in result["ancestors"]]
        assert "people/work" in ancestor_paths
        assert "." in ancestor_paths
        # Each ancestor has current_content
        for ancestor in result["ancestors"]:
            assert "current_content" in ancestor
            assert "has_meta" in ancestor

    def test_write_entity_auto_journal(self, initialized_kb):
        """Providing reasoning should auto-log a journal entry."""
        result = ops.write_entity(
            initialized_kb,
            path="people/work/eve_martinez",
            content="# Eve Martinez\n\nML researcher.\n",
            meta={
                "source": "conference",
                "aliases": ["Eve Martinez"],
            },
            create=True,
            reasoning="Met at NeurIPS poster session",
        )
        assert result["success"]
        assert result["journal_logged"] is True
        assert result["journal_path"] is not None
        # Verify the journal file was actually written
        journal_path = initialized_kb / result["journal_path"]
        assert journal_path.exists()
        journal_content = journal_path.read_text()
        assert "Met at NeurIPS poster session" in journal_content

    def test_write_entity_without_reasoning_no_journal(self, initialized_kb):
        """Without reasoning, no journal should be logged (backward compat)."""
        result = ops.write_entity(
            initialized_kb,
            path="people/work/frank_lee",
            content="# Frank Lee\n\nEngineer.\n",
            meta={
                "source": "manual",
                "aliases": ["Frank Lee"],
            },
            create=True,
        )
        assert result["success"]
        assert result["journal_logged"] is False

    def test_write_entity_journal_uses_custom_source(self, initialized_kb):
        """journal_source should override meta.source for journal entries."""
        result = ops.write_entity(
            initialized_kb,
            path="people/work/grace_kim",
            content="# Grace Kim\n\nDesigner.\n",
            meta={
                "source": "email",
                "aliases": ["Grace Kim"],
            },
            create=True,
            reasoning="Referred by colleague",
            journal_source="referral",
        )
        assert result["success"]
        assert result["journal_logged"] is True
        journal_path = initialized_kb / result["journal_path"]
        journal_content = journal_path.read_text()
        assert "referral" in journal_content


# ============================================================================
# Batch Update Summaries
# ============================================================================


class TestBatchUpdateSummaries:
    """Test the update_summaries batch operation."""

    def test_batch_update_summaries(self, initialized_kb):
        """Should update multiple summaries in one call."""
        result = ops.update_summaries(
            initialized_kb,
            updates=[
                {"path": "people/work", "content": "# Work Contacts\n\nUpdated batch.\n"},
                {"path": "people", "content": "# People\n\nUpdated batch.\n"},
            ],
        )
        assert result["success"]
        assert result["count"] == 2
        assert "people/work" in result["updated"]
        assert "people" in result["updated"]

        # Verify files were actually written
        work_summary = (initialized_kb / "people" / "work" / "_summary.md").read_text()
        assert "Updated batch" in work_summary

    def test_batch_update_partial_failure(self, initialized_kb):
        """One bad update shouldn't block others."""
        result = ops.update_summaries(
            initialized_kb,
            updates=[
                {"path": "people/work", "content": "# Work Contacts\n\nGood update.\n"},
                {"path": None, "content": "bad"},  # Missing path
                {"path": "people", "content": "# People\n\nAlso good.\n"},
            ],
        )
        assert result["success"]  # At least some succeeded
        assert result["count"] == 2
        assert len(result.get("errors", [])) == 1

    def test_batch_update_empty_list(self, initialized_kb):
        """Empty updates list should succeed with count 0."""
        result = ops.update_summaries(initialized_kb, updates=[])
        assert result["success"]
        assert result["count"] == 0

    def test_full_2call_workflow(self, initialized_kb):
        """Integration: write_entity + update_summaries = complete 2-call workflow."""
        # Call 1: Write entity with reasoning
        write_result = ops.write_entity(
            initialized_kb,
            path="people/work/hank_brown",
            content="# Hank Brown\n\nFounder of startup.io. Met at a demo day.\n",
            meta={
                "source": "conference",
                "aliases": ["Hank Brown", "hank@startup.io"],
                "email": "hank@startup.io",
            },
            create=True,
            reasoning="Met at demo day, potential evaluation-tool adopter",
        )
        assert write_result["success"]
        assert write_result["journal_logged"] is True
        assert len(write_result["ancestors"]) >= 2

        # Call 2: Update all ancestor summaries using the ancestors from call 1
        updates = []
        for ancestor in write_result["ancestors"]:
            existing = ancestor["current_content"]
            updated = existing.rstrip() + "\n\n- Added Hank Brown (startup.io founder)\n"
            updates.append({"path": ancestor["path"], "content": updated})

        summary_result = ops.update_summaries(initialized_kb, updates=updates)
        assert summary_result["success"]
        assert summary_result["count"] == len(write_result["ancestors"])

        # Verify: entity readable, summaries updated, journal exists
        entity = ops.read_entity(initialized_kb, "people/work/hank_brown")
        assert entity is not None
        assert "startup.io" in entity["content"]

        journal_path = initialized_kb / write_result["journal_path"]
        assert journal_path.exists()


class TestPathSafety:
    """Test path traversal protections in operations."""

    def test_write_summary_rejects_root_escape(self, initialized_kb):
        """write_summary should block paths that escape KB root."""
        result = ops.write_summary(
            initialized_kb,
            path="../escaped_dir",
            content="# Escape Attempt\n",
        )
        assert not result.get("success")
        assert result["error_code"] == "validation_error"
        assert "Invalid path component" in result["error"]
        assert not (initialized_kb.parent / "escaped_dir" / "_summary.md").exists()

    def test_move_entity_rejects_invalid_source_path(self, initialized_kb):
        """move_entity should reject traversal-like source paths."""
        outside_dir = initialized_kb.parent / "outside_src"
        outside_dir.mkdir()
        (outside_dir / "_summary.md").write_text("# Outside\n")

        result = ops.move_entity(
            initialized_kb,
            source_path="../outside_src",
            target_path="people/friends/outside_src",
        )

        assert not result.get("success")
        assert result["error_code"] == "validation_error"
        assert "Invalid source path" in result["error"]
        assert outside_dir.exists()
        assert not (initialized_kb / "people" / "friends" / "outside_src").exists()

    def test_move_entity_rejects_invalid_target_path(self, initialized_kb):
        """move_entity should reject traversal-like target paths."""
        result = ops.move_entity(
            initialized_kb,
            source_path="people/work/bob_jones",
            target_path="../outside_target",
        )
        assert not result.get("success")
        assert result["error_code"] == "validation_error"
        assert "Invalid target path" in result["error"]
        assert (initialized_kb / "people" / "work" / "bob_jones").exists()

    def test_update_summaries_rejects_root_escape(self, initialized_kb):
        """update_summaries should not allow writes outside KB root."""
        result = ops.update_summaries(
            initialized_kb,
            updates=[{"path": "../escaped_batch", "content": "# Nope\n"}],
        )
        assert not result["success"]
        assert len(result.get("errors", [])) == 1
        assert not (initialized_kb.parent / "escaped_batch" / "_summary.md").exists()
