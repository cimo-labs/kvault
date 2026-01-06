"""
Session management for kgraph pipeline.

A session represents a single processing run from data ingestion through
application to the knowledge graph. Sessions enable:
- Progress tracking
- Resume after interruption
- Batch management
"""

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from kgraph.pipeline.audit import log_audit


class SessionState(str, Enum):
    """States a session can be in."""

    CREATED = "created"
    """Session created but not started"""

    EXTRACTING = "extracting"
    """Extracting entities from raw data"""

    RESEARCHING = "researching"
    """Researching existing entities for matches"""

    RECONCILING = "reconciling"
    """Making decisions about entity reconciliation"""

    STAGING = "staging"
    """Staging operations for execution"""

    APPLYING = "applying"
    """Applying operations to knowledge graph"""

    REVIEWING = "reviewing"
    """Waiting for human review"""

    COMPLETED = "completed"
    """Session completed successfully"""

    FAILED = "failed"
    """Session failed with errors"""

    PAUSED = "paused"
    """Session paused for later resumption"""


@dataclass
class BatchInfo:
    """Information about a processing batch."""

    batch_id: str
    """Unique batch identifier"""

    source_file: Optional[str] = None
    """Source file being processed"""

    items_total: int = 0
    """Total items in batch"""

    items_processed: int = 0
    """Items processed so far"""

    entities_extracted: int = 0
    """Entities extracted from items"""

    started_at: Optional[str] = None
    """When batch started"""

    completed_at: Optional[str] = None
    """When batch completed"""

    error: Optional[str] = None
    """Error message if batch failed"""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BatchInfo":
        """Create from dictionary."""
        return cls(**data)


