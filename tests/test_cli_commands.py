"""Tests for kvault CLI commands using CliRunner."""

import json
import pytest
from pathlib import Path
from click.testing import CliRunner

from kvault.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


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

    def test_tree(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "tree"])
        assert result.exit_code == 0
        assert "people/" in result.output


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

    def test_read_nonexistent(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "read", "people/nobody"])
        assert result.exit_code != 0


# ============================================================================
# Write
# ============================================================================


class TestWriteCommand:
    def test_write_create(self, runner, cli_kb):
        input_content = "---\nsource: test\naliases:\n  - Bob\n---\n\n# Bob\n\nA new friend.\n"
        result = runner.invoke(
            cli,
            ["--kb-root", str(cli_kb), "write", "people/friends/bob", "--create"],
            input=input_content,
        )
        assert result.exit_code == 0
        assert "Created" in result.output
        assert (cli_kb / "people" / "friends" / "bob" / "_summary.md").exists()

    def test_write_create_json(self, runner, cli_kb):
        input_content = "---\nsource: test\naliases:\n  - Carol\n---\n\n# Carol\n"
        result = runner.invoke(
            cli,
            ["--kb-root", str(cli_kb), "--json", "write", "people/friends/carol", "--create"],
            input=input_content,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"]
        assert data["created"] is True
        assert "ancestors" in data

    def test_write_update(self, runner, cli_kb_with_entity):
        input_content = "---\nsource: manual\naliases:\n  - Alice Smith\n---\n\n# Alice Smith\n\nUpdated!\n"
        result = runner.invoke(
            cli,
            ["--kb-root", str(cli_kb_with_entity), "write", "people/friends/alice_smith"],
            input=input_content,
        )
        assert result.exit_code == 0
        assert "Updated" in result.output

    def test_write_with_reasoning(self, runner, cli_kb):
        input_content = "---\nsource: conf\naliases:\n  - Dave\n---\n\n# Dave\n"
        result = runner.invoke(
            cli,
            [
                "--kb-root",
                str(cli_kb),
                "--json",
                "write",
                "people/friends/dave",
                "--create",
                "--reasoning",
                "Met at conference",
            ],
            input=input_content,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"]
        assert data["journal_logged"] is True

    def test_write_duplicate_fails(self, runner, cli_kb_with_entity):
        input_content = "---\nsource: test\naliases: []\n---\n\n# Dupe\n"
        result = runner.invoke(
            cli,
            [
                "--kb-root",
                str(cli_kb_with_entity),
                "--json",
                "write",
                "people/friends/alice_smith",
                "--create",
            ],
            input=input_content,
        )
        assert result.exit_code == 0  # JSON mode doesn't exit(1)
        data = json.loads(result.output)
        assert not data["success"]
        assert data["error_code"] == "already_exists"


# ============================================================================
# List
# ============================================================================


class TestListCommand:
    def test_list_all(self, runner, cli_kb_with_entity):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb_with_entity), "list"])
        assert result.exit_code == 0
        assert "alice_smith" in result.output

    def test_list_json(self, runner, cli_kb_with_entity):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb_with_entity), "--json", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) >= 1


# ============================================================================
# Delete
# ============================================================================


class TestDeleteCommand:
    def test_delete_with_force(self, runner, cli_kb_with_entity):
        result = runner.invoke(
            cli,
            ["--kb-root", str(cli_kb_with_entity), "delete", "people/friends/alice_smith", "--force"],
        )
        assert result.exit_code == 0
        assert "Deleted" in result.output
        assert not (cli_kb_with_entity / "people" / "friends" / "alice_smith").exists()

    def test_delete_json(self, runner, cli_kb_with_entity):
        result = runner.invoke(
            cli,
            [
                "--kb-root",
                str(cli_kb_with_entity),
                "--json",
                "delete",
                "people/friends/alice_smith",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"]

    def test_delete_nonexistent(self, runner, cli_kb):
        result = runner.invoke(
            cli, ["--kb-root", str(cli_kb), "--json", "delete", "people/nobody"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert not data["success"]


# ============================================================================
# Move
# ============================================================================


class TestMoveCommand:
    def test_move(self, runner, cli_kb_with_entity):
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
        # people/alice_smith is only 2-level deep, should work
        assert result.exit_code == 0
        assert "Moved" in result.output


# ============================================================================
# Summary commands
# ============================================================================


class TestSummaryCommands:
    def test_read_summary(self, runner, cli_kb):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb), "read-summary", "people"])
        assert result.exit_code == 0
        assert "People" in result.output

    def test_read_summary_json(self, runner, cli_kb):
        result = runner.invoke(
            cli, ["--kb-root", str(cli_kb), "--json", "read-summary", "people"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["path"] == "people"

    def test_write_summary(self, runner, cli_kb):
        input_content = "# People\n\nUpdated summary.\n"
        result = runner.invoke(
            cli, ["--kb-root", str(cli_kb), "write-summary", "people"], input=input_content
        )
        assert result.exit_code == 0
        assert "Updated summary" in result.output or "people" in result.output

    def test_update_summaries(self, runner, cli_kb):
        updates = json.dumps([{"path": "people", "content": "# People\n\nBatch updated.\n"}])
        result = runner.invoke(
            cli, ["--kb-root", str(cli_kb), "update-summaries"], input=updates
        )
        assert result.exit_code == 0

    def test_ancestors(self, runner, cli_kb_with_entity):
        result = runner.invoke(
            cli,
            ["--kb-root", str(cli_kb_with_entity), "--json", "ancestors", "people/friends/alice_smith"],
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
        assert result.exit_code == 0
        assert "Logged 1 actions" in result.output


# ============================================================================
# Validate
# ============================================================================


class TestValidateCommand:
    def test_validate(self, runner, cli_kb_with_entity):
        result = runner.invoke(cli, ["--kb-root", str(cli_kb_with_entity), "validate"])
        assert result.exit_code == 0

    def test_validate_json(self, runner, cli_kb_with_entity):
        result = runner.invoke(
            cli, ["--kb-root", str(cli_kb_with_entity), "--json", "validate"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "valid" in data
        assert "issues" in data
