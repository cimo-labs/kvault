"""Atomic file writes and a per-KB write lock.

The lock serializes mutating operations from concurrent kvault processes
(agents, cron pipelines, interactive sessions) against one knowledge base.
It is deliberately simple: a lock directory created with an atomic ``mkdir``,
an owner file for diagnostics, staleness detection at acquire time, and an
atomic rename-then-remove break so two waiters can never free the same lock
twice.  Hostnames are not recorded as identity — this targets single-machine
deployments where hostnames drift with DHCP.
"""

import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

LOCK_DIR_NAME = "lock"
OWNER_FILE_NAME = "owner.json"

# A lock whose owner process is gone is breakable after this grace period
# (covers the window between mkdir and the owner file landing).
_DEAD_OWNER_GRACE_SECONDS = 5.0
# A lock is breakable regardless of owner liveness after this long — a wedged
# holder should not block a KB forever.
_HARD_STALE_SECONDS = 600.0


class LockError(RuntimeError):
    """Raised when the KB write lock cannot be acquired."""


def atomic_write_text(path: Union[str, Path], content: str) -> None:
    """Write *content* to *path* atomically via a same-directory temp file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


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
    """Per-KB advisory write lock, reentrant within the owning process.

    Usage::

        with KBWriteLock(kg_root):
            ...mutate the KB...
    """

    _local_depth: Dict[str, int] = {}
    _local_guard = threading.RLock()

    def __init__(self, kg_root: Union[str, Path], timeout: float = 10.0):
        self.root = Path(kg_root).expanduser().resolve()
        self.timeout = timeout
        self.lock_dir = self.root / ".kvault" / LOCK_DIR_NAME
        self._key = str(self.root)

    # -- staleness ---------------------------------------------------------

    def _owner_metadata(self) -> Optional[Dict[str, Any]]:
        owner_file = self.lock_dir / OWNER_FILE_NAME
        try:
            return json.loads(owner_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _lock_age_seconds(self) -> Optional[float]:
        try:
            return max(0.0, time.time() - self.lock_dir.stat().st_mtime)
        except OSError:
            return None

    def _is_stale(self) -> bool:
        age = self._lock_age_seconds()
        if age is None:
            return False
        if age > _HARD_STALE_SECONDS:
            return True
        if age < _DEAD_OWNER_GRACE_SECONDS:
            return False
        owner = self._owner_metadata()
        if owner is None:
            # Past the grace period with no readable owner file: crashed
            # between mkdir and the owner write.
            return True
        pid = owner.get("pid")
        return isinstance(pid, int) and not _pid_alive(pid)

    def _break_stale(self) -> None:
        """Break a stale lock atomically; losing a race here is fine."""
        tombstone = self.lock_dir.parent / f"{LOCK_DIR_NAME}.stale.{uuid.uuid4().hex}"
        try:
            os.rename(self.lock_dir, tombstone)
        except OSError:
            return  # someone else broke or refreshed it first
        shutil.rmtree(tombstone, ignore_errors=True)

    # -- acquire/release ---------------------------------------------------

    def acquire(self) -> None:
        with self._local_guard:
            depth = self._local_depth.get(self._key, 0)
            if depth > 0:
                self._local_depth[self._key] = depth + 1
                return

        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.lock_dir.mkdir(parents=True)
            except FileExistsError:
                if self._is_stale():
                    self._break_stale()
                    continue
                if time.monotonic() >= deadline:
                    owner = self._owner_metadata() or {}
                    raise LockError(
                        f"Could not acquire KB write lock at {self.lock_dir} "
                        f"within {self.timeout:.0f}s (held by pid {owner.get('pid', '?')}). "
                        "If no kvault process is running, the lock will expire on its own."
                    )
                time.sleep(0.1)
                continue

            atomic_write_text(
                self.lock_dir / OWNER_FILE_NAME,
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "acquired_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )
            with self._local_guard:
                self._local_depth[self._key] = 1
            return

    def release(self) -> None:
        with self._local_guard:
            depth = self._local_depth.get(self._key, 0)
            if depth > 1:
                self._local_depth[self._key] = depth - 1
                return
            self._local_depth.pop(self._key, None)
        shutil.rmtree(self.lock_dir, ignore_errors=True)

    def __enter__(self) -> "KBWriteLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


__all__ = ["KBWriteLock", "LockError", "atomic_write_text"]
