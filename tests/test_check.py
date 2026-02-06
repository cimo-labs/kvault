"""Tests for kvault check command."""

import os
import time
from datetime import date
from pathlib import Path

from click.testing import CliRunner

from kvault.cli.main import cli


# Base time: 1 hour ago. We'll build up from here.
_BASE_TIME = time.time() - 3600


def _set_mtime(path: Path, offset_seconds: int) -> None:
    """Set mtime to _BASE_TIME + offset_seconds."""
    t = _BASE_TIME + offset_seconds
    os.utime(path, (t, t))


def _create_clean_kb(kb_path: Path) -> None:
    """Create a minimal clean KB where all checks pass.

    Timing: entity=T+0, parents=T+100..T+400, journal=T+500, index=T+600
    """
    runner = CliRunner()
    runner.invoke(cli, ["init", str(kb_path), "--name", "Test"])

    # Create an entity at sufficient depth (depth >= 3)
    entity_dir = kb_path / "people" / "contacts" / "alice"
    entity_dir.mkdir(parents=True, exist_ok=True)
    (entity_dir / "_summary.md").write_text(
        "---\nsource: manual\naliases: [Alice]\n---\n\n# Alice\n"
    )

    # Set entity as oldest
    _set_mtime(entity_dir / "_summary.md", 0)

    # Set parent summaries progressively newer (child→root)
    _set_mtime(kb_path / "people" / "contacts" / "_summary.md", 100)
    _set_mtime(kb_path / "people" / "_summary.md", 200)
    _set_mtime(kb_path / "projects" / "_summary.md", 200)
    _set_mtime(kb_path / "accomplishments" / "_summary.md", 200)
    _set_mtime(kb_path / "people" / "family" / "_summary.md", 200)
    _set_mtime(kb_path / "people" / "friends" / "_summary.md", 200)
    _set_mtime(kb_path / "_summary.md", 300)

    # Journal for today, newer than entity
    today = date.today()
    journal_file = kb_path / "journal" / today.strftime("%Y-%m") / "log.md"
    journal_file.parent.mkdir(parents=True, exist_ok=True)
    journal_file.write_text(
        f"# {today.strftime('%Y-%m')}\n\n## {today.isoformat()}\n\nTest\n"
    )
    # Set to "today" by using current time
    now = time.time()
    os.utime(journal_file, (now, now))

    # Index newer than entity
    _set_mtime(kb_path / ".kvault" / "index.db", 500)


def test_clean_kb_passes(tmp_path):
    kb_path = tmp_path / "kb"
    _create_clean_kb(kb_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["check", "--kb-root", str(kb_path), "--threshold", "0"])

    assert result.exit_code == 0, f"Expected clean KB, got: {result.output}"
    assert result.output == ""


def test_stale_parent_shows_propagate_warning(tmp_path):
    kb_path = tmp_path / "kb"
    _create_clean_kb(kb_path)

    # Make child summary 10 minutes newer than parent
    child = kb_path / "people" / "contacts" / "_summary.md"
    parent = kb_path / "people" / "_summary.md"
    _set_mtime(parent, 100)
    _set_mtime(child, 800)  # 700s newer = ~11 minutes

    runner = CliRunner()
    result = runner.invoke(cli, ["check", "--kb-root", str(kb_path), "--threshold", "1"])

    assert result.exit_code == 1
    assert "PROPAGATE" in result.output
    assert "people/_summary.md" in result.output or "contacts" in result.output


