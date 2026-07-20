"""Destructive CLI operations require explicit confirmation."""

import json

import pytest
from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import operations as ops


@pytest.fixture
def kb(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    (root / "_summary.md").write_text("# Root\n")
    ops.write_entity(root, "people/alice", "# Alice\n", create=True)
    return root


def test_delete_json_requires_confirm(kb):
    runner = CliRunner()
    result = runner.invoke(cli, ["--kb-root", str(kb), "--json", "delete", "people/alice"])
    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["error_code"] == "confirmation_required"
    assert (kb / "people" / "alice").exists()

    confirmed = runner.invoke(
        cli, ["--kb-root", str(kb), "--json", "delete", "people/alice", "--confirm"]
    )
    assert json.loads(confirmed.output)["success"]
    assert not (kb / "people" / "alice").exists()


def test_delete_force_still_works(kb):
    runner = CliRunner()
    result = runner.invoke(
        cli, ["--kb-root", str(kb), "--json", "delete", "people/alice", "--force"]
    )
    assert json.loads(result.output)["success"]


def test_delete_interactive_prompt(kb):
    runner = CliRunner()
    declined = runner.invoke(cli, ["--kb-root", str(kb), "delete", "people/alice"], input="n\n")
    assert declined.exit_code != 0
    assert (kb / "people" / "alice").exists()

    accepted = runner.invoke(cli, ["--kb-root", str(kb), "delete", "people/alice"], input="y\n")
    assert accepted.exit_code == 0
    assert not (kb / "people" / "alice").exists()


def test_move_json_requires_confirm(kb):
    runner = CliRunner()
    result = runner.invoke(
        cli, ["--kb-root", str(kb), "--json", "move", "people/alice", "people/alice_smith"]
    )
    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["error_code"] == "confirmation_required"
    assert (kb / "people" / "alice").exists()

    confirmed = runner.invoke(
        cli,
        ["--kb-root", str(kb), "--json", "move", "people/alice", "people/alice_smith", "--confirm"],
    )
    assert json.loads(confirmed.output)["success"]
    assert (kb / "people" / "alice_smith").exists()


def test_move_interactive_prompt(kb):
    runner = CliRunner()
    declined = runner.invoke(
        cli, ["--kb-root", str(kb), "move", "people/alice", "people/alice_smith"], input="n\n"
    )
    assert declined.exit_code != 0
    assert (kb / "people" / "alice").exists()
