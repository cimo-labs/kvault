"""
ObservabilityLogger - Phase-based logging for debugging and improvement.

Logs agent decisions with structured data for analysis.
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class LogEntry:
    """A log entry from the observability database."""

    id: int
    ts: str
    session: str
    phase: str
    data: Dict[str, Any] = field(default_factory=dict)


class ObservabilityLogger:
    """Phase-based logging for debugging and improvement.

    Phases:
    - input: What was received for processing
    - research: Entity lookup results and matching
    - decide: Agent decisions with reasoning
    - write: What was written to storage
    - propagate: Summary propagation chain
    - error: Errors and how they were handled
    """

    PHASES = [
        "input",
        "research",
        "decide",
        "write",
        "execute",
        "propagate",
        "log",
        "rebuild",
        "refactor",
        "error",
        # Orchestrator-specific phases
        "cli_raw",  # Full CLI output
        "step_research",
        "step_decide",
        "step_execute",
        "step_write",
        "step_propagate",
        "step_log",
        "step_rebuild",
        "step_refactor",
    ]

    def __init__(self, db_path: Path):
        """Initialize logger with database path.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.session_id = self._new_session()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                -- Main logs table
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL DEFAULT (datetime('now')),
                    session TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    data JSON NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_session ON logs(session);
                CREATE INDEX IF NOT EXISTS idx_phase ON logs(phase);
                CREATE INDEX IF NOT EXISTS idx_ts ON logs(ts);

                -- Analysis views
                CREATE VIEW IF NOT EXISTS errors AS
                SELECT id, ts, session,
                       json_extract(data, '$.error_type') as error_type,
                       json_extract(data, '$.entity') as entity,
                       json_extract(data, '$.resolution') as resolution,
                       data
                FROM logs WHERE phase = 'error';

                CREATE VIEW IF NOT EXISTS decisions AS
                SELECT id, ts, session,
                       json_extract(data, '$.entity') as entity,
                       json_extract(data, '$.action') as action,
                       json_extract(data, '$.confidence') as confidence,
                       json_extract(data, '$.reasoning') as reasoning
                FROM logs WHERE phase = 'decide';
            """)

    def _new_session(self) -> str:
        """Generate a new session ID."""
        return f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    def new_session(self) -> str:
        """Start a new session and return its ID.

        Returns:
            New session ID
        """
        self.session_id = self._new_session()
        return self.session_id

    def log(self, phase: str, data: Dict[str, Any]) -> None:
        """Log a phase with structured data.

        Args:
            phase: Phase name (input, research, decide, write, propagate, error, or step_*)
            data: Structured data for the log entry
        """
        # Allow any phase that's in PHASES or starts with "step_"
        if phase not in self.PHASES and not phase.startswith("step_"):
            raise ValueError(
                f"Invalid phase: {phase}. Must be one of {self.PHASES} or start with 'step_'"
            )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO logs (session, phase, data)
                VALUES (?, ?, ?)
                """,
                (self.session_id, phase, json.dumps(data, default=str)),
            )

    # Convenience methods

    def log_input(self, items: List[Dict], source: Optional[str] = None) -> None:
        """Log what was received for processing.

        Args:
            items: List of input items
            source: Optional source identifier
        """
        self.log(
            "input",
            {
                "items": items,
                "count": len(items),
                "source": source,
            },
        )

    def log_research(
        self,
        entity: str,
        query: str,
        matches: List[Dict],
        decision: str,
    ) -> None:
        """Log research results.

        Args:
            entity: Entity being researched
            query: Search query used
            matches: List of match results
            decision: Resulting decision
        """
        self.log(
            "research",
            {
                "entity": entity,
                "query": query,
                "matches": matches,
                "match_count": len(matches),
                "decision": decision,
            },
        )

    def log_decide(
        self,
        entity: str,
        action: str,
        reasoning: str,
        confidence: Optional[float] = None,
    ) -> None:
        """Log decision with reasoning.

        Args:
            entity: Entity the decision is about
            action: Action taken (create, update, skip, merge)
            reasoning: Explanation of why
            confidence: Optional confidence score (0.0-1.0)
        """
        data: Dict[str, Any] = {
            "entity": entity,
            "action": action,
            "reasoning": reasoning,
        }
        if confidence is not None:
            data["confidence"] = confidence

        self.log("decide", data)

    def log_write(
        self,
        path: str,
        change_type: str,
        diff_summary: str,
    ) -> None:
        """Log what was written.

        Args:
            path: Entity path written to
            change_type: Type of change (create, update, delete)
            diff_summary: Summary of changes
        """
        self.log(
            "write",
            {
                "path": path,
                "change_type": change_type,
                "diff_summary": diff_summary,
            },
        )

    def log_propagate(
        self,
        from_path: str,
        updated_paths: List[str],
        reasoning: Optional[str] = None,
    ) -> None:
        """Log propagation chain.

        Args:
            from_path: Source entity path
            updated_paths: List of ancestor paths that were updated
            reasoning: Optional explanation
        """
        self.log(
            "propagate",
            {
                "from_path": from_path,
                "updated_paths": updated_paths,
                "propagation_depth": len(updated_paths),
                "reasoning": reasoning,
            },
        )

    def log_error(
        self,
        error_type: str,
        entity: Optional[str] = None,
        details: Optional[Dict] = None,
        resolution: Optional[str] = None,
    ) -> None:
        """Log errors and how they were handled.

        Args:
            error_type: Type of error
            entity: Optional entity involved
            details: Optional additional details
            resolution: Optional resolution taken
        """
        data: Dict[str, Any] = {
            "error_type": error_type,
        }
        if entity:
            data["entity"] = entity
        if details:
            data["details"] = details
        if resolution:
            data["resolution"] = resolution

        self.log("error", data)

    # Query methods

    def get_session(self, session_id: Optional[str] = None) -> List[LogEntry]:
        """Get all logs for a session.

        Args:
            session_id: Session ID (defaults to current session)

        Returns:
            List of LogEntry objects
        """
        session_id = session_id or self.session_id

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM logs WHERE session = ? ORDER BY id",
                (session_id,),
            ).fetchall()

            return [
                LogEntry(
                    id=row["id"],
                    ts=row["ts"],
                    session=row["session"],
                    phase=row["phase"],
                    data=json.loads(row["data"]),
                )
                for row in rows
            ]

    def get_errors(self, since: Optional[str] = None, limit: int = 100) -> List[LogEntry]:
        """Get error logs.

        Args:
            since: Optional ISO timestamp to filter from
            limit: Maximum results

        Returns:
            List of error LogEntry objects
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if since:
                rows = conn.execute(
                    """
                    SELECT * FROM logs
                    WHERE phase = 'error' AND ts >= ?
                    ORDER BY ts DESC LIMIT ?
                    """,
                    (since, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM logs
                    WHERE phase = 'error'
                    ORDER BY ts DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

            return [
                LogEntry(
                    id=row["id"],
                    ts=row["ts"],
                    session=row["session"],
                    phase=row["phase"],
                    data=json.loads(row["data"]),
                )
                for row in rows
            ]

    def get_decisions(self, action: Optional[str] = None, limit: int = 100) -> List[LogEntry]:
        """Get decision logs.

        Args:
            action: Optional action filter (create, update, skip, merge)
            limit: Maximum results

        Returns:
            List of decision LogEntry objects
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if action:
                rows = conn.execute(
                    """
                    SELECT * FROM logs
                    WHERE phase = 'decide' AND json_extract(data, '$.action') = ?
                    ORDER BY ts DESC LIMIT ?
                    """,
                    (action, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM logs
                    WHERE phase = 'decide'
                    ORDER BY ts DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

            return [
                LogEntry(
                    id=row["id"],
                    ts=row["ts"],
                    session=row["session"],
                    phase=row["phase"],
                    data=json.loads(row["data"]),
                )
                for row in rows
            ]

    def get_low_confidence(self, threshold: float = 0.7) -> List[LogEntry]:
        """Get decisions below confidence threshold.

        Args:
            threshold: Confidence threshold (decisions below this are returned)

        Returns:
            List of low-confidence LogEntry objects
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM logs
                WHERE phase = 'decide'
                  AND json_extract(data, '$.confidence') IS NOT NULL
                  AND CAST(json_extract(data, '$.confidence') AS REAL) < ?
                ORDER BY CAST(json_extract(data, '$.confidence') AS REAL)
                """,
                (threshold,),
            ).fetchall()

            return [
                LogEntry(
                    id=row["id"],
                    ts=row["ts"],
                    session=row["session"],
                    phase=row["phase"],
                    data=json.loads(row["data"]),
                )
                for row in rows
            ]

    def get_session_summary(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Get summary statistics for a session.

        Args:
            session_id: Session ID (defaults to current session)

        Returns:
            Dictionary with summary statistics
        """
        session_id = session_id or self.session_id

        with sqlite3.connect(self.db_path) as conn:
            # Phase counts
            phase_counts = {}
            for row in conn.execute(
                """
                SELECT phase, COUNT(*) as count
                FROM logs WHERE session = ?
                GROUP BY phase
                """,
                (session_id,),
            ):
                phase_counts[row[0]] = row[1]

            # Action counts (from decide phase)
            action_counts = {}
            for row in conn.execute(
                """
                SELECT json_extract(data, '$.action') as action, COUNT(*) as count
                FROM logs
                WHERE session = ? AND phase = 'decide'
                GROUP BY json_extract(data, '$.action')
                """,
                (session_id,),
            ):
                if row[0]:
                    action_counts[row[0]] = row[1]

            # Error count
            error_count = phase_counts.get("error", 0)

            return {
                "session_id": session_id,
                "phase_counts": phase_counts,
                "action_counts": action_counts,
                "error_count": error_count,
                "total_logs": sum(phase_counts.values()),
            }
