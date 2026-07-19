"""Tests for kvault CLI commands using CliRunner."""

import json
import pytest
from click.testing import CliRunner

from kvault.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def cli_kb(tmp_path):
    """Fresh KB for CLI tests."""
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "_summary.md").write_text("# Test KB\n\nCLI test knowledge base.\n")
    (kb / ".kvault").mkdir()
    (kb / "people").mkdir()
    (kb / "people" / "_summary.md").write_text("# People\n\nAll contacts.\n")
    (kb / "people" / "friends").mkdir()
    (kb / "people" / "friends" / "_summary.md").write_text("# Friends\n\nFriends list.\n")
    (kb / "projects").mkdir()
    (kb / "projects" / "_summary.md").write_text("# Projects\n\nAll projects.\n")
    return kb


@pytest.fixture
def cli_kb_with_entity(cli_kb):
    """CLI KB with one entity pre-created."""
    entity_dir = cli_kb / "people" / "friends" / "alice_smith"
    entity_dir.mkdir(parents=True)
    (entity_dir / "_summary.md").write_text(
        "---\nsource: test\naliases:\n  - Alice Smith\nupdated: '2026-01-15'\ncreated: '2026-01-15'\n---\n\n# Alice Smith\n\nA friend.\n"
    )
    return cli_kb


# ============================================================================
# Help & Status
# ============================================================================


class TestHelp:
    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "kvault" in result.output
        assert "read" in result.output
        assert "write" in result.output

    def test_status(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "status"])
        assert result.exit_code == 0
        assert "KB root" in result.output

    def test_status_json(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "--json", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "entity_count" in data

    def test_status_post_command_common_options(self, runner, cli_kb):
        result = runner.invoke(cli, ["status", "--json", "--kb-root", str(cli_kb)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "entity_count" in data

    def test_tree(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "tree"])
        assert result.exit_code == 0
        assert "people" in result.output
        assert "[2 children, 3 total]" in result.output


# ============================================================================
# Tree (annotated outline)
# ============================================================================


class TestTree:
    def test_default_depth_unlimited(self, runner, cli_kb_with_entity):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb_with_entity), "tree"])
        assert result.exit_code == 0
        assert "alice_smith" in result.output  # depth 3 — hidden under the old default
        assert "…" not in result.output

    def test_depth_truncation_marker(self, runner, cli_kb_with_entity):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb_with_entity), "tree", "--depth", "1"])
        assert result.exit_code == 0
        assert "alice_smith" not in result.output
        assert "…2 nodes below" in result.output

    def test_max_children_elision(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "tree", "--max-children", "1"])
        assert result.exit_code == 0
        assert "more children" in result.output

    def test_gist_flag(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "tree", "--gist"])
        assert result.exit_code == 0
        assert "— All contacts." in result.output

    def test_subtree_path(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "tree", "people"])
        assert result.exit_code == 0
        assert result.output.splitlines()[0].startswith("people")
        assert "projects" not in result.output

    def test_json_envelope(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "--json", "tree"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["path"] == "."
        assert data["total_nodes"] == 4
        assert data["shown_nodes"] == 4
        assert data["outline"]["children_count"] == 2

    def test_missing_node_fails(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "tree", "nope"])
        assert result.exit_code != 0
        assert "Node not found" in result.output


# ============================================================================
# Read
# ============================================================================


class TestReadCommand:
    def test_read_existing(self, runner, cli_kb_with_entity):
        result = runner.invoke(
            cli, ["--kb-root", str(cli_kb_with_entity), "read", "people/friends/alice_smith"]
        )
        assert result.exit_code == 0
        assert "Alice Smith" in result.output
        assert "Parent summary (people/friends)" in result.output
        assert "Friends list." in result.output

    def test_read_json(self, runner, cli_kb_with_entity):
        result = runner.invoke(
            cli,
            [
                "--kb-root",
                str(cli_kb_with_entity),
                "--json",
                "read",
                "people/friends/alice_smith",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["path"] == "people/friends/alice_smith"
        assert data["parent"]["path"] == "people/friends"

    def test_read_category_json(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "--json", "read", "people"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["path"] == "people"
        assert data["kind"] == "category"
        assert data["parent"]["path"] == "."

    def test_read_post_command_common_options(self, runner, cli_kb_with_entity):
        result = runner.invoke(
            cli,
            [
                "read",
                "people/friends/alice_smith",
                "--json",
                "--kb-root",
                str(cli_kb_with_entity),
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["path"] == "people/friends/alice_smith"

    def test_read_nonexistent(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "read", "people/nobody"])
        assert result.exit_code != 0


# ============================================================================
# Write
# ============================================================================


class TestWriteCommand:
    @pytest.mark.parametrize("name", ["write", "delete", "move"])
    def test_direct_mutation_names_fail_closed(self, runner, cli_kb, name):
        result = runner.invoke(
            cli,
            ["--kb-root", str(cli_kb), "--json", name, "people/friends/alice_smith"],
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False
        assert data["error_code"] == "workflow_required"


# ============================================================================
# List
# ============================================================================


class TestListCommand:
    def test_list_all(self, runner, cli_kb_with_entity):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb_with_entity), "list", "--recursive"])
        assert result.exit_code == 0
        assert "alice_smith" in result.output

    def test_list_json(self, runner, cli_kb_with_entity):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb_with_entity), "--json", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any(item["path"] == "people" for item in data)


# ============================================================================
# Search
# ============================================================================


class TestSearchCommand:
    def test_search_json(self, runner, cli_kb_with_entity):
        result = runner.invoke(
            cli, ["--kb-root", str(cli_kb_with_entity), "--json", "search", "alice"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["query"] == "alice"
        assert data["results"][0]["path"] == "people/friends/alice_smith"

    def test_search_plain_text(self, runner, cli_kb_with_entity):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb_with_entity), "search", "friends"])
        assert result.exit_code == 0
        assert "people/friends" in result.output


