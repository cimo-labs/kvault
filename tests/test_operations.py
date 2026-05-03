"""Tests for kvault.core.operations — the shared stateless operations layer.

These tests call operations functions directly (no MCP server initialization).
"""

import shutil

import pytest
from kvault.core import operations as ops

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def ops_kb(sample_kb):
    """Sample KB ready for operations (no MCP init needed)."""
    # Ensure .kvault dir exists (operations don't create it)
    (sample_kb / ".kvault").mkdir(exist_ok=True)
    return sample_kb


@pytest.fixture
def empty_ops_kb(tmp_path):
    """Fresh KB with categories, no entities."""
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "_summary.md").write_text("# Test KB\n\nEmpty knowledge base for testing.\n")
    (kb / "people").mkdir()
    (kb / "people" / "_summary.md").write_text("# People\n\nAll contacts.\n")
    (kb / "people" / "friends").mkdir()
    (kb / "people" / "friends" / "_summary.md").write_text("# Friends\n\nFriends list.\n")
    (kb / "projects").mkdir()
    (kb / "projects" / "_summary.md").write_text("# Projects\n\nAll projects.\n")
    (kb / ".kvault").mkdir()
    return kb


def _add_child(kb, parent, name, content=None):
    path = kb / parent / name if parent != "." else kb / name
    path.mkdir(parents=True, exist_ok=True)
    body = content or f"# {name.replace('_', ' ').title()}\n\nChild summary for {name}.\n"
    (path / "_summary.md").write_text(body)
    return path


# ============================================================================
# Read operations
# ============================================================================


class TestReadEntity:
    def test_read_existing_entity(self, ops_kb):
        result = ops.read_entity(ops_kb, "people/friends/alice_smith")
        assert result is not None
        assert result["path"] == "people/friends/alice_smith"
        assert "content" in result

    def test_read_nonexistent_returns_none(self, ops_kb):
        result = ops.read_entity(ops_kb, "people/nobody")
        assert result is None

    def test_read_includes_parent_summary(self, ops_kb):
        result = ops.read_entity(ops_kb, "people/friends/alice_smith")
        assert result is not None
        assert "parent_summary" in result
        assert "parent_path" in result
        assert result["parent_path"] == "people/friends"


class TestReadSummary:
    def test_read_existing_summary(self, ops_kb):
        result = ops.read_summary(ops_kb, "people/friends")
        assert result is not None
        assert result["path"] == "people/friends"

    def test_read_root_summary(self, ops_kb):
        result = ops.read_summary(ops_kb, ".")
        assert result is not None

    def test_read_nonexistent_returns_none(self, ops_kb):
        result = ops.read_summary(ops_kb, "nonexistent/path")
        assert result is None


class TestReadNode:
    def test_read_root_has_no_parent(self, ops_kb):
        result = ops.read_node(ops_kb, ".")
        assert result is not None
        assert result["kind"] == "root"
        assert result["parent"] is None
        assert any(child["path"] == "people" for child in result["children"])

    def test_read_branch_includes_immediate_parent(self, ops_kb):
        result = ops.read_node(ops_kb, "people/friends")
        assert result is not None
        assert result["kind"] == "category"
        assert result["parent"]["path"] == "people"

    def test_read_leaf_includes_immediate_parent(self, ops_kb):
        result = ops.read_node(ops_kb, "people/friends/alice_smith")
        assert result is not None
        assert result["kind"] == "entity"
        assert result["parent"]["path"] == "people/friends"

    def test_read_node_parent_modes(self, ops_kb):
        none = ops.read_node(ops_kb, "people/friends/alice_smith", parents="none")
        assert none["parent"] is None

        all_parents = ops.read_node(ops_kb, "people/friends/alice_smith", parents="all")
        assert [parent["path"] for parent in all_parents["parents"]] == [
            "people/friends",
            "people",
            ".",
        ]

    def test_read_node_rejects_escape(self, ops_kb):
        assert ops.read_node(ops_kb, "../escaped") is None


# ============================================================================
# Write operations
# ============================================================================


