"""move --batch: one lock, one confirm, one propagation list, all-or-nothing validation."""

import json

from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import operations as ops

BODY = "# Node\n\nA node.\n"
META = {"source": "manual", "aliases": ["Node"]}


def _seed(kb):
    for name in ("alpha", "bravo", "charlie"):
        assert ops.write_node(kb, f"projects/{name}", BODY, META, create=True)["success"]


def test_batch_moves_under_one_result(empty_kb):
    _seed(empty_kb)
    result = ops.move_entities(
        empty_kb,
        [
            {"from": "projects/alpha", "to": "projects/greek/alpha"},
            {"from": "projects/bravo", "to": "projects/greek/bravo"},
        ],
    )
    assert result["success"] and result["count"] == 2
    assert (empty_kb / "projects" / "greek" / "alpha" / "_summary.md").exists()
    assert not (empty_kb / "projects" / "alpha").exists()
    codes = [n["code"] for n in result["notes"]]
    assert "created" in codes and "propagate" in codes
    assert result["ancestor_paths"][:1] == ["projects"] or "projects" in result["ancestor_paths"]
    assert "projects/greek" in result["ancestor_paths"]
    assert "." in result["ancestor_paths"]
    # the hub was stubbed once, not twice
    created = [n for n in result["notes"] if n["code"] == "created"][0]
    assert created["detail"]["paths"] == ["projects/greek"]


def test_invalid_entry_moves_nothing(empty_kb):
    _seed(empty_kb)
    result = ops.move_entities(
        empty_kb,
        [
            {"from": "projects/alpha", "to": "projects/greek/alpha"},
            {"from": "projects/missing", "to": "projects/greek/missing"},
            {"from": "projects/bravo", "to": "projects/alpha"},
            {"bad": "entry"},
        ],
    )
    assert result["success"] is False
    errors = result["details"]["errors"]
    assert [e["index"] for e in errors] == [1, 2, 3]
    assert (empty_kb / "projects" / "alpha").exists()
    assert not (empty_kb / "projects" / "greek").exists()


def test_batch_rejects_nested_sources_and_reused_targets(empty_kb):
    _seed(empty_kb)
    ops.move_entity(empty_kb, "projects/charlie", "projects/alpha/charlie")
    nested = ops.move_entities(
        empty_kb,
        [
            {"from": "projects/alpha", "to": "projects/x"},
            {"from": "projects/alpha/charlie", "to": "projects/y"},
        ],
    )
    assert nested["success"] is False
    assert "inside another move" in nested["details"]["errors"][0]["error"]
    twice = ops.move_entities(
        empty_kb,
        [
            {"from": "projects/alpha", "to": "projects/x"},
            {"from": "projects/bravo", "to": "projects/x"},
        ],
    )
    assert twice["success"] is False and "twice" in twice["details"]["errors"][0]["error"]


def test_dry_run_changes_nothing(empty_kb):
    _seed(empty_kb)
    result = ops.move_entities(
        empty_kb, [{"from": "projects/alpha", "to": "projects/greek/alpha"}], dry_run=True
    )
    assert result["success"] and result["dry_run"]
    assert result["stubs"] == ["projects/greek"]
    assert (empty_kb / "projects" / "alpha").exists()
    assert not (empty_kb / "projects" / "greek").exists()


def test_batch_into_new_root_needs_flag(empty_kb):
    _seed(empty_kb)
    refused = ops.move_entities(empty_kb, [{"from": "projects/alpha", "to": "archive/alpha"}])
    assert refused["success"] is False and refused["details"]["reason"] == "new_root"
    allowed = ops.move_entities(
        empty_kb, [{"from": "projects/alpha", "to": "archive/alpha"}], new_root=True
    )
    assert allowed["success"]


def test_cli_batch_requires_confirm_and_reads_stdin(empty_kb):
    _seed(empty_kb)
    runner = CliRunner()
    payload = json.dumps([{"from": "projects/alpha", "to": "projects/greek/alpha"}])
    refused = runner.invoke(
        cli, ["move", "--batch", "--json", "--kb-root", str(empty_kb)], input=payload
    )
    assert refused.exit_code == 1
    assert json.loads(refused.output)["error_code"] == "confirmation_required"
    dry = runner.invoke(
        cli, ["move", "--batch", "--dry-run", "--kb-root", str(empty_kb)], input=payload
    )
    assert dry.exit_code == 0, dry.output
    assert "Dry run: 1 moves valid" in dry.output
    assert (empty_kb / "projects" / "alpha").exists()
    done = runner.invoke(
        cli, ["move", "--batch", "--confirm", "--kb-root", str(empty_kb)], input=payload
    )
    assert done.exit_code == 0, done.output
    assert "Moved: 1 nodes" in done.output
    assert "Ancestors to update:" in done.output
    assert (empty_kb / "projects" / "greek" / "alpha" / "_summary.md").exists()


def test_cli_move_positional_still_required_without_batch(empty_kb):
    runner = CliRunner()
    result = runner.invoke(cli, ["move", "--confirm", "--kb-root", str(empty_kb)])
    assert result.exit_code != 0
    assert "SOURCE and TARGET are required" in result.output
