"""Tests for kvault log CLI commands."""

import json

from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core.observability import ObservabilityLogger


def test_log_summary_json_defaults_to_latest_session(tmp_path):
    db_path = tmp_path / "logs.db"
    logger = ObservabilityLogger(db_path)

    first_session = logger.session_id
    logger.log("research", {"query": "alice"})

    logger.new_session()
    latest_session = logger.session_id
    logger.log("decide", {"entity": "Alice", "action": "create", "reasoning": "No match"})

    runner = CliRunner()
    result = runner.invoke(cli, ["log", "summary", "--db", str(db_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["session_id"] == latest_session
    assert payload["session_id"] != first_session
    assert payload["phase_counts"]["decide"] == 1


def test_log_summary_json_honors_explicit_session_id(tmp_path):
    db_path = tmp_path / "logs.db"
    logger = ObservabilityLogger(db_path)

    first_session = logger.session_id
    logger.log("research", {"query": "alice"})

    logger.new_session()
    logger.log("decide", {"entity": "Alice", "action": "create", "reasoning": "No match"})

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["log", "summary", "--db", str(db_path), "--session-id", first_session, "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["session_id"] == first_session
    assert payload["phase_counts"]["research"] == 1
    assert payload["total_logs"] == 1


def test_log_summary_fails_for_missing_db(tmp_path):
    missing_db = tmp_path / "missing.db"
    runner = CliRunner()
    result = runner.invoke(cli, ["log", "summary", "--db", str(missing_db)])

    assert result.exit_code != 0
    assert "does not exist" in result.output
