"""Tests for kvault init command."""

import sqlite3
from datetime import date
from pathlib import Path

from click.testing import CliRunner

from kvault.cli.main import cli


def test_init_creates_directory_structure(tmp_path):
    runner = CliRunner()
    kb_path = tmp_path / "my_kb"
    result = runner.invoke(cli, ["init", str(kb_path), "--name", "Alice"])

    assert result.exit_code == 0
    assert kb_path.exists()
    assert (kb_path / "_summary.md").exists()
    assert (kb_path / "CLAUDE.md").exists()
    assert (kb_path / "people" / "_summary.md").exists()
    assert (kb_path / "people" / "family" / "_summary.md").exists()
    assert (kb_path / "people" / "friends" / "_summary.md").exists()
    assert (kb_path / "people" / "contacts" / "_summary.md").exists()
    assert (kb_path / "projects" / "_summary.md").exists()
    assert (kb_path / "accomplishments" / "_summary.md").exists()
    assert (kb_path / ".kvault" / "index.db").exists()
    assert (kb_path / ".kvault" / "logs.db").exists()


def test_init_root_summary_contains_owner_name(tmp_path):
    runner = CliRunner()
    kb_path = tmp_path / "my_kb"
    runner.invoke(cli, ["init", str(kb_path), "--name", "Alice"])

    content = (kb_path / "_summary.md").read_text()
    assert "Alice" in content
    assert "{{OWNER_NAME}}" not in content


def test_init_claude_md_contains_rules(tmp_path):
    runner = CliRunner()
    kb_path = tmp_path / "my_kb"
    runner.invoke(cli, ["init", str(kb_path), "--name", "Alice"])

    content = (kb_path / "CLAUDE.md").read_text()
    assert "PROPAGATE ALL ANCESTORS" in content
    assert "FIX HOOK WARNINGS FIRST" in content
    assert "JOURNAL EVERY SESSION" in content
    assert "FULL CORPUS ONLY" in content
    assert "FRONTMATTER REQUIRED" in content
    assert "RESEARCH BEFORE WRITE" in content
    assert "Alice" in content
    assert "{{OWNER_NAME}}" not in content


def test_init_databases_have_tables(tmp_path):
    runner = CliRunner()
    kb_path = tmp_path / "my_kb"
    runner.invoke(cli, ["init", str(kb_path), "--name", "Alice"])

    # Check index.db has expected tables
    conn = sqlite3.connect(kb_path / ".kvault" / "index.db")
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "entities" in tables

    # Check logs.db has expected tables
    conn = sqlite3.connect(kb_path / ".kvault" / "logs.db")
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "logs" in tables


def test_init_refuses_existing_kb(tmp_path):
    runner = CliRunner()
    kb_path = tmp_path / "my_kb"

    # First init succeeds
    result = runner.invoke(cli, ["init", str(kb_path), "--name", "Alice"])
    assert result.exit_code == 0

    # Second init fails
    result = runner.invoke(cli, ["init", str(kb_path), "--name", "Alice"])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_init_journal_has_todays_date(tmp_path):
    runner = CliRunner()
    kb_path = tmp_path / "my_kb"
    runner.invoke(cli, ["init", str(kb_path), "--name", "Alice"])

    today = date.today()
    journal_dir = kb_path / "journal" / today.strftime("%Y-%m")
    journal_file = journal_dir / "log.md"
    assert journal_file.exists()

    content = journal_file.read_text()
    assert today.isoformat() in content


def test_init_default_path_uses_cwd(tmp_path):
    runner = CliRunner()
    # Use an empty dir so no .kvault/ exists
    kb_path = tmp_path / "cwd_kb"
    kb_path.mkdir()

    result = runner.invoke(cli, ["init", str(kb_path), "--name", "Bob"])
    assert result.exit_code == 0
    assert (kb_path / "_summary.md").exists()


def test_init_output_includes_instructions(tmp_path):
    runner = CliRunner()
    kb_path = tmp_path / "my_kb"
    result = runner.invoke(cli, ["init", str(kb_path), "--name", "Alice"])

    assert "Initialized knowledge base" in result.output
    assert "mcpServers" in result.output
    assert "kvault check" in result.output
    assert "CLAUDE.md" in result.output


def test_init_no_placeholder_tokens_remain(tmp_path):
    """Ensure all {{PLACEHOLDER}} tokens are replaced."""
    runner = CliRunner()
    kb_path = tmp_path / "my_kb"
    runner.invoke(cli, ["init", str(kb_path), "--name", "Alice"])

    for md_file in kb_path.rglob("*.md"):
        content = md_file.read_text()
        assert "{{" not in content, f"Unreplaced placeholder in {md_file.relative_to(kb_path)}"
