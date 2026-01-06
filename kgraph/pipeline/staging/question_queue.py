"""
Question queue for human review of ambiguous decisions.

When the decision agent cannot confidently reconcile an entity,
it queues a question for human review. This module manages that queue.
"""

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from contextlib import contextmanager

from kgraph.pipeline.audit import log_audit


QUESTION_SCHEMA = """
-- Question queue for human review
CREATE TABLE IF NOT EXISTS question_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    staged_op_id INTEGER,          -- Links to staged_operations.id
    question_type TEXT NOT NULL,   -- reconcile, tier, duplicate, other
    question_text TEXT NOT NULL,
    context_data TEXT,             -- JSON blob with additional context
    suggested_action TEXT,         -- Suggested answer
    confidence REAL,               -- Decision confidence that triggered review
    priority INTEGER DEFAULT 50,   -- 1-100, lower = more urgent
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, answered, skipped, expired
    user_answer TEXT,
    answered_at TEXT,
    created_at TEXT NOT NULL,

    FOREIGN KEY (staged_op_id) REFERENCES staged_operations(id)
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_question_batch ON question_queue(batch_id);
CREATE INDEX IF NOT EXISTS idx_question_status ON question_queue(status);
CREATE INDEX IF NOT EXISTS idx_question_priority ON question_queue(priority, id);
CREATE INDEX IF NOT EXISTS idx_question_staged_op ON question_queue(staged_op_id);
"""


@dataclass
class PendingQuestion:
    """
    A question awaiting human review.

    Created when DecisionAgent has low confidence and needs human input.
    """

    id: Optional[int] = None
    """Database ID (set after insert)"""

    batch_id: str = ""
    """Batch this question belongs to"""

    staged_op_id: Optional[int] = None
    """Link to the staged operation this question is about"""

    question_type: str = "reconcile"
    """Type: reconcile, tier, duplicate, other"""

    question_text: str = ""
    """Human-readable question"""

    context: Dict[str, Any] = field(default_factory=dict)
    """Additional context for making the decision"""

    suggested_action: Optional[str] = None
    """What the system suggests (if any)"""

    confidence: float = 0.5
    """Confidence level that triggered review"""

    priority: int = 50
    """Priority 1-100, lower = more urgent"""

    status: str = "pending"
    """Status: pending, answered, skipped, expired"""

    user_answer: Optional[str] = None
    """User's answer (set when answered)"""

    answered_at: Optional[str] = None
    """When the question was answered"""

    created_at: Optional[str] = None
    """When the question was created"""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PendingQuestion":
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            batch_id=data.get("batch_id", ""),
            staged_op_id=data.get("staged_op_id"),
            question_type=data.get("question_type", "reconcile"),
            question_text=data.get("question_text", ""),
            context=data.get("context", {}),
            suggested_action=data.get("suggested_action"),
            confidence=data.get("confidence", 0.5),
            priority=data.get("priority", 50),
            status=data.get("status", "pending"),
            user_answer=data.get("user_answer"),
            answered_at=data.get("answered_at"),
            created_at=data.get("created_at"),
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PendingQuestion":
        """Create from database row."""
        d = dict(row)
        # Parse JSON context
        if d.get("context_data"):
            d["context"] = json.loads(d["context_data"])
        else:
            d["context"] = {}
        return cls.from_dict(d)


