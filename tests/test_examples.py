"""Validate that README quickstart examples actually work.

Following CJE's test_examples.py pattern: ensure documentation stays accurate.
"""

import pytest
from pathlib import Path
from click.testing import CliRunner
from kvault.cli.main import cli


class TestReadmeQuickstart:
    """Validate the README quickstart section works as documented."""

    def test_init_creates_kb(self, tmp_path):
        """README step: kvault init my_kb --name 'Your Name'"""
        runner = CliRunner()
        kb_path = tmp_path / "my_kb"
        result = runner.invoke(cli, ["init", str(kb_path), "--name", "Test User"])
        assert result.exit_code == 0, f"init failed: {result.output}"
        assert (kb_path / "_summary.md").exists()
        assert (kb_path / ".kvault").exists()

    def test_check_passes_on_fresh_kb(self, tmp_path):
        """README step: kvault check --kb-root my_kb"""
        runner = CliRunner()
        kb_path = tmp_path / "my_kb"
        # First init
        runner.invoke(cli, ["init", str(kb_path), "--name", "Test User"])
        # Then check
        result = runner.invoke(cli, ["check", "--kb-root", str(kb_path)])
        assert result.exit_code == 0, f"check failed: {result.output}"

    def test_index_search_works(self, tmp_path):
        """README: kvault index search --db .kvault/index.db --query 'term'"""
        runner = CliRunner()
        kb_path = tmp_path / "my_kb"
        runner.invoke(cli, ["init", str(kb_path), "--name", "Test User"])
        # Search on empty KB should not crash
        result = runner.invoke(cli, ["index", "search", "--db", str(kb_path / ".kvault" / "index.db"), "--query", "test"])
        assert result.exit_code == 0, f"search failed: {result.output}"

    def test_index_rebuild_works(self, tmp_path):
        """README: kvault index rebuild --kg-root ."""
        runner = CliRunner()
        kb_path = tmp_path / "my_kb"
        runner.invoke(cli, ["init", str(kb_path), "--name", "Test User"])
        result = runner.invoke(cli, ["index", "rebuild", "--kg-root", str(kb_path)])
        assert result.exit_code == 0, f"rebuild failed: {result.output}"