@dataclass
class SessionData:
    """
    Session data that can be serialized/deserialized.

    This is the core state that gets saved to disk.
    """

    session_id: str
    """Unique session identifier"""

    state: str = SessionState.CREATED.value
    """Current session state"""

    created_at: str = ""
    """When session was created"""

    updated_at: str = ""
    """Last update time"""

    config_path: Optional[str] = None
    """Path to configuration file used"""

    kg_path: Optional[str] = None
    """Path to knowledge graph"""

    current_batch_id: Optional[str] = None
    """Currently processing batch"""

    batches: List[Dict[str, Any]] = field(default_factory=list)
    """All batches in this session"""

    total_entities_extracted: int = 0
    """Total entities extracted across all batches"""

    total_operations_staged: int = 0
    """Total operations staged"""

    total_operations_applied: int = 0
    """Total operations applied"""

    total_operations_failed: int = 0
    """Total operations failed"""

    questions_pending: int = 0
    """Questions awaiting review"""

    questions_answered: int = 0
    """Questions answered"""

    error_message: Optional[str] = None
    """Error message if session failed"""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Additional metadata"""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionData":
        """Create from dictionary."""
        return cls(
            session_id=data["session_id"],
            state=data.get("state", SessionState.CREATED.value),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            config_path=data.get("config_path"),
            kg_path=data.get("kg_path"),
            current_batch_id=data.get("current_batch_id"),
            batches=data.get("batches", []),
            total_entities_extracted=data.get("total_entities_extracted", 0),
            total_operations_staged=data.get("total_operations_staged", 0),
            total_operations_applied=data.get("total_operations_applied", 0),
            total_operations_failed=data.get("total_operations_failed", 0),
            questions_pending=data.get("questions_pending", 0),
            questions_answered=data.get("questions_answered", 0),
            error_message=data.get("error_message"),
            metadata=data.get("metadata", {}),
        )


class SessionManager:
    """
    Manages session lifecycle and persistence.

    Sessions are stored as JSON files in a sessions directory.
    """

    def __init__(self, sessions_dir: Path):
        """
        Initialize session manager.

        Args:
            sessions_dir: Directory to store session files
        """
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._current_session: Optional[SessionData] = None

    @property
    def current(self) -> Optional[SessionData]:
        """Get current session data."""
        return self._current_session

    def create_session(
        self,
        config_path: Optional[str] = None,
        kg_path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionData:
        """
        Create a new session.

        Args:
            config_path: Path to configuration file
            kg_path: Path to knowledge graph
            metadata: Additional metadata

        Returns:
            New session data
        """
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        now = datetime.now().isoformat()

        session = SessionData(
            session_id=session_id,
            state=SessionState.CREATED.value,
            created_at=now,
            updated_at=now,
            config_path=config_path,
            kg_path=kg_path,
            metadata=metadata or {},
        )

        self._current_session = session
        self._save_session(session)

        log_audit("session", "created", {"session_id": session_id})

        return session

    def load_session(self, session_id: str) -> Optional[SessionData]:
        """
        Load an existing session.

        Args:
            session_id: Session identifier

        Returns:
            Session data if found, None otherwise
        """
        session_path = self.sessions_dir / f"{session_id}.json"

        if not session_path.exists():
            return None

        with open(session_path) as f:
            data = json.load(f)

        session = SessionData.from_dict(data)
        self._current_session = session

        log_audit("session", "loaded", {"session_id": session_id})

        return session

    def update_state(self, state: SessionState) -> None:
        """
        Update session state.

        Args:
            state: New state
        """
        if not self._current_session:
            raise RuntimeError("No active session")

        old_state = self._current_session.state
        self._current_session.state = state.value
        self._current_session.updated_at = datetime.now().isoformat()

        self._save_session(self._current_session)

        log_audit(
            "session",
            "state_change",
            {
                "session_id": self._current_session.session_id,
                "from": old_state,
                "to": state.value,
            },
        )

    def start_batch(
        self,
        source_file: Optional[str] = None,
        items_total: int = 0,
    ) -> str:
        """
        Start a new batch within the session.

        Args:
            source_file: Optional source file being processed
            items_total: Total items in batch

        Returns:
            Batch ID
        """
        if not self._current_session:
            raise RuntimeError("No active session")

        batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        batch_info = BatchInfo(
            batch_id=batch_id,
            source_file=source_file,
            items_total=items_total,
            started_at=datetime.now().isoformat(),
        )

        self._current_session.batches.append(batch_info.to_dict())
        self._current_session.current_batch_id = batch_id
        self._current_session.updated_at = datetime.now().isoformat()

        self._save_session(self._current_session)

        log_audit(
            "batch",
            "started",
            {
                "session_id": self._current_session.session_id,
                "batch_id": batch_id,
                "items": items_total,
            },
        )

        return batch_id

    def update_batch(
        self,
        batch_id: Optional[str] = None,
        items_processed: Optional[int] = None,
        entities_extracted: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        Update batch progress.

        Args:
            batch_id: Batch to update (defaults to current)
            items_processed: Items processed so far
            entities_extracted: Entities extracted
            error: Error message if failed
        """
        if not self._current_session:
            raise RuntimeError("No active session")

        batch_id = batch_id or self._current_session.current_batch_id
        if not batch_id:
            return

        # Find batch in list
        for batch_dict in self._current_session.batches:
            if batch_dict.get("batch_id") == batch_id:
                if items_processed is not None:
                    batch_dict["items_processed"] = items_processed
                if entities_extracted is not None:
                    batch_dict["entities_extracted"] = entities_extracted
                    self._current_session.total_entities_extracted += entities_extracted
                if error is not None:
                    batch_dict["error"] = error
                break

        self._current_session.updated_at = datetime.now().isoformat()
        self._save_session(self._current_session)

    def complete_batch(self, batch_id: Optional[str] = None) -> None:
        """
        Mark a batch as complete.

        Args:
            batch_id: Batch to complete (defaults to current)
        """
        if not self._current_session:
            raise RuntimeError("No active session")

        batch_id = batch_id or self._current_session.current_batch_id
        if not batch_id:
            return

        for batch_dict in self._current_session.batches:
            if batch_dict.get("batch_id") == batch_id:
                batch_dict["completed_at"] = datetime.now().isoformat()
                break

        if self._current_session.current_batch_id == batch_id:
            self._current_session.current_batch_id = None

        self._current_session.updated_at = datetime.now().isoformat()
        self._save_session(self._current_session)

        log_audit(
            "batch",
            "completed",
            {"session_id": self._current_session.session_id, "batch_id": batch_id},
        )

    def update_stats(
        self,
        operations_staged: Optional[int] = None,
        operations_applied: Optional[int] = None,
        operations_failed: Optional[int] = None,
        questions_pending: Optional[int] = None,
        questions_answered: Optional[int] = None,
    ) -> None:
        """
        Update session statistics.

        Args:
            operations_staged: Operations staged (adds to total)
            operations_applied: Operations applied (adds to total)
            operations_failed: Operations failed (adds to total)
            questions_pending: Set questions pending count
            questions_answered: Set questions answered count
        """
        if not self._current_session:
            raise RuntimeError("No active session")

        if operations_staged is not None:
            self._current_session.total_operations_staged += operations_staged
        if operations_applied is not None:
            self._current_session.total_operations_applied += operations_applied
        if operations_failed is not None:
            self._current_session.total_operations_failed += operations_failed
        if questions_pending is not None:
            self._current_session.questions_pending = questions_pending
        if questions_answered is not None:
            self._current_session.questions_answered = questions_answered

        self._current_session.updated_at = datetime.now().isoformat()
        self._save_session(self._current_session)

    def fail_session(self, error_message: str) -> None:
        """
        Mark session as failed.

        Args:
            error_message: Error message
        """
        if not self._current_session:
            raise RuntimeError("No active session")

        self._current_session.state = SessionState.FAILED.value
        self._current_session.error_message = error_message
        self._current_session.updated_at = datetime.now().isoformat()

        self._save_session(self._current_session)

        log_audit(
            "session",
            "failed",
            {
                "session_id": self._current_session.session_id,
                "error": error_message,
            },
        )

    def complete_session(self) -> None:
        """Mark session as completed."""
        if not self._current_session:
            raise RuntimeError("No active session")

        self._current_session.state = SessionState.COMPLETED.value
        self._current_session.updated_at = datetime.now().isoformat()

        self._save_session(self._current_session)

        log_audit(
            "session",
            "completed",
            {
                "session_id": self._current_session.session_id,
                "entities_extracted": self._current_session.total_entities_extracted,
                "operations_applied": self._current_session.total_operations_applied,
                "operations_failed": self._current_session.total_operations_failed,
            },
        )

    def list_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        List recent sessions.

        Args:
            limit: Maximum sessions to return

        Returns:
            List of session summaries
        """
        sessions = []

        for session_file in sorted(
            self.sessions_dir.glob("session_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]:
            try:
                with open(session_file) as f:
                    data = json.load(f)

                sessions.append({
                    "session_id": data["session_id"],
                    "state": data.get("state"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "batches": len(data.get("batches", [])),
                    "entities_extracted": data.get("total_entities_extracted", 0),
                    "operations_applied": data.get("total_operations_applied", 0),
                })
            except (json.JSONDecodeError, KeyError):
                continue

        return sessions

    def get_resumable_sessions(self) -> List[Dict[str, Any]]:
        """
        Get sessions that can be resumed.

        Returns:
            List of resumable session summaries
        """
        resumable_states = {
            SessionState.PAUSED.value,
            SessionState.REVIEWING.value,
            SessionState.STAGING.value,
            SessionState.EXTRACTING.value,
            SessionState.RESEARCHING.value,
            SessionState.RECONCILING.value,
        }

        return [s for s in self.list_sessions() if s.get("state") in resumable_states]

    def _save_session(self, session: SessionData) -> None:
        """Save session to disk."""
        session_path = self.sessions_dir / f"{session.session_id}.json"

        with open(session_path, "w") as f:
            json.dump(session.to_dict(), f, indent=2)