class TestWriteEntity:
    def test_create_new_entity(self, empty_ops_kb):
        result = ops.write_entity(
            empty_ops_kb,
            "people/friends/bob",
            "# Bob\n\nA friend.\n",
            meta={"source": "test", "aliases": ["Bob"]},
            create=True,
        )
        assert result["success"]
        assert result["path"] == "people/friends/bob"
        assert result["created"] is True
        assert "ancestors" in result
        assert len(result["ancestors"]) >= 1

    def test_create_duplicate_fails(self, ops_kb):
        result = ops.write_entity(
            ops_kb,
            "people/friends/alice_smith",
            "# Duplicate\n",
            meta={"source": "test", "aliases": []},
            create=True,
        )
        assert not result["success"]
        assert result["error_code"] == "already_exists"

    def test_update_existing(self, ops_kb):
        result = ops.write_entity(
            ops_kb,
            "people/friends/alice_smith",
            "# Alice Smith\n\nUpdated content.\n",
            meta={"source": "manual", "aliases": ["Alice Smith"]},
            create=False,
        )
        assert result["success"]
        assert result["created"] is False

    def test_update_nonexistent_fails(self, empty_ops_kb):
        result = ops.write_entity(
            empty_ops_kb,
            "people/nobody",
            "# Nobody\n",
            meta={"source": "test", "aliases": []},
            create=False,
        )
        assert not result["success"]
        assert result["error_code"] == "not_found"

    def test_auto_journal_with_reasoning(self, empty_ops_kb):
        result = ops.write_entity(
            empty_ops_kb,
            "people/friends/carol",
            "# Carol\n\nMet at conference.\n",
            meta={"source": "conference", "aliases": ["Carol"]},
            create=True,
            reasoning="Met at NeurIPS",
        )
        assert result["success"]
        assert result["journal_logged"] is True
        assert result["journal_path"] is not None
        journal_full = empty_ops_kb / result["journal_path"]
        assert journal_full.exists()
        assert "NeurIPS" in journal_full.read_text()

    def test_no_journal_without_reasoning(self, empty_ops_kb):
        result = ops.write_entity(
            empty_ops_kb,
            "people/friends/dave",
            "# Dave\n",
            meta={"source": "test", "aliases": ["Dave"]},
            create=True,
        )
        assert result["success"]
        assert result["journal_logged"] is False

    def test_default_source_auto_cli(self, empty_ops_kb):
        result = ops.write_entity(
            empty_ops_kb,
            "people/friends/eve",
            "# Eve\n",
            create=True,
        )
        assert result["success"]
        entity = ops.read_entity(empty_ops_kb, "people/friends/eve")
        assert entity["meta"]["source"] == "auto:cli"

    def test_custom_default_source(self, empty_ops_kb):
        result = ops.write_entity(
            empty_ops_kb,
            "people/friends/frank",
            "# Frank\n",
            create=True,
            default_source="auto:mcp",
        )
        assert result["success"]
        entity = ops.read_entity(empty_ops_kb, "people/friends/frank")
        assert entity["meta"]["source"] == "auto:mcp"

    def test_create_deep_entity_path(self, empty_ops_kb):
        deep_path = "people/contacts/professional/education/person"
        result = ops.write_entity(
            empty_ops_kb,
            deep_path,
            "# Person\n\nDeep contact path.\n",
            create=True,
        )
        assert result["success"]
        assert (empty_ops_kb / deep_path / "_summary.md").exists()


