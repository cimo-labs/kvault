"""Atomic filesystem primitives and recoverable KB write transactions."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from kvault.core.paths import PathSafetyError, resolve_within_root

_TRANSACTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")


class LockBusyError(RuntimeError):
    """Raised when another reconciliation owns the KB write lock."""


class TransactionError(RuntimeError):
    """Raised for invalid or unrecoverable transaction state."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Durably replace *path* with *data* using a same-directory rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_write_text(path: Path, content: str) -> None:
    atomic_write_bytes(path, content.encode("utf-8"))


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def file_revision(path: Path) -> Optional[str]:
    """Return a stable SHA-256 revision for a file, or ``None`` when absent."""
    if not path.is_file():
        return None
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class KBWriteLock:
    """Cross-platform single-writer lock implemented by atomic directory creation."""

    def __init__(self, root: Path, owner: str):
        self.root = Path(root).resolve()
        self.owner = owner
        self.lock_dir = resolve_within_root(
            self.root,
            ".kvault/locks/write.lock",
            allow_root=False,
            reject_symlinks=True,
        )
        self.acquired = False

    def acquire(self) -> None:
        self.lock_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.lock_dir.mkdir()
        except FileExistsError as exc:
            metadata = self.metadata()
            raise LockBusyError(
                "KB write lock is already held"
                + (f" by {metadata.get('owner')}" if metadata else "")
            ) from exc
        try:
            atomic_write_json(
                self.lock_dir / "owner.json",
                {
                    "owner": self.owner,
                    "pid": os.getpid(),
                    "hostname": socket.gethostname(),
                    "acquired_at": utc_now(),
                },
            )
        except Exception:
            shutil.rmtree(self.lock_dir, ignore_errors=True)
            raise
        self.acquired = True

    def metadata(self) -> Dict[str, Any]:
        try:
            return json.loads((self.lock_dir / "owner.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    def is_stale(self) -> bool:
        metadata = self.metadata()
        if not metadata:
            return True
        if metadata.get("hostname") != socket.gethostname():
            return False
        try:
            return not _pid_alive(int(metadata.get("pid", -1)))
        except (TypeError, ValueError):
            return True

    def release(self) -> None:
        if self.acquired:
            shutil.rmtree(self.lock_dir, ignore_errors=True)
            self.acquired = False

    def __enter__(self) -> "KBWriteLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()


@dataclass
class Snapshot:
    relative_path: str
    existed: bool
    kind: str


class FileTransaction:
    """Recoverable transaction using write-ahead state and filesystem snapshots.

    Multi-file replacement is not atomically available on ordinary filesystems. This
    class therefore records every target before mutation, applies individual atomic
    file writes under a KB-wide lock, and can restore the complete pre-state after an
    exception or interrupted process.
    """

    def __init__(self, root: Path, transaction_id: str):
        self.root = Path(root).resolve()
        if not _TRANSACTION_ID_RE.fullmatch(transaction_id):
            raise TransactionError("Invalid transaction identifier")
        self.transaction_id = transaction_id
        try:
            self.tx_dir = resolve_within_root(
                self.root,
                Path(".kvault") / "transactions" / transaction_id,
                allow_root=False,
                reject_symlinks=True,
            )
        except PathSafetyError as exc:
            raise TransactionError("Unsafe transaction path") from exc
        self.backup_dir = self.tx_dir / "backups"
        self.stage_dir = self.tx_dir / "stage"
        self.trash_dir = self.tx_dir / "trash"
        self.state_path = self.tx_dir / "state.json"
        self.snapshots: List[Snapshot] = []
        self.staged: List[str] = []
        self.applied: List[str] = []

    @property
    def state(self) -> Dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    def _write_state(self, status: str, **extra: Any) -> None:
        payload: Dict[str, Any] = {
            "schema_version": 1,
            "transaction_id": self.transaction_id,
            "status": status,
            "updated_at": utc_now(),
            "snapshots": [snapshot.__dict__ for snapshot in self.snapshots],
            "staged": list(self.staged),
            "applied": list(self.applied),
        }
        payload.update(extra)
        atomic_write_json(self.state_path, payload)

    def begin(self, relative_paths: Iterable[str]) -> None:
        if self.tx_dir.exists():
            raise TransactionError(f"Transaction already exists: {self.transaction_id}")
        unique = sorted({str(Path(path)) for path in relative_paths})
        resolved: List[tuple[str, Path]] = []
        for rel in unique:
            try:
                absolute = resolve_within_root(
                    self.root,
                    rel,
                    allow_root=False,
                    reject_symlinks=True,
                )
            except PathSafetyError as exc:
                raise TransactionError(f"Unsafe snapshot path: {rel}") from exc
            if absolute.is_symlink():
                raise TransactionError(f"Refusing to snapshot symlink target: {rel}")
            resolved.append((rel, absolute))

        try:
            self.backup_dir.mkdir(parents=True)
            self.stage_dir.mkdir()
            for rel, absolute in resolved:
                backup = self.backup_dir / rel
                if absolute.is_dir():
                    symlink = next(
                        (item for item in absolute.rglob("*") if item.is_symlink()), None
                    )
                    if symlink is not None:
                        raise TransactionError(
                            "Refusing to snapshot a directory containing a symlink: "
                            f"{symlink.relative_to(self.root)}"
                        )
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(absolute, backup)
                    kind = "directory"
                    existed = True
                elif absolute.is_file():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(absolute, backup)
                    kind = "file"
                    existed = True
                else:
                    kind = "missing"
                    existed = False
                self.snapshots.append(Snapshot(rel, existed, kind))
            self._write_state("prepared", created_at=utc_now())
        except Exception:
            shutil.rmtree(self.tx_dir, ignore_errors=True)
            self.snapshots = []
            raise

    def mark_staged(self, relative_paths: Iterable[str]) -> None:
        self.staged = sorted({str(Path(path)) for path in relative_paths})
        self._write_state("staged", staged_at=utc_now())

    def mark_applying(self) -> None:
        self._write_state("applying")

    def mark_applied(self, relative_path: str) -> None:
        self.applied.append(relative_path)
        self._write_state("applying")

    def commit(self) -> None:
        self._write_state("committed", committed_at=utc_now())
        shutil.rmtree(self.backup_dir, ignore_errors=True)
        shutil.rmtree(self.stage_dir, ignore_errors=True)
        shutil.rmtree(self.trash_dir, ignore_errors=True)

    def rollback(self, reason: Optional[str] = None) -> None:
        if not self.snapshots:
            self._load_snapshots()
        for snapshot in reversed(self.snapshots):
            try:
                absolute = resolve_within_root(
                    self.root,
                    snapshot.relative_path,
                    allow_root=False,
                    reject_symlinks=True,
                )
            except PathSafetyError as exc:
                raise TransactionError(f"Unsafe rollback path: {snapshot.relative_path}") from exc
            backup = self.backup_dir / snapshot.relative_path
            if absolute.is_dir() and not absolute.is_symlink():
                shutil.rmtree(absolute)
            elif absolute.exists() or absolute.is_symlink():
                absolute.unlink()
            if snapshot.existed:
                absolute.parent.mkdir(parents=True, exist_ok=True)
                if snapshot.kind == "directory":
                    shutil.copytree(backup, absolute)
                elif snapshot.kind == "file":
                    shutil.copy2(backup, absolute)
        self._write_state("rolled_back", rolled_back_at=utc_now(), reason=reason)
        shutil.rmtree(self.backup_dir, ignore_errors=True)
        shutil.rmtree(self.stage_dir, ignore_errors=True)
        shutil.rmtree(self.trash_dir, ignore_errors=True)

    def _load_snapshots(self) -> None:
        state = self.state
        self.snapshots = [Snapshot(**item) for item in state.get("snapshots", [])]
        self.staged = list(state.get("staged", []))
        self.applied = list(state.get("applied", []))

    @classmethod
    def active(cls, root: Path) -> List["FileTransaction"]:
        try:
            tx_root = resolve_within_root(
                Path(root).resolve(),
                ".kvault/transactions",
                allow_root=False,
                reject_symlinks=True,
            )
        except PathSafetyError as exc:
            raise TransactionError("Unsafe transaction directory") from exc
        if not tx_root.exists():
            return []
        transactions: List[FileTransaction] = []
        for path in sorted(tx_root.iterdir()):
            if not path.is_dir():
                continue
            tx = cls(root, path.name)
            if tx.state.get("status") in {"prepared", "staged", "applying"}:
                tx._load_snapshots()
                transactions.append(tx)
        return transactions
