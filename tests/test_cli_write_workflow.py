"""Tests for the full 2-call write workflow via CLI."""

import json
import pytest
from click.testing import CliRunner

from kvault.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


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
    def test_old_2call_workflow_is_removed(self, runner, workflow_kb):
        """The pre-0.12 write/propagate protocol cannot mutate the KB."""
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
            input="# ACME Corp\n\nKey customer acquired at trade show.\n",
        )
        assert result.exit_code == 1
        write_data = json.loads(result.output)
        assert write_data["error_code"] == "workflow_required"
        assert not (workflow_kb / "people" / "contacts" / "acme").exists()

    def test_validate_legacy_tree_returns_structured_nonzero(self, runner, workflow_kb):
        """The unified validator fails closed for an unmigrated legacy tree."""
        entity_content = "---\nsource: test\naliases:\n  - Bob\n---\n\n# Bob\n\nTest entity.\n"
        runner.invoke(
            cli,
            ["--kb-root", str(workflow_kb), "write", "people/contacts/bob", "--create"],
            input=entity_content,
        )

        result = runner.invoke(cli, ["--kb-root", str(workflow_kb), "--json", "validate"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["success"] is False
        assert data["valid"] is False
        assert data["issues"]
