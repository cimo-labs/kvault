"""Durable per-operation log — the ``ops`` table in ``.kvault/logs.db``.

One row per completed KB operation: what ran, against which path, what it
decided (the ``did`` line and any notes), and how long it took. This is the
store behind ``kvault log tail`` — "what happened to this KB recently, and
what did kvault decide along the way".

Design constraints, all deliberate:

- **A logging failure must never fail a KB operation.** ``append`` swallows
  every exception and returns ``False``; callers may attach a ``skipped``
  note. The append also runs *after* the mutation has committed, never inside
  its critical section.
- **No WAL.** WAL creates ``-wal``/``-shm`` sidecar files next to the DB.
  Live KBs gitignore ``.kvault/*.db`` — the sidecars would be untracked, and
  the Moss maintenance automation treats unexpected untracked files as a
  reason to block git sync. Logging sits off the write path, so the
  contention WAL solves does not exist here.
- **UTC everywhere.** The old ``logs`` table mixed local-time session ids
  with UTC row timestamps — an 8-hour skew inside a single row.
- **Bounded.** Note payloads are capped and old rows pruned, because the old
  table accumulated 58 KB single rows with no retention at all.

The legacy ``logs`` table (``ObservabilityLogger``) is left untouched for
backward compatibility; new work lands here.
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

SESSION_ENV = "KVAULT_SESSION"
DISABLE_ENV = "KVAULT_OPS_LOG"

MAX_NOTES_CHARS = 4096
MAX_ROWS = 5000
#: Prune when the table exceeds MAX_ROWS by this slack, so the DELETE runs
#: occasionally rather than on every append at the boundary.
PRUNE_SLACK = 256

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session TEXT NOT NULL,
    surface TEXT NOT NULL,
    op TEXT NOT NULL,
    path TEXT,
    did TEXT,
    changed INTEGER,
    partial INTEGER,
    notes TEXT,
    ms REAL
);
CREATE INDEX IF NOT EXISTS idx_ops_session ON ops(session);
"""


def new_session_id() -> str:
    """Mint a session id: UTC timestamp + entropy, sortable and unambiguous."""
    return f"s{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def resolve_session_id(explicit: Optional[str] = None) -> str:
    """Correlation id for this process: explicit > $KVAULT_SESSION > minted.

    The env var is how an agent ties the several commands of one logical KB
    task (write → propagate → check) into one session across invocations.
    """
    if explicit:
        return str(explicit)[:64]
    env = os.environ.get(SESSION_ENV, "").strip()
    if env:
        return env[:64]
    return new_session_id()


def oplog_disabled() -> bool:
    """True when the durable append is turned off via KVAULT_OPS_LOG=0."""
    return os.environ.get(DISABLE_ENV, "").strip() == "0"


class OpLog:
    """Append/read the ``ops`` table. Every method is failure-proof by contract."""

    def __init__(
        self,
        kg_root: Optional[Union[str, Path]] = None,
        session_id: Optional[str] = None,
        db_path: Optional[Union[str, Path]] = None,
    ):
        if db_path is not None:
            self.db_path = Path(db_path)
        elif kg_root is not None:
            self.db_path = Path(kg_root) / ".kvault" / "logs.db"
        else:
            raise ValueError("OpLog requires kg_root or db_path")
        self.session_id = resolve_session_id(session_id)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=2.0)
        conn.executescript(_SCHEMA)
        return conn

    def append(
        self,
        op: str,
        result: Dict[str, Any],
        ms: Optional[float] = None,
        surface: str = "cli",
    ) -> bool:
        """Record one completed operation. Returns False instead of raising.

        Called after the KB mutation has already committed — this can lose a
        log row, never a write.
        """
        if oplog_disabled():
            return True
        conn: Optional[sqlite3.Connection] = None
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            notes_json: Optional[str] = None
            notes = result.get("notes")
            if notes:
                notes_json = json.dumps(notes, default=str)
                if len(notes_json) > MAX_NOTES_CHARS:
                    # Cap by DROPPING whole entries, never by slicing the JSON
                    # string — a mid-document slice stores invalid JSON that
                    # tail() can only read back as None, silently losing the
                    # decisions this log exists to preserve.
                    compact = [
                        {"code": n.get("code"), "text": str(n.get("text", ""))[:200]} for n in notes
                    ]
                    while compact:
                        candidate = compact + [
                            {"code": "truncated", "text": f"{len(notes)} notes total"}
                        ]
                        notes_json = json.dumps(candidate, default=str)
                        if len(notes_json) <= MAX_NOTES_CHARS:
                            break
                        compact.pop()
                    else:
                        notes_json = json.dumps(
                            [{"code": "truncated", "text": f"{len(notes)} notes total"}]
                        )
            conn = self._connect()
            conn.execute(
                "INSERT INTO ops (ts, session, surface, op, path, did, changed, partial, notes, ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    self.session_id,
                    surface,
                    op,
                    result.get("path") or result.get("event_id") or result.get("source"),
                    result.get("did"),
                    None if "changed" not in result else int(bool(result.get("changed"))),
                    int(bool(result.get("partial"))),
                    notes_json,
                    None if ms is None else round(float(ms), 1),
                ),
            )
            count = conn.execute("SELECT COUNT(*) FROM ops").fetchone()[0]
            if count > MAX_ROWS + PRUNE_SLACK:
                conn.execute(
                    "DELETE FROM ops WHERE id IN " "(SELECT id FROM ops ORDER BY id ASC LIMIT ?)",
                    (count - MAX_ROWS,),
                )
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def tail(self, limit: int = 20, session: Optional[str] = None) -> List[Dict[str, Any]]:
        """Most recent operations, newest first. Empty list on any failure."""
        conn: Optional[sqlite3.Connection] = None
        try:
            if not self.db_path.exists():
                return []
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=2.0)
            conn.row_factory = sqlite3.Row
            if session:
                rows = conn.execute(
                    "SELECT * FROM ops WHERE session = ? ORDER BY id DESC LIMIT ?",
                    (session, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ops ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            out: List[Dict[str, Any]] = []
            for row in rows:
                entry = dict(row)
                if entry.get("notes"):
                    try:
                        entry["notes"] = json.loads(entry["notes"])
                    except (ValueError, TypeError):
                        entry["notes"] = None
                entry["changed"] = None if entry["changed"] is None else bool(entry["changed"])
                entry["partial"] = bool(entry["partial"])
                out.append(entry)
            return out
        except Exception:
            return []
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def summary(self) -> Dict[str, Any]:
        """Aggregate counts over the ops table. Zeroes on any failure."""
        empty = {"total_ops": 0, "sessions": 0, "op_counts": {}, "partial_count": 0}
        conn: Optional[sqlite3.Connection] = None
        try:
            if not self.db_path.exists():
                return empty
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=2.0)
            op_counts: Dict[str, int] = {}
            for row in conn.execute("SELECT op, COUNT(*) FROM ops GROUP BY op"):
                op_counts[str(row[0])] = row[1]
            total = sum(op_counts.values())
            sessions = conn.execute("SELECT COUNT(DISTINCT session) FROM ops").fetchone()[0]
            partial = conn.execute("SELECT COUNT(*) FROM ops WHERE partial = 1").fetchone()[0]
            return {
                "total_ops": total,
                "sessions": sessions,
                "op_counts": op_counts,
                "partial_count": partial,
            }
        except Exception:
            return empty
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


__all__ = [
    "OpLog",
    "new_session_id",
    "resolve_session_id",
    "oplog_disabled",
    "SESSION_ENV",
    "DISABLE_ENV",
]
