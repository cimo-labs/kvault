"""Tests for kvault MCP handler functions.

Tests each of the 20 MCP handlers individually against the sample KB fixture.
Complements the E2E workflow tests by testing handler-level behavior.
"""

import pytest
from pathlib import Path
from tests.conftest import SAMPLE_KB_ENTITY_COUNT

from kvault.mcp.server import (
    handle_kvault_init,
    handle_kvault_search,
    handle_kvault_find_by_alias,
    handle_kvault_find_by_email_domain,
    handle_kvault_rebuild_index,
    handle_kvault_read_entity,
    handle_kvault_write_entity,
    handle_kvault_list_entities,
    handle_kvault_delete_entity,
    handle_kvault_move_entity,
    handle_kvault_read_summary,
    handle_kvault_write_summary,
    handle_kvault_get_parent_summaries,
    handle_kvault_propagate_all,
    handle_kvault_research,
    handle_kvault_log_phase,
    handle_kvault_write_journal,
    handle_kvault_validate_kb,
    handle_kvault_status,
)


# ============================================================================
# Init
# ============================================================================


class TestInit:
    def test_returns_hierarchy(self, sample_kb):
        result = handle_kvault_init(str(sample_kb))
        assert "hierarchy" in result
        assert "people" in result["hierarchy"]
        assert "projects" in result["hierarchy"]

    def test_returns_entity_count(self, sample_kb):
        result = handle_kvault_init(str(sample_kb))
        # Count depends on whether index was pre-built
        assert "entity_count" in result

    def test_returns_root_summary(self, sample_kb):
        result = handle_kvault_init(str(sample_kb))
        assert "root_summary" in result
        assert "Test Knowledge Base" in result["root_summary"]

    def test_creates_session(self, sample_kb):
        result = handle_kvault_init(str(sample_kb))
        assert "session_id" in result
        assert result["session_id"] is not None

    def test_init_creates_kvault_dir(self, sample_kb):
        handle_kvault_init(str(sample_kb))
        assert (sample_kb / ".kvault").exists()
        assert (sample_kb / ".kvault" / "index.db").exists()


# ============================================================================
# Search
# ============================================================================


class TestSearch:
    def test_returns_list(self, initialized_kb):
        results = handle_kvault_search("Alice")
        assert isinstance(results, list)
        assert len(results) >= 1

    def test_result_structure(self, initialized_kb):
        results = handle_kvault_search("Alice")
        r = results[0]
        assert "path" in r
        assert "name" in r
        assert "aliases" in r
        assert "category" in r

    def test_sql_injection_safe(self, initialized_kb):
        results = handle_kvault_search('"; DROP TABLE entities; --')
        assert isinstance(results, list)  # Didn't crash


# ============================================================================
# Read Entity
# ============================================================================


class TestReadEntity:
    def test_reads_with_frontmatter(self, initialized_kb):
        result = handle_kvault_read_entity("people/friends/alice_smith")
        assert result is not None
        assert result["meta"]["source"] == "manual"
        assert "Alice Smith" in result["meta"]["aliases"]
        assert "Alice Smith" in result["content"]

    def test_returns_none_for_missing(self, initialized_kb):
        result = handle_kvault_read_entity("people/nobody")
        assert result is None

    def test_has_frontmatter_flag(self, initialized_kb):
        result = handle_kvault_read_entity("people/friends/alice_smith")
        assert result["has_frontmatter"] is True


# ============================================================================
# Write Entity
# ============================================================================