class QuestionQueue:
    """
    SQLite-backed queue for human review questions.

    Questions are prioritized by confidence - lower confidence means
    higher priority (more urgent need for human input).
    """

    def __init__(self, db_path: Path):
        """
        Initialize question queue.

        Args:
            db_path: Path to SQLite database file (shared with StagingDatabase)
        """
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with self._conn() as conn:
            conn.executescript(QUESTION_SCHEMA)

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

    def add_question(
        self,
        batch_id: str,
        question_type: str,
        question_text: str,
        staged_op_id: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
        suggested_action: Optional[str] = None,
        confidence: float = 0.5,
    ) -> int:
        """
        Add a question to the queue.

        Priority is calculated from confidence:
        - confidence 0.0 → priority 1 (most urgent)
        - confidence 0.5 → priority 50
        - confidence 0.8 → priority 80

        Args:
            batch_id: Batch identifier
            question_type: Type of question
            question_text: Human-readable question
            staged_op_id: Link to staged operation
            context: Additional context dict
            suggested_action: Suggested answer
            confidence: Decision confidence (0.0-1.0)

        Returns:
            Question ID
        """
        # Calculate priority: lower confidence = lower priority number = more urgent
        priority = max(1, min(100, int(confidence * 100)))

        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO question_queue (
                    batch_id, staged_op_id, question_type, question_text,
                    context_data, suggested_action, confidence, priority,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    batch_id,
                    staged_op_id,
                    question_type,
                    question_text,
                    json.dumps(context or {}),
                    suggested_action,
                    confidence,
                    priority,
                    datetime.now().isoformat(),
                ),
            )
            question_id = cursor.lastrowid

        log_audit(
            "question",
            "queued",
            {
                "question_id": question_id,
                "batch_id": batch_id,
                "type": question_type,
                "confidence": confidence,
                "priority": priority,
            },
        )

        return question_id

    def get_question(self, question_id: int) -> Optional[PendingQuestion]:
        """Get a question by ID."""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT id, batch_id, staged_op_id, question_type, question_text,
                       context_data, suggested_action, confidence, priority,
                       status, user_answer, answered_at, created_at
                FROM question_queue
                WHERE id = ?
                """,
                (question_id,),
            )
            row = cursor.fetchone()

        if not row:
            return None

        return PendingQuestion.from_row(row)

    def get_pending(
        self,
        batch_id: Optional[str] = None,
        question_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[PendingQuestion]:
        """
        Get pending questions, ordered by priority (most urgent first).

        Args:
            batch_id: Optional filter by batch
            question_type: Optional filter by type
            limit: Maximum questions to return

        Returns:
            List of PendingQuestion objects
        """
        query = """
            SELECT id, batch_id, staged_op_id, question_type, question_text,
                   context_data, suggested_action, confidence, priority,
                   status, user_answer, answered_at, created_at
            FROM question_queue
            WHERE status = 'pending'
        """
        params: List[Any] = []

        if batch_id:
            query += " AND batch_id = ?"
            params.append(batch_id)

        if question_type:
            query += " AND question_type = ?"
            params.append(question_type)

        query += " ORDER BY priority ASC, id ASC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            cursor = conn.execute(query, params)
            return [PendingQuestion.from_row(row) for row in cursor.fetchall()]

    def get_next(self, batch_id: Optional[str] = None) -> Optional[PendingQuestion]:
        """
        Get the next question to answer (highest priority pending).

        Args:
            batch_id: Optional filter by batch

        Returns:
            Next question or None if queue is empty
        """
        questions = self.get_pending(batch_id=batch_id, limit=1)
        return questions[0] if questions else None

    def answer(
        self,
        question_id: int,
        answer: str,
        update_staged_op: bool = True,
    ) -> bool:
        """
        Answer a question.

        Args:
            question_id: Question ID
            answer: User's answer
            update_staged_op: Whether to update linked staged operation

        Returns:
            True if question was answered, False if not found
        """
        with self._conn() as conn:
            # Get question to check if it exists and get staged_op_id
            cursor = conn.execute(
                "SELECT staged_op_id FROM question_queue WHERE id = ? AND status = 'pending'",
                (question_id,),
            )
            row = cursor.fetchone()

            if not row:
                return False

            staged_op_id = row["staged_op_id"]

            # Update question
            conn.execute(
                """
                UPDATE question_queue
                SET status = 'answered', user_answer = ?, answered_at = ?
                WHERE id = ?
                """,
                (answer, datetime.now().isoformat(), question_id),
            )

            # Update linked staged operation status if requested
            if update_staged_op and staged_op_id:
                # Parse answer to determine new status
                if answer.lower() in ("reject", "rejected", "no"):
                    new_status = "rejected"
                elif answer.lower() in ("approve", "approved", "yes"):
                    new_status = "ready"
                else:
                    # Custom answer - mark ready but could be refined
                    new_status = "ready"

                conn.execute(
                    """
                    UPDATE staged_operations
                    SET status = ?
                    WHERE id = ?
                    """,
                    (new_status, staged_op_id),
                )

        log_audit(
            "question",
            "answered",
            {
                "question_id": question_id,
                "answer": answer,
                "staged_op_id": staged_op_id,
            },
        )

        return True

    def skip(self, question_id: int) -> bool:
        """
        Skip a question (defer for later).

        Args:
            question_id: Question ID

        Returns:
            True if question was skipped, False if not found
        """
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE question_queue
                SET status = 'skipped'
                WHERE id = ? AND status = 'pending'
                """,
                (question_id,),
            )

        if cursor.rowcount > 0:
            log_audit("question", "skipped", {"question_id": question_id})
            return True
        return False

    def count_pending(self, batch_id: Optional[str] = None) -> int:
        """
        Count pending questions.

        Args:
            batch_id: Optional filter by batch

        Returns:
            Number of pending questions
        """
        query = "SELECT COUNT(*) as count FROM question_queue WHERE status = 'pending'"
        params: List[Any] = []

        if batch_id:
            query += " AND batch_id = ?"
            params.append(batch_id)

        with self._conn() as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchone()["count"]

    def count_by_status(self, batch_id: Optional[str] = None) -> Dict[str, int]:
        """
        Get counts by status.

        Args:
            batch_id: Optional filter by batch

        Returns:
            Dict of status -> count
        """
        query = """
            SELECT status, COUNT(*) as count
            FROM question_queue
        """
        params: List[Any] = []

        if batch_id:
            query += " WHERE batch_id = ?"
            params.append(batch_id)

        query += " GROUP BY status"

        with self._conn() as conn:
            cursor = conn.execute(query, params)
            return {row["status"]: row["count"] for row in cursor.fetchall()}

    def expire_old(self, days: int = 30) -> int:
        """
        Expire questions older than specified days.

        Args:
            days: Age in days after which to expire

        Returns:
            Number of questions expired
        """
        cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        # Simple day subtraction (not accounting for DST edge cases)
        from datetime import timedelta
        cutoff = cutoff - timedelta(days=days)

        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE question_queue
                SET status = 'expired'
                WHERE status = 'pending' AND created_at < ?
                """,
                (cutoff.isoformat(),),
            )
            expired = cursor.rowcount

        if expired > 0:
            log_audit("question", "expired", {"count": expired, "days": days})

        return expired

    def get_for_staged_op(self, staged_op_id: int) -> List[PendingQuestion]:
        """
        Get all questions for a staged operation.

        Args:
            staged_op_id: Staged operation ID

        Returns:
            List of questions for this operation
        """
        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT id, batch_id, staged_op_id, question_type, question_text,
                       context_data, suggested_action, confidence, priority,
                       status, user_answer, answered_at, created_at
                FROM question_queue
                WHERE staged_op_id = ?
                ORDER BY created_at ASC
                """,
                (staged_op_id,),
            )
            return [PendingQuestion.from_row(row) for row in cursor.fetchall()]