class TestWriteNode:
    def test_update_branch_preserves_frontmatter(self, empty_ops_kb):
        (empty_ops_kb / "people" / "_summary.md").write_text(
            "---\nsource: seed\naliases: []\ncreated: '2026-01-01'\nupdated: '2026-01-01'\n---\n\n# People\n\nAll contacts.\n"
        )
        result = ops.write_node(empty_ops_kb, "people", "# People\n\nUpdated contacts.\n")
        assert result["success"]
        node = ops.read_node(empty_ops_kb, "people")
        assert node["meta"]["source"] == "seed"
        assert node["meta"]["aliases"] == []

    def test_create_branch_node(self, empty_ops_kb):
        result = ops.write_node(
            empty_ops_kb,
            "projects/archive",
            "# Archive\n\nPast work.\n",
            create=True,
        )
        assert result["success"]
        node = ops.read_node(empty_ops_kb, "projects/archive")
        assert node["kind"] == "entity"
        assert node["parent"]["path"] == "projects"

    def test_write_node_returns_propagation_targets(self, empty_ops_kb):
        result = ops.write_node(
            empty_ops_kb,
            "people/friends/new_person",
            "# New Person\n\nNew contact.\n",
            create=True,
        )
        assert result["success"]
        assert [ancestor["path"] for ancestor in result["ancestors"]] == [
            "people/friends",
            "people",
            ".",
        ]


# ============================================================================
# Strict parent summary updates
# ============================================================================