# ============================================================================
# Delete
# ============================================================================


class TestDeleteCommand:
    def test_delete_does_not_remove_target(self, runner, cli_kb_with_entity):
        target = cli_kb_with_entity / "people" / "friends" / "alice_smith"
        result = runner.invoke(
            cli,
            ["--kb-root", str(cli_kb_with_entity), "delete", "people/friends/alice_smith"],
        )
        assert result.exit_code == 1
        assert target.is_dir()


# ============================================================================
# Move
# ============================================================================


class TestMoveCommand:
    def test_move_does_not_change_tree(self, runner, cli_kb_with_entity):
        result = runner.invoke(
            cli,
            [
                "--kb-root",
                str(cli_kb_with_entity),
                "move",
                "people/friends/alice_smith",
                "people/alice_smith",
            ],
        )
        assert result.exit_code == 1
        assert (cli_kb_with_entity / "people" / "friends" / "alice_smith").is_dir()
        assert not (cli_kb_with_entity / "people" / "alice_smith").exists()


# ============================================================================
# Summary commands
# ============================================================================


class TestSummaryCommands:
    def test_read_summary(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "read-summary", "people"])
        assert result.exit_code == 0
        assert "People" in result.output

    def test_read_summary_json(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "--json", "read-summary", "people"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["path"] == "people"

    def test_write_summary(self, runner, cli_kb):
        before = (cli_kb / "people" / "_summary.md").read_bytes()
        input_content = "# People\n\nUpdated summary.\n"
        result = runner.invoke(
            cli, ["--kb-root", str(cli_kb), "write-summary", "people"], input=input_content
        )
        assert result.exit_code == 1
        assert (cli_kb / "people" / "_summary.md").read_bytes() == before

    def test_update_summaries(self, runner, cli_kb):
        updates = json.dumps([{"path": "people", "content": "# People\n\nBatch updated.\n"}])
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "update-summaries"], input=updates)
        assert result.exit_code == 1

    def test_ancestors(self, runner, cli_kb_with_entity):
        result = runner.invoke(
            cli,
            [
                "--kb-root",
                str(cli_kb_with_entity),
                "--json",
                "ancestors",
                "people/friends/alice_smith",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"]
        paths = [a["path"] for a in data["ancestors"]]
        assert "people/friends" in paths
        assert "." in paths


# ============================================================================
# Journal
# ============================================================================


class TestJournalCommand:
    def test_journal(self, runner, cli_kb):
        actions = json.dumps(
            [{"action_type": "create", "path": "people/friends/bob", "reasoning": "New friend"}]
        )
        result = runner.invoke(
            cli,
            ["--kb-root", str(cli_kb), "journal", "--source", "test"],
            input=actions,
        )
        assert result.exit_code == 1
        assert not any((cli_kb / "journal").glob("*/log.md"))


# ============================================================================
# Validate
# ============================================================================


class TestValidateCommand:
    def test_validate(self, runner, cli_kb_with_entity):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb_with_entity), "validate"])
        assert result.exit_code == 0

    def test_validate_json(self, runner, cli_kb_with_entity):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb_with_entity), "--json", "validate"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "valid" in data
        assert "issues" in data


# ============================================================================
# Root pinning
# ============================================================================


def test_allowed_roots_blocks_disallowed_cli_root(runner, cli_kb, tmp_path, monkeypatch):
    other = tmp_path / "other_kb"
    other.mkdir()
    (other / "_summary.md").write_text("# Other\n")
    (other / ".kvault").mkdir()
    monkeypatch.setenv("KVAULT_ALLOWED_ROOTS", str(cli_kb))

    result = runner.invoke(cli, ["status", "--kb-root", str(other)])

    assert result.exit_code != 0
    assert "not allowed" in result.output
