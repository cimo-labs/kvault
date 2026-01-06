"""
SQLite database for staging operations.

All entity changes are staged here before being applied to the knowledge graph.
This provides:
- Atomic batching of operations
- Human review queue
- Audit trail
- Rollback capability
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from kgraph.pipeline.audit import log_audit


SCHEMA = """
-- Staged operations awaiting execution
CREATE TABLE IF NOT EXISTS staged_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    action TEXT NOT NULL,           -- merge, update, create
    target_path TEXT,               -- NULL for create
    confidence REAL NOT NULL,
    reasoning TEXT,
    entity_data TEXT NOT NULL,      -- JSON blob
    candidates_data TEXT,           -- JSON blob
    status TEXT NOT NULL DEFAULT 'staged',  -- staged, ready, pending_review, applied, failed, rejected
    priority INTEGER DEFAULT 3,     -- 1=merge, 2=update, 3=create
    created_at TEXT NOT NULL,
    applied_at TEXT,
    error_message TEXT
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_staged_batch ON staged_operations(batch_id);
CREATE INDEX IF NOT EXISTS idx_staged_status ON staged_operations(status);
CREATE INDEX IF NOT EXISTS idx_staged_priority ON staged_operations(priority, id);
"""


class StagingDatabase:
    """
    SQLite database for staged operations.

    Operations flow through these statuses:
    - staged: Initial state after extraction
    - ready: Approved for execution (auto or human)
    - pending_review: Waiting for human review
    - applied: Successfully applied to KG
    - failed: Error during application
    - rejected: Human rejected the operation
    """

    def __init__(self, db_path: Path):
        """
        Initialize staging database.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Get a database connection with proper settings."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def stage_operation(
        self,
        batch_id: str,
        entity_name: str,
        action: str,
        entity_data: Dict[str, Any],
        confidence: float,
        reasoning: str = "",
        target_path: Optional[str] = None,
        candidates: Optional[List[Dict]] = None,
        status: str = "staged",
    ) -> int:
        """
        Stage an operation for later execution.

        Args:
            batch_id: Batch identifier
            entity_name: Name of entity
            action: "merge", "update", or "create"
            entity_data: Entity data as dict
            confidence: Decision confidence (0.0-1.0)
            reasoning: Why this decision was made
            target_path: Target entity path for merge/update
            candidates: List of match candidates
            status: Initial status

        Returns:
            ID of staged operation
        """
        priority = {"merge": 1, "update": 2, "create": 3}.get(action, 3)

        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO staged_operations (
                    batch_id, entity_name, action, target_path,
                    confidence, reasoning, entity_data, candidates_data,
                    status, priority, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    entity_name,
                    action,
                    target_path,
                    confidence,
                    reasoning,
                    json.dumps(entity_data),
                    json.dumps(candidates or []),
                    status,
                    priority,
                    datetime.now().isoformat(),
                ),
            )

            op_id = cursor.lastrowid

        log_audit(
            "staging",
            "stage",
            {
                "operation_id": op_id,
                "entity": entity_name,
                "action": action,
                "confidence": confidence,
                "status": status,
            },
        )

        return op_id

    def get_operation(self, op_id: int) -> Optional[Dict[str, Any]]:
        """Get a single operation by ID."""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT id, batch_id, entity_name, action, target_path,
                       confidence, reasoning, entity_data, candidates_data,
                       status, priority, created_at, applied_at, error_message
                FROM staged_operations
                WHERE id = ?
                """,
                (op_id,),
            )
            row = cursor.fetchone()

        if not row:
            return None

        return self._row_to_dict(row)

    def get_ready_operations(
        self,
        batch_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Get operations ready for execution.

        Returns operations ordered by priority (merges first, then updates, then creates).

        Args:
            batch_id: Optional filter by batch
            limit: Maximum operations to return

        Returns:
            List of operation dicts
        """
        query = """
            SELECT id, batch_id, entity_name, action, target_path,
                   confidence, reasoning, entity_data, candidates_data,
                   status, priority, created_at, applied_at, error_message
            FROM staged_operations
            WHERE status = 'ready'
        """
        params: List[Any] = []

        if batch_id:
            query += " AND batch_id = ?"
            params.append(batch_id)

        query += " ORDER BY priority ASC, id ASC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_batch_operations(
        self,
        batch_id: str,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all operations for a batch."""
        query = """
            SELECT id, batch_id, entity_name, action, target_path,
                   confidence, reasoning, entity_data, candidates_data,
                   status, priority, created_at, applied_at, error_message
            FROM staged_operations
            WHERE batch_id = ?
        """
        params: List[Any] = [batch_id]

        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY priority ASC, id ASC"

        with self._conn() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def update_status(
        self,
        op_id: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Update operation status.

        Args:
            op_id: Operation ID
            status: New status
            error_message: Optional error message for failed operations
        """
        with self._conn() as conn:
            if status == "applied":
                conn.execute(
                    """
                    UPDATE staged_operations
                    SET status = ?, applied_at = ?
                    WHERE id = ?
                    """,
                    (status, datetime.now().isoformat(), op_id),
                )
            elif error_message:
                conn.execute(
                    """
                    UPDATE staged_operations
                    SET status = ?, error_message = ?
                    WHERE id = ?
                    """,
                    (status, error_message, op_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE staged_operations
                    SET status = ?
                    WHERE id = ?
                    """,
                    (status, op_id),
                )

        log_audit(
            "staging",
            status if status in ("ready", "reject") else "status_change",
            {"operation_id": op_id, "status": status},
        )

    def count_by_status(self) -> Dict[str, int]:
        """Get counts of operations by status."""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT status, COUNT(*) as count
                FROM staged_operations
                GROUP BY status
                """
            )
            return {row["status"]: row["count"] for row in cursor.fetchall()}

    def count_by_batch(self, batch_id: str) -> Dict[str, int]:
        """Get counts by status for a specific batch."""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT status, COUNT(*) as count
                FROM staged_operations
                WHERE batch_id = ?
                GROUP BY status
                """,
                (batch_id,),
            )
            return {row["status"]: row["count"] for row in cursor.fetchall()}

    def get_recent_batches(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent batch summaries."""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT
                    batch_id,
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'applied' THEN 1 ELSE 0 END) as applied,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN status = 'pending_review' THEN 1 ELSE 0 END) as pending,
                    MIN(created_at) as started_at,
                    MAX(applied_at) as completed_at
                FROM staged_operations
                GROUP BY batch_id
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a database row to a dict with parsed JSON fields."""
        d = dict(row)
        if d.get("entity_data"):
            d["entity_data"] = json.loads(d["entity_data"])
        if d.get("candidates_data"):
            d["candidates_data"] = json.loads(d["candidates_data"])
        return d
