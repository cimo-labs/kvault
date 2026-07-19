from pathlib import Path

import pytest

from kvault.core.transactions import (
    FileTransaction,
    KBWriteLock,
    LockBusyError,
    atomic_write_text,
    file_revision,
)


def test_atomic_write_and_revision(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "value.md"
    atomic_write_text(target, "first\n")
    first = file_revision(target)
    atomic_write_text(target, "second\n")
    assert target.read_text() == "second\n"
    assert file_revision(target) != first


def test_write_lock_is_single_owner(tmp_path: Path) -> None:
    (tmp_path / ".kvault").mkdir()
    with KBWriteLock(tmp_path, "first"):
        with pytest.raises(LockBusyError):
            KBWriteLock(tmp_path, "second").acquire()

    with KBWriteLock(tmp_path, "second"):
        pass


def test_transaction_rollback_restores_files_and_directories(tmp_path: Path) -> None:
    (tmp_path / ".kvault").mkdir()
    node = tmp_path / "people" / "alice"
    node.mkdir(parents=True)
    (node / "_summary.md").write_text("before\n")
    new_file = tmp_path / "people" / "_summary.md"

    tx = FileTransaction(tmp_path, "tx_test")
    tx.begin(["people/alice", "people/_summary.md"])
    tx.mark_applying()
    (node / "_summary.md").write_text("after\n")
    atomic_write_text(new_file, "new\n")
    tx.mark_applied("people/alice/_summary.md")
    tx.mark_applied("people/_summary.md")
    tx.rollback("injected failure")

    assert (node / "_summary.md").read_text() == "before\n"
    assert not new_file.exists()
    assert tx.state["status"] == "rolled_back"


def test_active_transaction_can_be_reloaded_and_rolled_back(tmp_path: Path) -> None:
    (tmp_path / ".kvault").mkdir()
    target = tmp_path / "_summary.md"
    target.write_text("before\n")
    tx = FileTransaction(tmp_path, "tx_interrupted")
    tx.begin(["_summary.md"])
    tx.mark_applying()
    target.write_text("after\n")
    tx.mark_applied("_summary.md")

    active = FileTransaction.active(tmp_path)
    assert [item.transaction_id for item in active] == ["tx_interrupted"]
    active[0].rollback("recovered")
    assert target.read_text() == "before\n"


def test_transaction_refuses_nested_symlinks_before_snapshot(tmp_path: Path) -> None:
    from kvault.core.transactions import TransactionError

    (tmp_path / ".kvault").mkdir()
    node = tmp_path / "people" / "alice"
    node.mkdir(parents=True)
    (node / "_summary.md").write_text("before\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    (node / "attachment.txt").symlink_to(outside)
    tx = FileTransaction(tmp_path, "tx_symlink")

    with pytest.raises(TransactionError, match="containing a symlink"):
        tx.begin(["people/alice"])

    assert not tx.tx_dir.exists()
    assert outside.read_text() == "secret\n"
