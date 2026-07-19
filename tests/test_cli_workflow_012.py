"""CLI contract tests for the breaking kvault 0.12 workflow."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from kvault.cli.main import cli


def _init(runner: CliRunner, tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    result = runner.invoke(cli, ["init", str(root), "--name", "CLI Owner"])
    assert result.exit_code == 0, result.output
    return root


def _json(result) -> dict:
    assert result.output, result.exception
    return json.loads(result.output)


def test_capture_events_and_no_op_reconciliation_round_trip(tmp_path: Path) -> None:
    runner = CliRunner()
    root = _init(runner, tmp_path)
    base = ["--kb-root", str(root), "--json"]

    captured = runner.invoke(
        cli,
        [*base, "capture", "--source", "conversation", "--source-ref", "message:1"],
        input="A durable candidate.\n",
    )
    assert captured.exit_code == 0, captured.output
    event_id = _json(captured)["event_id"]

    pending = runner.invoke(cli, [*base, "events", "list", "--status", "pending"])
    assert pending.exit_code == 0
    assert [event["event_id"] for event in _json(pending)["events"]] == [event_id]

    prepared = runner.invoke(cli, [*base, "reconcile", "prepare", event_id, "--path", "."])
    assert prepared.exit_code == 0, prepared.output
    assert _json(prepared)["revisions"]["."].startswith("sha256:")

    plan = {
        "schema_version": 1,
        "event_ids": [event_id],
        "decisions": [
            {
                "event_id": event_id,
                "outcome": "journal_only",
                "reasoning": "Useful temporal context, but not durable current state.",
            }
        ],
        "mutations": [],
        "reasoning": "Retain only as immutable evidence.",
        "requested_by": "cli-test",
    }
    applied = runner.invoke(
        cli,
        [*base, "reconcile", "apply"],
        input=json.dumps(plan),
    )
    assert applied.exit_code == 0, applied.output
    reconciliation_id = _json(applied)["reconciliation_id"]

    shown = runner.invoke(cli, [*base, "events", "show", event_id])
    assert shown.exit_code == 0
    assert _json(shown)["event"]["state"]["state"] == "resolved"

    status = runner.invoke(
        cli,
        [*base, "reconcile", "status", reconciliation_id],
    )
    assert status.exit_code == 0
    assert _json(status)["result"]["result"]["event_outcomes"][event_id] == "journal_only"


def test_legacy_mutation_command_is_fail_closed_even_in_json_mode(tmp_path: Path) -> None:
    runner = CliRunner()
    root = _init(runner, tmp_path)

    result = runner.invoke(
        cli,
        ["--kb-root", str(root), "--json", "write", "people/friends/alice", "--create"],
        input="# Alice\n",
    )

    assert result.exit_code == 1
    payload = _json(result)
    assert payload["success"] is False
    assert payload["error_code"] == "workflow_required"
    assert not (root / "people" / "friends" / "alice").exists()


def test_old_kb_capture_is_allowed_but_semantic_reconciliation_requires_migration(
    tmp_path: Path,
) -> None:
    root = tmp_path / "old"
    root.mkdir()
    (root / "_summary.md").write_text("# Old KB\n", encoding="utf-8")
    runner = CliRunner()
    base = ["--kb-root", str(root), "--json"]
    captured = runner.invoke(
        cli,
        [*base, "capture", "--source", "test"],
        input="Candidate before migration.",
    )
    assert captured.exit_code == 0
    event_id = _json(captured)["event_id"]
    plan = {
        "schema_version": 1,
        "event_ids": [event_id],
        "decisions": [{"event_id": event_id, "outcome": "no_op", "reasoning": "Already known."}],
        "mutations": [],
        "reasoning": "No change.",
    }

    blocked = runner.invoke(
        cli,
        [*base, "reconcile", "apply"],
        input=json.dumps(plan),
    )

    assert blocked.exit_code == 1
    assert _json(blocked)["error_code"] == "migration_required"


def test_migrate_preview_is_side_effect_free_then_apply_installs_schema(tmp_path: Path) -> None:
    root = tmp_path / "old"
    root.mkdir()
    (root / "_summary.md").write_text("# Old KB\n", encoding="utf-8")
    runner = CliRunner()
    base = ["--kb-root", str(root), "--json", "migrate"]

    preview = runner.invoke(cli, [*base, "--dry-run"])
    assert preview.exit_code == 0
    assert _json(preview)["dry_run"] is True
    assert not (root / ".kvault" / "schema.json").exists()

    applied = runner.invoke(cli, base)
    assert applied.exit_code == 0, applied.output
    assert _json(applied)["schema_after"] == 1
    assert (root / ".kvault" / "schema.json").is_file()


def test_skill_path_and_install_copy_canonical_bundle(tmp_path: Path) -> None:
    runner = CliRunner()
    located = runner.invoke(cli, ["skill", "path", "--json"])
    assert located.exit_code == 0, located.output
    source = Path(_json(located)["path"])
    assert (source / "SKILL.md").is_file()

    destination = tmp_path / "runtime" / "skills" / "kvault"
    installed = runner.invoke(cli, ["skill", "install", str(destination), "--json"])
    assert installed.exit_code == 0, installed.output
    assert (destination / "SKILL.md").is_file()
    assert (destination / "agents" / "openai.yaml").is_file()
    assert (destination / "references" / "parallel-reconciliation.md").is_file()

    source_replace = runner.invoke(cli, ["skill", "install", str(source), "--force"])
    assert source_replace.exit_code != 0
    assert (source / "SKILL.md").is_file()

    alias = tmp_path / "skill-alias"
    alias.symlink_to(destination, target_is_directory=True)
    symlink_replace = runner.invoke(cli, ["skill", "install", str(alias), "--force"])
    assert symlink_replace.exit_code != 0
    assert alias.is_symlink()
    assert (destination / "SKILL.md").is_file()