def test_missing_journal_shows_log_warning(tmp_path):
    kb_path = tmp_path / "kb"
    _create_clean_kb(kb_path)

    # Delete today's journal
    today = date.today()
    journal_file = kb_path / "journal" / today.strftime("%Y-%m") / "log.md"
    if journal_file.exists():
        journal_file.unlink()

    # Entity must have been modified "today" — set its mtime to now
    entity = kb_path / "people" / "contacts" / "alice" / "_summary.md"
    now = time.time()
    os.utime(entity, (now, now))

    # Make parents and index newer to avoid PROPAGATE noise
    os.utime(kb_path / "people" / "contacts" / "_summary.md", (now + 1, now + 1))
    os.utime(kb_path / "people" / "_summary.md", (now + 2, now + 2))
    os.utime(kb_path / "_summary.md", (now + 3, now + 3))
    os.utime(kb_path / ".kvault" / "index.db", (now + 4, now + 4))

    runner = CliRunner()
    result = runner.invoke(cli, ["check", "--kb-root", str(kb_path), "--threshold", "0"])

    assert result.exit_code == 1
    assert "LOG" in result.output


def test_stale_index_shows_rebuild_warning(tmp_path):
    kb_path = tmp_path / "kb"
    _create_clean_kb(kb_path)

    # Make entity much newer than index
    entity = kb_path / "people" / "contacts" / "alice" / "_summary.md"
    _set_mtime(entity, 0)
    _set_mtime(kb_path / ".kvault" / "index.db", 0)

    # Now advance entity by 10 minutes
    _set_mtime(entity, 700)
    # Keep parents newer than entity to avoid PROPAGATE warnings
    _set_mtime(kb_path / "people" / "contacts" / "_summary.md", 800)
    _set_mtime(kb_path / "people" / "_summary.md", 900)
    _set_mtime(kb_path / "_summary.md", 1000)

    runner = CliRunner()
    result = runner.invoke(cli, ["check", "--kb-root", str(kb_path), "--threshold", "1"])

    assert result.exit_code == 1
    assert "REBUILD" in result.output


def test_missing_frontmatter_shows_write_warning(tmp_path):
    kb_path = tmp_path / "kb"
    _create_clean_kb(kb_path)

    # Create entity without frontmatter
    entity_dir = kb_path / "people" / "contacts" / "bob"
    entity_dir.mkdir(parents=True, exist_ok=True)
    (entity_dir / "_summary.md").write_text("# Bob\n\nNo frontmatter here.\n")

    # Set all mtimes so parents are newer (avoid PROPAGATE noise)
    _set_mtime(entity_dir / "_summary.md", 0)
    _set_mtime(kb_path / "people" / "contacts" / "alice" / "_summary.md", 0)
    _set_mtime(kb_path / "people" / "contacts" / "_summary.md", 100)
    _set_mtime(kb_path / "people" / "_summary.md", 200)
    _set_mtime(kb_path / "_summary.md", 300)
    _set_mtime(kb_path / ".kvault" / "index.db", 500)

    runner = CliRunner()
    result = runner.invoke(cli, ["check", "--kb-root", str(kb_path), "--threshold", "0"])

    assert result.exit_code == 1
    assert "WRITE" in result.output
    assert "frontmatter" in result.output


def test_overcrowded_directory_shows_branch_warning(tmp_path):
    kb_path = tmp_path / "kb"
    _create_clean_kb(kb_path)

    # Create 11 child directories under people/contacts/
    contacts_dir = kb_path / "people" / "contacts"
    for i in range(11):
        child = contacts_dir / f"person_{i}"
        child.mkdir(parents=True, exist_ok=True)
        (child / "_summary.md").write_text(
            f"---\nsource: manual\naliases: [Person {i}]\n---\n\n# Person {i}\n"
        )
        _set_mtime(child / "_summary.md", 0)

    _set_mtime(kb_path / "people" / "contacts" / "alice" / "_summary.md", 0)
    _set_mtime(contacts_dir / "_summary.md", 100)
    _set_mtime(kb_path / "people" / "_summary.md", 200)
    _set_mtime(kb_path / "_summary.md", 300)
    _set_mtime(kb_path / ".kvault" / "index.db", 500)

    runner = CliRunner()
    result = runner.invoke(cli, ["check", "--kb-root", str(kb_path), "--threshold", "0"])

    assert result.exit_code == 1
    assert "BRANCH" in result.output


def test_no_kb_exits_silently(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["check", "--kb-root", str(tmp_path / "nonexistent")])

    assert result.exit_code == 0
    assert result.output == ""