class TestStrictSummaryUpdates:
    def test_prepare_returns_parent_children_digest_and_no_hint_under_threshold(self, empty_ops_kb):
        result = ops.prepare_summary_update(empty_ops_kb, "people")

        assert result["success"] is True
        assert result["path"] == "people"
        assert result["parent"]["path"] == "people"
        assert [child["path"] for child in result["children"]] == ["people/friends"]
        assert result["child_count"] == 1
        assert result["digest_algorithm"] == ops.SUMMARY_UPDATE_DIGEST_ALGORITHM
        assert result["children_digest"].startswith("sha256:")
        assert result["max_direct_children"] == ops.MAX_DIRECT_CHILDREN
        assert result["hierarchy_hint"] is None

    def test_prepare_root_works(self, empty_ops_kb):
        result = ops.prepare_summary_update(empty_ops_kb, ".")

        assert result["success"] is True
        assert result["parent"]["kind"] == "root"
        assert sorted(child["path"] for child in result["children"]) == ["people", "projects"]

    def test_prepare_ignores_hidden_directories_and_grandchildren(self, empty_ops_kb):
        _add_child(empty_ops_kb, "people/friends", "alice")
        hidden = empty_ops_kb / "people" / ".hidden" / "secret"
        hidden.mkdir(parents=True)
        (hidden / "_summary.md").write_text("# Secret\n\nHidden child.\n")

        result = ops.prepare_summary_update(empty_ops_kb, "people")

        assert result["success"] is True
        assert [child["path"] for child in result["children"]] == ["people/friends"]

    def test_digest_is_stable_when_children_are_unchanged(self, empty_ops_kb):
        first = ops.prepare_summary_update(empty_ops_kb, "people")
        second = ops.prepare_summary_update(empty_ops_kb, "people")

        assert first["children_digest"] == second["children_digest"]

    def test_digest_changes_when_direct_child_body_changes(self, empty_ops_kb):
        before = ops.prepare_summary_update(empty_ops_kb, "people")
        (empty_ops_kb / "people" / "friends" / "_summary.md").write_text(
            "# Friends\n\nUpdated direct child body.\n"
        )

        after = ops.prepare_summary_update(empty_ops_kb, "people")

        assert before["children_digest"] != after["children_digest"]

    def test_digest_changes_when_direct_child_frontmatter_changes(self, empty_ops_kb):
        before = ops.prepare_summary_update(empty_ops_kb, "people")
        (empty_ops_kb / "people" / "friends" / "_summary.md").write_text(
            "---\nsource: seed\naliases: [Friends]\n---\n\n# Friends\n\nFriends list.\n"
        )

        after = ops.prepare_summary_update(empty_ops_kb, "people")

        assert before["children_digest"] != after["children_digest"]

    def test_digest_changes_when_direct_child_is_added_or_removed(self, empty_ops_kb):
        before = ops.prepare_summary_update(empty_ops_kb, "people")
        added = _add_child(empty_ops_kb, "people", "contacts")

        after_add = ops.prepare_summary_update(empty_ops_kb, "people")
        shutil.rmtree(added)
        after_remove = ops.prepare_summary_update(empty_ops_kb, "people")

        assert before["children_digest"] != after_add["children_digest"]
        assert after_remove["children_digest"] == before["children_digest"]

    def test_prepare_returns_hierarchy_hint_above_threshold(self, empty_ops_kb):
        for index in range(ops.MAX_DIRECT_CHILDREN + 1):
            _add_child(empty_ops_kb, "projects", f"child_{index}")

        result = ops.prepare_summary_update(empty_ops_kb, "projects")

        assert result["success"] is True
        assert result["child_count"] == ops.MAX_DIRECT_CHILDREN + 1
        assert result["hierarchy_hint"] == {
            "code": "too_many_direct_children",
            "message": "Parent has 11 direct children; consider introducing intermediate branch nodes.",
            "child_count": 11,
            "max_direct_children": ops.MAX_DIRECT_CHILDREN,
        }

    def test_write_parent_summary_succeeds_with_fresh_digest(self, empty_ops_kb):
        prepared = ops.prepare_summary_update(empty_ops_kb, "people")

        result = ops.write_parent_summary(
            empty_ops_kb,
            "people",
            "# People\n\nUpdated from direct child summaries.\n",
            prepared["children_digest"],
        )

        assert result["success"] is True
        assert (empty_ops_kb / "people" / "_summary.md").read_text() == (
            "# People\n\nUpdated from direct child summaries.\n"
        )

    def test_write_parent_summary_preserves_frontmatter_when_meta_omitted(self, empty_ops_kb):
        (empty_ops_kb / "people" / "_summary.md").write_text(
            "---\nsource: seed\naliases: [People]\n---\n\n# People\n\nAll contacts.\n"
        )
        prepared = ops.prepare_summary_update(empty_ops_kb, "people")

        result = ops.write_parent_summary(
            empty_ops_kb,
            "people",
            "# People\n\nUpdated with preserved metadata.\n",
            prepared["children_digest"],
        )

        assert result["success"] is True
        node = ops.read_node(empty_ops_kb, "people")
        assert node["meta"]["source"] == "seed"
        assert node["meta"]["aliases"] == ["People"]
        assert "Updated with preserved metadata" in node["content"]

    def test_write_parent_summary_applies_explicit_meta(self, empty_ops_kb):
        prepared = ops.prepare_summary_update(empty_ops_kb, "people")

        result = ops.write_parent_summary(
            empty_ops_kb,
            "people",
            "# People\n\nUpdated with explicit metadata.\n",
            prepared["children_digest"],
            meta={"source": "manual", "aliases": ["Contacts"]},
        )

        assert result["success"] is True
        node = ops.read_node(empty_ops_kb, "people")
        assert node["meta"] == {"source": "manual", "aliases": ["Contacts"]}

    def test_write_parent_summary_rejects_stale_digest(self, empty_ops_kb):
        prepared = ops.prepare_summary_update(empty_ops_kb, "people")
        _add_child(empty_ops_kb, "people", "contacts")

        result = ops.write_parent_summary(
            empty_ops_kb,
            "people",
            "# People\n\nStale update.\n",
            prepared["children_digest"],
        )

        assert result["success"] is False
        assert result["error_code"] == "workflow_error"
        assert result["details"]["received_digest"] == prepared["children_digest"]
        assert result["details"]["expected_digest"].startswith("sha256:")

    def test_write_parent_summary_still_succeeds_above_threshold_with_fresh_digest(
        self, empty_ops_kb
    ):
        for index in range(ops.MAX_DIRECT_CHILDREN + 1):
            _add_child(empty_ops_kb, "projects", f"child_{index}")
        prepared = ops.prepare_summary_update(empty_ops_kb, "projects")

        result = ops.write_parent_summary(
            empty_ops_kb,
            "projects",
            "# Projects\n\nComprehensive summary despite many direct children.\n",
            prepared["children_digest"],
        )

        assert result["success"] is True
        assert result["hierarchy_hint"]["code"] == "too_many_direct_children"

    def test_write_parent_summary_rejects_missing_digest(self, empty_ops_kb):
        result = ops.write_parent_summary(empty_ops_kb, "people", "# People\n", "")

        assert result["success"] is False
        assert result["error_code"] == "validation_error"

    def test_prepare_rejects_escape_paths(self, empty_ops_kb):
        result = ops.prepare_summary_update(empty_ops_kb, "../escaped")

        assert result["success"] is False
        assert result["error_code"] == "validation_error"

    def test_prepare_missing_parent_returns_not_found(self, empty_ops_kb):
        result = ops.prepare_summary_update(empty_ops_kb, "people/missing")

        assert result["success"] is False
        assert result["error_code"] == "not_found"


