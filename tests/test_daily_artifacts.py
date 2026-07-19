"""Tests for daily artifact generation (core and CLI)."""

import json
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core.daily_artifacts import generate_daily_artifact
from kvault.core.paths import PathSafetyError


def test_generate_daily_artifact_creates_expected_sections(sample_kb):
    """Core generator should write a daily artifact with required sections."""
    result = generate_daily_artifact(sample_kb, artifact_date=date(2026, 2, 10), force=True)

    assert result.written is True
    assert result.path.exists()
    assert "# Daily Artifact - 2026-02-10" in result.content
    assert "## Goals Snapshot" in result.content
    assert "## Near-Future Context" in result.content
    assert "## People Summary (Full)" in result.content
    assert "All tracked contacts" in result.content


def test_generate_daily_artifact_reuses_existing_file(sample_kb):
    """Without force, existing artifact should be reused rather than overwritten."""
    first = generate_daily_artifact(sample_kb, artifact_date=date(2026, 2, 10), force=True)
    second = generate_daily_artifact(sample_kb, artifact_date=date(2026, 2, 10), force=False)

    assert first.path == second.path
    assert second.written is False
    assert second.content == first.content


def test_generate_daily_artifact_force_overwrites(sample_kb):
    """Force mode should overwrite an existing artifact."""
    initial = generate_daily_artifact(sample_kb, artifact_date=date(2026, 2, 11), force=True)
    initial.path.write_text("sentinel")

    forced = generate_daily_artifact(sample_kb, artifact_date=date(2026, 2, 11), force=True)
    assert forced.written is True
    assert forced.content != "sentinel"
    assert "Daily Artifact - 2026-02-11" in forced.content


def test_cli_artifact_daily_generates_file(sample_kb):
    """CLI command should generate the artifact file successfully."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["artifact", "daily", "--kb-root", str(sample_kb), "--date", "2026-02-12"],
    )

    assert result.exit_code == 0
    assert "daily artifact" in result.output.lower()
    artifact_path = sample_kb / ".kvault" / "artifacts" / "daily" / "2026-02-12.md"
    assert artifact_path.exists()


def test_cli_artifact_daily_honors_top_level_kb_root(sample_kb, tmp_path):
    """Top-level --kb-root should control nested artifact commands."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(
            cli,
            ["--kb-root", str(sample_kb), "artifact", "daily", "--date", "2026-02-13"],
            catch_exceptions=False,
        )
        assert not (Path.cwd() / ".kvault").exists()

    assert result.exit_code == 0
    assert (sample_kb / ".kvault" / "artifacts" / "daily" / "2026-02-13.md").exists()


def test_cli_artifact_daily_json(sample_kb):
    """Artifact CLI should support machine-readable JSON output."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["artifact", "daily", "--kb-root", str(sample_kb), "--date", "2026-02-14", "--json"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["success"] is True
    assert data["kg_root"] == str(sample_kb.resolve())
    assert data["relative_path"] == ".kvault/artifacts/daily/2026-02-14.md"
    assert "# Daily Artifact - 2026-02-14" in data["content"]


def test_daily_artifact_does_not_read_symlinked_source_outside_root(sample_kb, tmp_path):
    outside = tmp_path / "outside-root-summary.md"
    outside.write_text("# Secret\n\nOUTSIDE SENTINEL\n", encoding="utf-8")
    (sample_kb / "_summary.md").unlink()
    (sample_kb / "_summary.md").symlink_to(outside)

    result = generate_daily_artifact(sample_kb, artifact_date=date(2026, 2, 15), force=True)

    assert "OUTSIDE SENTINEL" not in result.content


def test_daily_artifact_refuses_symlinked_output_directory(sample_kb, tmp_path):
    outside = tmp_path / "outside-artifacts"
    outside.mkdir()
    (sample_kb / ".kvault" / "artifacts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathSafetyError, match="Symlink"):
        generate_daily_artifact(sample_kb, artifact_date=date(2026, 2, 16), force=True)

    assert not (outside / "daily" / "2026-02-16.md").exists()