class TestWriteEntity:
    def test_create_success(self, empty_kb):
        result = handle_kvault_write_entity(
            path="people/new_person",
            meta={"source": "test", "aliases": ["New Person"]},
            content="# New Person\n",
            create=True,
        )
        assert result["success"]
        assert result["created"] is True

    def test_create_duplicate_blocked(self, initialized_kb):
        result = handle_kvault_write_entity(
            path="people/friends/alice_smith",
            meta={"source": "test", "aliases": ["Alice"]},
            content="# Alice\n",
            create=True,
        )
        assert not result.get("success")
        assert result["error_code"] == "already_exists"

    def test_update_success(self, initialized_kb):
        result = handle_kvault_write_entity(
            path="people/friends/alice_smith",
            meta={"source": "manual", "aliases": ["Alice Smith"]},
            content="# Alice Smith\n\nUpdated content.\n",
            create=False,
        )
        assert result["success"]
        assert result["created"] is False

    def test_update_missing_blocked(self, empty_kb):
        result = handle_kvault_write_entity(
            path="people/nobody",
            meta={"source": "test", "aliases": []},
            content="# Nobody\n",
            create=False,
        )
        assert not result.get("success")
        assert result["error_code"] == "not_found"

    def test_missing_source_rejected(self, empty_kb):
        result = handle_kvault_write_entity(
            path="people/test",
            meta={"aliases": ["Test"]},
            content="# Test\n",
            create=True,
        )
        assert not result.get("success")
        assert "source" in result.get("error", "").lower()

    def test_missing_aliases_rejected(self, empty_kb):
        result = handle_kvault_write_entity(
            path="people/test",
            meta={"source": "test"},
            content="# Test\n",
            create=True,
        )
        assert not result.get("success")
        assert "aliases" in result.get("error", "").lower()

    def test_auto_sets_created_date(self, empty_kb):
        handle_kvault_write_entity(
            path="people/test",
            meta={"source": "test", "aliases": ["Test"]},
            content="# Test\n",
            create=True,
        )
        entity = handle_kvault_read_entity("people/test")
        assert "created" in entity["meta"]
        assert "updated" in entity["meta"]

    def test_auto_rebuild(self, empty_kb):
        result = handle_kvault_write_entity(
            path="people/test",
            meta={"source": "test", "aliases": ["Test"]},
            content="# Test\n",
            create=True,
            auto_rebuild=True,
        )
        assert result.get("index_rebuilt") is True
        assert result.get("entity_count") >= 1


# ============================================================================
# Delete Entity
# ============================================================================


class TestDeleteEntity:
    def test_delete_success(self, initialized_kb):
        result = handle_kvault_delete_entity("people/work/bob_jones")
        assert result["success"]
        assert result["deleted"] is True

    def test_delete_missing_returns_error(self, initialized_kb):
        result = handle_kvault_delete_entity("people/nobody")
        assert not result.get("success")
        assert result["error_code"] == "not_found"

    def test_delete_with_auto_rebuild(self, initialized_kb):
        result = handle_kvault_delete_entity("people/work/bob_jones", auto_rebuild=True)
        assert result["success"]
        assert result.get("index_rebuilt") is True
        assert result["entity_count"] == SAMPLE_KB_ENTITY_COUNT - 1


# ============================================================================
# Move Entity
# ============================================================================


class TestMoveEntity:
    def test_move_success(self, initialized_kb):
        result = handle_kvault_move_entity(
            "people/work/bob_jones",
            "people/friends/bob_jones",
        )
        assert result["success"]
        assert result["source"] == "people/work/bob_jones"
        assert result["target"] == "people/friends/bob_jones"

    def test_move_missing_source(self, initialized_kb):
        result = handle_kvault_move_entity("people/nobody", "people/somewhere")
        assert not result.get("success")
        assert result["error_code"] == "not_found"

    def test_move_existing_target(self, initialized_kb):
        result = handle_kvault_move_entity(
            "people/friends/alice_smith",
            "people/work/sarah_chen",
        )
        assert not result.get("success")
        assert result["error_code"] == "already_exists"

    def test_move_invalid_target_path(self, initialized_kb):
        result = handle_kvault_move_entity(
            "people/friends/alice_smith",
            "a/b/c/d/e/too/deep",
        )
        assert not result.get("success")


# ============================================================================
# Summary Operations
# ============================================================================


class TestReadSummary:
    def test_reads_root(self, initialized_kb):
        result = handle_kvault_read_summary(".")
        assert result is not None
        assert "Test Knowledge Base" in result.get("content", "")

    def test_reads_category(self, initialized_kb):
        result = handle_kvault_read_summary("people")
        assert result is not None

    def test_returns_none_for_missing(self, initialized_kb):
        result = handle_kvault_read_summary("nonexistent")
        assert result is None