# ============================================================================
# Write + propagate workflow
# ============================================================================


class TestWritePropagateWorkflow:
    def test_full_2call_workflow(self, empty_ops_kb):
        """Integration: write_entity + update_summaries."""
        # Call 1: Write
        write_result = ops.write_entity(
            empty_ops_kb,
            "people/friends/gina",
            "# Gina\n\nNew friend.\n",
            meta={"source": "manual", "aliases": ["Gina"]},
            create=True,
            reasoning="Added from contact list",
        )
        assert write_result["success"]
        assert write_result["journal_logged"] is True
        assert len(write_result["ancestors"]) >= 1

        # Call 2: Update summaries
        updates = []
        for ancestor in write_result["ancestors"]:
            existing = ancestor["current_content"]
            updated = existing.rstrip() + "\n\n- Added Gina\n"
            updates.append({"path": ancestor["path"], "content": updated})

        summary_result = ops.update_summaries(empty_ops_kb, updates)
        assert summary_result["success"]
        assert summary_result["count"] == len(write_result["ancestors"])

        # Verify entity is readable
        entity = ops.read_entity(empty_ops_kb, "people/friends/gina")
        assert entity is not None
        assert "New friend" in entity["content"]

    def test_strict_parent_update_workflow_detects_out_of_order_digest(self, empty_ops_kb):
        write_result = ops.write_node(
            empty_ops_kb,
            "people/friends/gina",
            "# Gina\n\nNew friend.\n",
            create=True,
        )
        assert write_result["success"] is True

        stale_people = ops.prepare_summary_update(empty_ops_kb, "people")
        touched = []
        for path in ["people/friends", "people", "."]:
            prepared = ops.prepare_summary_update(empty_ops_kb, path)
            content = prepared["parent"]["content"].rstrip() + f"\n\nUpdated rollup for {path}.\n"
            result = ops.write_parent_summary(
                empty_ops_kb,
                path,
                content,
                prepared["children_digest"],
            )
            assert result["success"] is True
            touched.append(path)

        assert touched == ["people/friends", "people", "."]
        assert (
            "Updated rollup for people/friends"
            in (empty_ops_kb / "people" / "friends" / "_summary.md").read_text()
        )
        assert "Updated rollup for people" in (empty_ops_kb / "people" / "_summary.md").read_text()
        assert "Updated rollup for ." in (empty_ops_kb / "_summary.md").read_text()

        stale_result = ops.write_parent_summary(
            empty_ops_kb,
            "people",
            "# People\n\nOut-of-order stale summary.\n",
            stale_people["children_digest"],
        )
        assert stale_result["success"] is False
        assert stale_result["error_code"] == "workflow_error"


# ============================================================================
# Delete / Move
# ============================================================================


class TestDeleteEntity:
    def test_delete_existing(self, ops_kb):
        result = ops.delete_entity(ops_kb, "people/work/bob_jones")
        assert result["success"]
        assert not (ops_kb / "people" / "work" / "bob_jones").exists()

    def test_delete_nonexistent(self, ops_kb):
        result = ops.delete_entity(ops_kb, "people/nobody")
        assert not result["success"]
        assert result["error_code"] == "not_found"


class TestMoveEntity:
    def test_move_entity(self, ops_kb):
        result = ops.move_entity(ops_kb, "people/work/bob_jones", "people/friends/bob_jones")
        assert result["success"]
        assert not (ops_kb / "people" / "work" / "bob_jones").exists()
        assert (ops_kb / "people" / "friends" / "bob_jones" / "_summary.md").exists()

    def test_move_to_existing_fails(self, ops_kb):
        result = ops.move_entity(ops_kb, "people/friends/alice_smith", "people/work/sarah_chen")
        assert not result["success"]
        assert result["error_code"] == "already_exists"


