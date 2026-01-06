"""
Checkpoint management for pipeline resume/recovery.

Checkpoints capture the complete state needed to resume processing
after an interruption. They are saved at key points in the pipeline
and enable picking up exactly where processing left off.
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from kgraph.pipeline.audit import log_audit
from kgraph.pipeline.session import SessionState


@dataclass
class CheckpointData:
    """
    Checkpoint data for resume/recovery.

    Captures everything needed to resume processing from a specific point.
    """

    checkpoint_id: str
    """Unique checkpoint identifier"""

    session_id: str
    """Session this checkpoint belongs to"""

    batch_id: Optional[str] = None
    """Batch being processed (if any)"""

    phase: str = ""
    """Current phase: extract, research, reconcile, stage, apply"""

    state: str = SessionState.CREATED.value
    """Session state at checkpoint"""

    created_at: str = ""
    """When checkpoint was created"""

    # Phase-specific data
    items_remaining: List[Dict[str, Any]] = field(default_factory=list)
    """Unprocessed items (for extraction phase)"""

    entities_pending: List[Dict[str, Any]] = field(default_factory=list)
    """Entities awaiting research/reconciliation"""

    operations_pending: List[int] = field(default_factory=list)
    """Operation IDs awaiting execution"""

    # Counters
    items_processed: int = 0
    """Items processed so far"""

    entities_extracted: int = 0
    """Entities extracted so far"""

    operations_staged: int = 0
    """Operations staged so far"""

    # Context
    context_data: Dict[str, Any] = field(default_factory=dict)
    """Additional context for resumption"""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckpointData":
        """Create from dictionary."""
        return cls(
            checkpoint_id=data["checkpoint_id"],
            session_id=data["session_id"],
            batch_id=data.get("batch_id"),
            phase=data.get("phase", ""),
            state=data.get("state", SessionState.CREATED.value),
            created_at=data.get("created_at", ""),
            items_remaining=data.get("items_remaining", []),
            entities_pending=data.get("entities_pending", []),
            operations_pending=data.get("operations_pending", []),
            items_processed=data.get("items_processed", 0),
            entities_extracted=data.get("entities_extracted", 0),
            operations_staged=data.get("operations_staged", 0),
            context_data=data.get("context_data", {}),
        )


class CheckpointManager:
    """
    Manages checkpoints for pipeline resume/recovery.

    Checkpoints are saved at key phase transitions:
    - After extraction batch completes
    - After research phase completes
    - After reconciliation phase completes
    - After staging phase completes
    - Periodically during long-running phases
    """

    def __init__(self, checkpoints_dir: Path):
        """
        Initialize checkpoint manager.

        Args:
            checkpoints_dir: Directory to store checkpoint files
        """
        self.checkpoints_dir = Path(checkpoints_dir)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

    def create_checkpoint(
        self,
        session_id: str,
        phase: str,
        state: SessionState,
        batch_id: Optional[str] = None,
        items_remaining: Optional[List[Dict[str, Any]]] = None,
        entities_pending: Optional[List[Dict[str, Any]]] = None,
        operations_pending: Optional[List[int]] = None,
        items_processed: int = 0,
        entities_extracted: int = 0,
        operations_staged: int = 0,
        context_data: Optional[Dict[str, Any]] = None,
    ) -> CheckpointData:
        """
        Create a checkpoint.

        Args:
            session_id: Session identifier
            phase: Current phase name
            state: Current session state
            batch_id: Current batch (if any)
            items_remaining: Unprocessed items
            entities_pending: Entities awaiting processing
            operations_pending: Operation IDs awaiting execution
            items_processed: Items processed count
            entities_extracted: Entities extracted count
            operations_staged: Operations staged count
            context_data: Additional context

        Returns:
            Checkpoint data
        """
        checkpoint_id = f"checkpoint_{session_id}_{phase}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        checkpoint = CheckpointData(
            checkpoint_id=checkpoint_id,
            session_id=session_id,
            batch_id=batch_id,
            phase=phase,
            state=state.value,
            created_at=datetime.now().isoformat(),
            items_remaining=items_remaining or [],
            entities_pending=entities_pending or [],
            operations_pending=operations_pending or [],
            items_processed=items_processed,
            entities_extracted=entities_extracted,
            operations_staged=operations_staged,
            context_data=context_data or {},
        )

        self._save_checkpoint(checkpoint)

        log_audit(
            "checkpoint",
            "created",
            {
                "checkpoint_id": checkpoint_id,
                "session_id": session_id,
                "phase": phase,
                "items_remaining": len(items_remaining or []),
                "entities_pending": len(entities_pending or []),
            },
        )

        return checkpoint

    def get_latest_checkpoint(self, session_id: str) -> Optional[CheckpointData]:
        """
        Get the latest checkpoint for a session.

        Args:
            session_id: Session identifier

        Returns:
            Latest checkpoint or None
        """
        checkpoints = self._list_checkpoints_for_session(session_id)

        if not checkpoints:
            return None

        # Sort by creation time, get latest
        latest = sorted(checkpoints, key=lambda c: c.created_at, reverse=True)[0]
        return latest

    def get_checkpoint_for_phase(
        self,
        session_id: str,
        phase: str,
    ) -> Optional[CheckpointData]:
        """
        Get the latest checkpoint for a specific phase.

        Args:
            session_id: Session identifier
            phase: Phase name

        Returns:
            Latest checkpoint for phase or None
        """
        checkpoints = self._list_checkpoints_for_session(session_id)

        phase_checkpoints = [c for c in checkpoints if c.phase == phase]

        if not phase_checkpoints:
            return None

        return sorted(phase_checkpoints, key=lambda c: c.created_at, reverse=True)[0]

    def load_checkpoint(self, checkpoint_id: str) -> Optional[CheckpointData]:
        """
        Load a specific checkpoint by ID.

        Args:
            checkpoint_id: Checkpoint identifier

        Returns:
            Checkpoint data or None
        """
        checkpoint_path = self.checkpoints_dir / f"{checkpoint_id}.json"

        if not checkpoint_path.exists():
            return None

        with open(checkpoint_path) as f:
            data = json.load(f)

        return CheckpointData.from_dict(data)

    def delete_checkpoint(self, checkpoint_id: str) -> bool:
        """
        Delete a checkpoint.

        Args:
            checkpoint_id: Checkpoint identifier

        Returns:
            True if deleted, False if not found
        """
        checkpoint_path = self.checkpoints_dir / f"{checkpoint_id}.json"

        if checkpoint_path.exists():
            checkpoint_path.unlink()
            log_audit("checkpoint", "deleted", {"checkpoint_id": checkpoint_id})
            return True

        return False

    def cleanup_old_checkpoints(
        self,
        session_id: str,
        keep_latest: int = 3,
    ) -> int:
        """
        Clean up old checkpoints for a session.

        Keeps the latest N checkpoints and deletes older ones.

        Args:
            session_id: Session identifier
            keep_latest: Number of recent checkpoints to keep

        Returns:
            Number of checkpoints deleted
        """
        checkpoints = self._list_checkpoints_for_session(session_id)

        if len(checkpoints) <= keep_latest:
            return 0

        # Sort by creation time, newest first
        sorted_checkpoints = sorted(
            checkpoints, key=lambda c: c.created_at, reverse=True
        )

        # Delete older ones
        deleted = 0
        for checkpoint in sorted_checkpoints[keep_latest:]:
            if self.delete_checkpoint(checkpoint.checkpoint_id):
                deleted += 1

        return deleted

    def cleanup_completed_sessions(self, sessions_dir: Path) -> int:
        """
        Clean up checkpoints for completed sessions.

        Reads session files to find completed sessions and removes
        their checkpoints.

        Args:
            sessions_dir: Directory containing session files

        Returns:
            Number of checkpoints deleted
        """
        deleted = 0

        for session_file in sessions_dir.glob("session_*.json"):
            try:
                with open(session_file) as f:
                    session_data = json.load(f)

                state = session_data.get("state")
                if state == SessionState.COMPLETED.value:
                    session_id = session_data["session_id"]
                    deleted += self._delete_all_checkpoints_for_session(session_id)

            except (json.JSONDecodeError, KeyError):
                continue

        return deleted

    def _save_checkpoint(self, checkpoint: CheckpointData) -> None:
        """Save checkpoint to disk."""
        checkpoint_path = self.checkpoints_dir / f"{checkpoint.checkpoint_id}.json"

        with open(checkpoint_path, "w") as f:
            json.dump(checkpoint.to_dict(), f, indent=2)

    def _list_checkpoints_for_session(self, session_id: str) -> List[CheckpointData]:
        """List all checkpoints for a session."""
        checkpoints = []

        for checkpoint_file in self.checkpoints_dir.glob(f"checkpoint_{session_id}_*.json"):
            try:
                with open(checkpoint_file) as f:
                    data = json.load(f)
                checkpoints.append(CheckpointData.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue

        return checkpoints

    def _delete_all_checkpoints_for_session(self, session_id: str) -> int:
        """Delete all checkpoints for a session."""
        deleted = 0

        for checkpoint_file in self.checkpoints_dir.glob(f"checkpoint_{session_id}_*.json"):
            checkpoint_file.unlink()
            deleted += 1

        if deleted > 0:
            log_audit(
                "checkpoint",
                "cleanup",
                {"session_id": session_id, "deleted": deleted},
            )

        return deleted


class ResumableOperation:
    """
    Context manager for resumable operations.

    Automatically creates checkpoints at the start and can be used
    to resume from the last checkpoint if interrupted.

    Example:
        with ResumableOperation(checkpoint_mgr, session_id, "extract") as op:
            if op.resumed:
                # Resume from checkpoint
                items = op.checkpoint.items_remaining
            else:
                # Start fresh
                items = load_all_items()

            for item in items:
                process(item)
                op.update(items_remaining=items[1:])  # Save progress
    """

    def __init__(
        self,
        checkpoint_manager: CheckpointManager,
        session_id: str,
        phase: str,
        batch_id: Optional[str] = None,
    ):
        """
        Initialize resumable operation.

        Args:
            checkpoint_manager: Checkpoint manager
            session_id: Session identifier
            phase: Phase name
            batch_id: Optional batch identifier
        """
        self.checkpoint_manager = checkpoint_manager
        self.session_id = session_id
        self.phase = phase
        self.batch_id = batch_id

        self.checkpoint: Optional[CheckpointData] = None
        self.resumed: bool = False
        self._current_state: SessionState = SessionState.CREATED

    def __enter__(self) -> "ResumableOperation":
        """Enter context, potentially resuming from checkpoint."""
        # Check for existing checkpoint
        self.checkpoint = self.checkpoint_manager.get_checkpoint_for_phase(
            self.session_id, self.phase
        )

        if self.checkpoint and (
            self.checkpoint.items_remaining
            or self.checkpoint.entities_pending
            or self.checkpoint.operations_pending
        ):
            self.resumed = True
            self._current_state = SessionState(self.checkpoint.state)

            log_audit(
                "checkpoint",
                "resumed",
                {
                    "checkpoint_id": self.checkpoint.checkpoint_id,
                    "phase": self.phase,
                },
            )

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context, cleaning up checkpoint if successful."""
        if exc_type is None:
            # Success - clean up checkpoint
            if self.checkpoint:
                self.checkpoint_manager.delete_checkpoint(self.checkpoint.checkpoint_id)
        else:
            # Failure - keep checkpoint for recovery
            log_audit(
                "checkpoint",
                "preserved_on_error",
                {
                    "session_id": self.session_id,
                    "phase": self.phase,
                    "error": str(exc_val),
                },
            )

    def update(
        self,
        state: Optional[SessionState] = None,
        items_remaining: Optional[List[Dict[str, Any]]] = None,
        entities_pending: Optional[List[Dict[str, Any]]] = None,
        operations_pending: Optional[List[int]] = None,
        items_processed: Optional[int] = None,
        entities_extracted: Optional[int] = None,
        operations_staged: Optional[int] = None,
        context_data: Optional[Dict[str, Any]] = None,
    ) -> CheckpointData:
        """
        Update checkpoint with current progress.

        Args:
            state: New state (optional)
            items_remaining: Updated remaining items
            entities_pending: Updated pending entities
            operations_pending: Updated pending operations
            items_processed: Updated items processed count
            entities_extracted: Updated entities extracted count
            operations_staged: Updated operations staged count
            context_data: Updated context data

        Returns:
            Updated checkpoint
        """
        if state:
            self._current_state = state

        # Build new checkpoint with updated data
        new_checkpoint = self.checkpoint_manager.create_checkpoint(
            session_id=self.session_id,
            phase=self.phase,
            state=self._current_state,
            batch_id=self.batch_id,
            items_remaining=items_remaining if items_remaining is not None else (
                self.checkpoint.items_remaining if self.checkpoint else []
            ),
            entities_pending=entities_pending if entities_pending is not None else (
                self.checkpoint.entities_pending if self.checkpoint else []
            ),
            operations_pending=operations_pending if operations_pending is not None else (
                self.checkpoint.operations_pending if self.checkpoint else []
            ),
            items_processed=items_processed if items_processed is not None else (
                self.checkpoint.items_processed if self.checkpoint else 0
            ),
            entities_extracted=entities_extracted if entities_extracted is not None else (
                self.checkpoint.entities_extracted if self.checkpoint else 0
            ),
            operations_staged=operations_staged if operations_staged is not None else (
                self.checkpoint.operations_staged if self.checkpoint else 0
            ),
            context_data=context_data if context_data is not None else (
                self.checkpoint.context_data if self.checkpoint else {}
            ),
        )

        # Delete old checkpoint
        if self.checkpoint:
            self.checkpoint_manager.delete_checkpoint(self.checkpoint.checkpoint_id)

        self.checkpoint = new_checkpoint
        return new_checkpoint