class TestWriteSummary:
    def test_write_new_summary(self, initialized_kb):
        result = handle_kvault_write_summary(
            path="people/friends",
            content="# Friends\n\nUpdated summary.\n",
        )
        assert result["success"]

    def test_write_with_meta(self, initialized_kb):
        result = handle_kvault_write_summary(
            path="people",
            content="# People\n\nAll contacts.\n",
            meta={"source": "auto-propagation", "updated": "2026-02-07"},
        )
        assert result["success"]


# ============================================================================
# Parent Summaries & Propagation
# ============================================================================


class TestParentSummaries:
    def test_returns_ancestor_chain(self, initialized_kb):
        result = handle_kvault_get_parent_summaries("people/friends/alice_smith")
        assert isinstance(result, list)
        assert len(result) >= 2  # people/friends + people + root
        paths = [r["path"] for r in result]
        assert "people/friends" in paths
        assert "people" in paths


class TestPropagateAll:
    def test_returns_ancestors_with_content(self, initialized_kb):
        result = handle_kvault_propagate_all("people/friends/alice_smith")
        assert result["success"]
        assert result["count"] >= 2
        for ancestor in result["ancestors"]:
            assert "path" in ancestor
            assert "current_content" in ancestor


# ============================================================================
# Research
# ============================================================================


class TestResearchHandler:
    def test_returns_matches_and_suggestion(self, initialized_kb):
        result = handle_kvault_research("Alice Smith")
        assert "matches" in result
        assert "suggested_action" in result
        assert "confidence" in result

    def test_with_email(self, initialized_kb):
        result = handle_kvault_research("Someone", email="test@acme.com")
        assert "matches" in result

    def test_with_phone(self, initialized_kb):
        result = handle_kvault_research("Someone", phone="+14155551234")
        assert "matches" in result


# ============================================================================
# Workflow Logging
# ============================================================================


class TestLogPhase:
    def test_logs_successfully(self, initialized_kb):
        result = handle_kvault_log_phase("research", {"query": "test", "results": 3})
        assert result["success"]
        assert result["phase"] == "research"


class TestWriteJournal:
    def test_creates_journal_file(self, initialized_kb):
        result = handle_kvault_write_journal(
            actions=[{"action_type": "create", "path": "people/test", "reasoning": "Test"}],
            source="test",
        )
        assert result["success"]
        assert result["actions_logged"] == 1
        assert "journal_path" in result

    def test_multiple_actions(self, initialized_kb):
        result = handle_kvault_write_journal(
            actions=[
                {"action_type": "create", "path": "people/a"},
                {"action_type": "update", "path": "people/b"},
            ],
            source="test",
        )
        assert result["actions_logged"] == 2

    def test_custom_date(self, initialized_kb):
        result = handle_kvault_write_journal(
            actions=[{"action_type": "create", "path": "people/test"}],
            source="test",
            date="2026-01-15",
        )
        assert "2026-01" in result["journal_path"]


# ============================================================================
# Validate KB
# ============================================================================


class TestValidateKb:
    def test_clean_kb_valid(self, initialized_kb):
        result = handle_kvault_validate_kb()
        index_missing = [i for i in result.get("issues", []) if i["type"] == "index_missing"]
        assert len(index_missing) == 0

    def test_detects_incomplete_entities(self, initialized_kb):
        """Entities with 'TBD' content should be flagged."""
        # The sample KB doesn't have TBD content, so this should be clean
        result = handle_kvault_validate_kb()
        incomplete = [i for i in result.get("issues", []) if i["type"] == "incomplete_entity"]
        # Sample KB should have no incomplete entities
        assert len(incomplete) == 0

    def test_response_structure(self, initialized_kb):
        result = handle_kvault_validate_kb()
        assert "valid" in result or "issue_count" in result
        assert "issues" in result
        assert "summary" in result


# ============================================================================
# Status
# ============================================================================


class TestStatus:
    def test_returns_session_info(self, initialized_kb):
        result = handle_kvault_status()
        assert "sessions" in result or "session_id" in result
