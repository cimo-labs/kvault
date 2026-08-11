"""Tests for the durable ops log (kvault/core/oplog.py).

The contract under test: a logging failure NEVER fails a KB operation, the
store stays bounded, timestamps are UTC, and WAL is never enabled (its
sidecar files break git-sync automation on live KBs).
"""

import json
import os
import sqlite3
import stat
from pathlib import Path

from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import operations as ops
from kvault.core.oplog import MAX_ROWS, PRUNE_SLACK, OpLog, resolve_session_id

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_kb(tmp_path, name="kb"):
    kb = tmp_path / name
    kb.mkdir()
    (kb / ".kvault").mkdir()
    (kb / "_summary.md").write_text("# Test KB\n\nRoot.\n")
    (kb / "people").mkdir()
    (kb / "people" / "_summary.md").write_text("# People\n\nPeople.\n")
    (kb / "people" / "contacts").mkdir()
    (kb / "people" / "contacts" / "_summary.md").write_text("# Contacts\n\nContacts.\n")
    return kb


def test_append_and_tail_roundtrip(tmp_path):
    kb = _make_kb(tmp_path)
    log = OpLog(kb)
    ok = log.append(
        "write",
        {"success": True, "path": "people/x", "did": "created people/x", "changed": True},
        ms=12.3,
    )
    assert ok is True
    rows = log.tail()
    assert len(rows) == 1
    row = rows[0]
    assert row["op"] == "write"
    assert row["path"] == "people/x"
    assert row["changed"] is True
    assert row["partial"] is False
    assert row["session"] == log.session_id
    assert row["ts"].startswith("20") and "+00:00" in row["ts"]  # UTC, explicit offset


def test_append_never_raises_on_corrupt_db(tmp_path):
    kb = _make_kb(tmp_path)
    db = kb / ".kvault" / "logs.db"
    db.write_bytes(os.urandom(4096))  # garbage — "file is not a database"
    ok = OpLog(kb).append("write", {"success": True, "path": "p"})
    assert ok is False
    assert OpLog(kb).tail() == []
    assert OpLog(kb).summary()["total_ops"] == 0


def test_append_never_raises_on_unwritable_dir(tmp_path):
    kb = _make_kb(tmp_path)
    kvault_dir = kb / ".kvault"
    kvault_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        ok = OpLog(kb).append("write", {"success": True, "path": "p"})
        assert ok is False
    finally:
        kvault_dir.chmod(stat.S_IRWXU)


def test_kb_write_succeeds_when_oplog_is_corrupt(tmp_path):
    """The acceptance check: a corrupt logs.db must not fail a real write."""
    kb = _make_kb(tmp_path)
    (kb / ".kvault" / "logs.db").write_bytes(os.urandom(4096))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--kb-root", str(kb), "--json", "write", "people/contacts/jane", "--create"],
        input="# Jane\n\nx.\n",
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert (kb / "people" / "contacts" / "jane" / "_summary.md").exists()
    # And the failure is reported, not hidden.
    assert any(n["code"] == "skipped" for n in payload["notes"])


def test_oplog_can_be_disabled_by_env(tmp_path, monkeypatch):
    kb = _make_kb(tmp_path)
    monkeypatch.setenv("KVAULT_OPS_LOG", "0")
    ok = OpLog(kb).append("write", {"success": True, "path": "p"})
    assert ok is True  # a no-op, not a failure
    monkeypatch.delenv("KVAULT_OPS_LOG")
    assert OpLog(kb).tail() == []


def test_session_env_var_correlates_invocations(tmp_path, monkeypatch):
    monkeypatch.setenv("KVAULT_SESSION", "task-42")
    assert resolve_session_id() == "task-42"
    kb = _make_kb(tmp_path)
    OpLog(kb).append("write", {"success": True, "path": "a"})
    OpLog(kb).append("delete", {"success": True, "path": "b"})
    rows = OpLog(kb).tail()
    assert {r["session"] for r in rows} == {"task-42"}


def test_prune_keeps_the_table_bounded(tmp_path):
    kb = _make_kb(tmp_path)
    log = OpLog(kb)
    conn = sqlite3.connect(log.db_path)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS ops (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
        " session TEXT NOT NULL, surface TEXT NOT NULL, op TEXT NOT NULL, path TEXT, did TEXT,"
        " changed INTEGER, partial INTEGER, notes TEXT, ms REAL);"
    )
    conn.executemany(
        "INSERT INTO ops (ts, session, surface, op) VALUES ('t', 's', 'cli', 'write')",
        [()] * (MAX_ROWS + PRUNE_SLACK),
    )
    conn.commit()
    conn.close()

    log.append("write", {"success": True, "path": "p"})

    conn = sqlite3.connect(log.db_path)
    count = conn.execute("SELECT COUNT(*) FROM ops").fetchone()[0]
    conn.close()
    assert count == MAX_ROWS


def test_giant_notes_payload_is_capped(tmp_path):
    kb = _make_kb(tmp_path)
    huge = [{"code": "created", "text": "x" * 5000, "level": 1}]
    OpLog(kb).append("write", {"success": True, "path": "p", "notes": huge})
    row = OpLog(kb).tail()[0]
    stored = json.dumps(row["notes"]) if row["notes"] else ""
    assert len(stored) <= 4200  # MAX_NOTES_CHARS + json overhead margin


def test_no_wal_anywhere():
    """WAL creates -wal/-shm sidecars; live KBs gitignore only *.db, and the
    Moss git-sync automation blocks on unexpected untracked files."""
    for path in sorted((REPO_ROOT / "kvault").rglob("*.py")):
        assert "journal_mode=wal" not in path.read_text().lower(), path


def test_mtime_of_summary_untouched_by_oplog(tmp_path):
    """The ops log lives in .kvault/ — appending must not touch node files."""
    kb = _make_kb(tmp_path)
    ops.write_node(kb, "people/contacts/a", "# A\n\nx.\n", create=True)
    summary = kb / "people" / "contacts" / "a" / "_summary.md"
    before = summary.stat().st_mtime_ns
    OpLog(kb).append("write", {"success": True, "path": "people/contacts/a"})
    assert summary.stat().st_mtime_ns == before