def create_reconcile_question(
    batch_id: str,
    entity_name: str,
    candidates: List[Dict[str, Any]],
    confidence: float,
    staged_op_id: Optional[int] = None,
) -> PendingQuestion:
    """
    Factory function to create a reconciliation question.

    Args:
        batch_id: Batch identifier
        entity_name: Name of entity being reconciled
        candidates: List of match candidates
        confidence: Decision confidence
        staged_op_id: Link to staged operation

    Returns:
        PendingQuestion ready for queue
    """
    # Format candidates for human readability
    candidate_lines = []
    for i, c in enumerate(candidates[:5], 1):
        score = c.get("match_score", c.get("score", 0))
        name = c.get("candidate_name", c.get("name", "Unknown"))
        match_type = c.get("match_type", c.get("type", "unknown"))
        candidate_lines.append(f"  {i}. {name} ({match_type}, score: {score:.2f})")

    candidates_text = "\n".join(candidate_lines) if candidate_lines else "  (no candidates)"

    question_text = f"""Should "{entity_name}" be merged with an existing entity?

Top candidates:
{candidates_text}

Options:
- "merge:N" - Merge with candidate N (e.g., "merge:1")
- "create" - Create as new entity
- "skip" - Skip this entity
"""

    return PendingQuestion(
        batch_id=batch_id,
        staged_op_id=staged_op_id,
        question_type="reconcile",
        question_text=question_text,
        context={
            "entity_name": entity_name,
            "candidates": candidates[:5],
        },
        suggested_action="create" if not candidates else f"merge:1",
        confidence=confidence,
    )