# ============================================================================
# List / Ancestors / Journal / Validate
# ============================================================================


class TestListEntities:
    def test_list_all(self, ops_kb):
        entities = ops.list_entities(ops_kb)
        assert len(entities) >= 4


class TestListNodes:
    def test_list_root_nodes(self, ops_kb):
        nodes = ops.list_nodes(ops_kb)
        paths = [node["path"] for node in nodes]
        assert "people" in paths
        assert "projects" in paths

    def test_list_recursive_nodes(self, ops_kb):
        nodes = ops.list_nodes(ops_kb, recursive=True)
        paths = [node["path"] for node in nodes]
        assert "people/friends/alice_smith" in paths


class TestSearchNodes:
    def test_search_returns_node_hits(self, ops_kb):
        result = ops.search_nodes(ops_kb, "alice", limit=3)
        assert result["count"] >= 1
        assert result["results"][0]["path"] == "people/friends/alice_smith"
        assert "content" not in result["results"][0]

    def test_search_parent_summaries(self, ops_kb):
        result = ops.search_nodes(ops_kb, "friends", limit=10)
        assert any(item["path"] == "people/friends" for item in result["results"])

    def test_search_include_content_truncates(self, ops_kb):
        result = ops.search_nodes(
            ops_kb,
            "alice",
            limit=1,
            include_content=True,
            content_max_chars=20,
            total_max_chars=20,
        )
        hit = result["results"][0]
        assert len(hit["content"]) <= 20
        assert hit["content_truncated"] is True

    def test_search_snippets_are_long_enough_for_context(self, ops_kb):
        long_note = ops_kb / "projects" / "long_note"
        long_note.mkdir(parents=True)
        (long_note / "_summary.md").write_text(
            "# Long Note\n\nneedle " + ("context detail " * 50) + "\n"
        )

        result = ops.search_nodes(ops_kb, "needle", limit=1)

        snippet = result["results"][0]["snippet"]
        assert result["results"][0]["path"] == "projects/long_note"
        assert len(snippet) > 400

    def test_search_ignores_hidden_directories(self, ops_kb):
        hidden = ops_kb / ".hidden" / "secret"
        hidden.mkdir(parents=True)
        (hidden / "_summary.md").write_text("# Secret\n\nNeedle phrase.\n")
        result = ops.search_nodes(ops_kb, "needle phrase", limit=5)
        assert result["results"] == []


class TestGetAncestors:
    def test_ancestors_include_root(self, ops_kb):
        result = ops.get_ancestors(ops_kb, "people/friends/alice_smith")
        assert result["success"]
        paths = [a["path"] for a in result["ancestors"]]
        assert "people/friends" in paths
        assert "people" in paths
        assert "." in paths


class TestWriteJournal:
    def test_write_journal(self, empty_ops_kb):
        result = ops.write_journal(
            empty_ops_kb,
            actions=[{"action_type": "create", "path": "people/friends/bob", "reasoning": "New"}],
            source="test",
        )
        assert result["success"]
        assert result["actions_logged"] == 1
        journal = empty_ops_kb / result["journal_path"]
        assert journal.exists()


class TestValidateKB:
    def test_validate_clean_kb(self, ops_kb):
        result = ops.validate_kb(ops_kb)
        # May have TBD issues but no errors
        assert "issues" in result
        assert "summary" in result


# ============================================================================
# Security
# ============================================================================


class TestSecurity:
    def test_validate_within_root(self, ops_kb):
        assert ops.validate_within_root(ops_kb, "people/friends") is True
        assert ops.validate_within_root(ops_kb, "../escaped") is False

    def test_write_summary_rejects_escape(self, ops_kb):
        result = ops.write_summary(ops_kb, "../escaped", "# Nope\n")
        assert not result["success"]

    def test_delete_rejects_escape(self, ops_kb):
        result = ops.delete_entity(ops_kb, "../escaped")
        assert not result["success"]
