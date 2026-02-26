"""Tests for the full 2-call write workflow via CLI."""

import json
import pytest
from click.testing import CliRunner

from kvault.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


@pytest.fixture
def workflow_kb(tmp_path):
    """KB ready for workflow tests."""
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "_summary.md").write_text("# Test KB\n\nWorkflow test KB.\n")
    (kb / ".kvault").mkdir()
    (kb / "people").mkdir()
    (kb / "people" / "_summary.md").write_text("# People\n\nAll contacts.\n")
    (kb / "people" / "contacts").mkdir()
    (kb / "people" / "contacts" / "_summary.md").write_text(
        "# Contacts\n\nProfessional contacts.\n"
    )
    (kb / "projects").mkdir()
    (kb / "projects" / "_summary.md").write_text("# Projects\n\nAll projects.\n")
    return kb


class TestFullWriteWorkflow:
    def test_2call_workflow(self, runner, workflow_kb):
        """Complete 2-call workflow: write entity → update summaries."""
        # Call 1: Write entity
        entity_content = (
            "---\n"
            "source: meeting_2026-02-25\n"
            "aliases:\n"
            "  - ACME Corp\n"
            "  - acme@example.com\n"
            "---\n"
            "\n"
            "# ACME Corp\n"
            "\n"
            "Key customer acquired at trade show.\n"
        )
        result = runner.invoke(
            cli,
            [
                "--kb-root",
                str(workflow_kb),
                "--json",
                "write",
                "people/contacts/acme",
                "--create",
                "--reasoning",
                "New customer from trade show",
            ],
            input=entity_content,
        )
        assert result.exit_code == 0, f"Write failed: {result.output}"
        write_data = json.loads(result.output)
        assert write_data["success"]
        assert write_data["journal_logged"] is True
        assert len(write_data["ancestors"]) >= 2

        # Call 2: Update summaries using ancestors from Call 1
        updates = []
        for ancestor in write_data["ancestors"]:
            existing = ancestor["current_content"]
            updated = existing.rstrip() + "\n\n- Added ACME Corp (trade show customer)\n"
            updates.append({"path": ancestor["path"], "content": updated})

        result = runner.invoke(
            cli,
            ["--kb-root", str(workflow_kb), "--json", "update-summaries"],
            input=json.dumps(updates),
        )
        assert result.exit_code == 0, f"Update failed: {result.output}"
        update_data = json.loads(result.output)
        assert update_data["success"]
        assert update_data["count"] == len(write_data["ancestors"])

        # Verify: entity exists and summaries updated
        result = runner.invoke(
            cli,
            ["--kb-root", str(workflow_kb), "--json", "read", "people/contacts/acme"],
        )
        assert result.exit_code == 0
        entity = json.loads(result.output)
        assert "ACME Corp" in entity["content"]

        # Verify: parent summaries contain the update
        contacts_summary = (workflow_kb / "people" / "contacts" / "_summary.md").read_text()
        assert "ACME Corp" in contacts_summary

    def test_write_then_validate(self, runner, workflow_kb):
        """Write an entity then validate the KB."""
        entity_content = "---\nsource: test\naliases:\n  - Bob\n---\n\n# Bob\n\nTest entity.\n"
        runner.invoke(
            cli,
            ["--kb-root", str(workflow_kb), "write", "people/contacts/bob", "--create"],
            input=entity_content,
        )

        result = runner.invoke(cli, ["--kb-root", str(workflow_kb), "--json", "validate"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["valid"] is True or all(i["severity"] == "info" for i in data.get("issues", []))
