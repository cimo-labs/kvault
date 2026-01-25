"""Session state management for kgraph MCP server.

Tracks workflow progress and provides advisory validation.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class WorkflowStep(Enum):
    """Workflow steps in order."""
    INIT = "init"
    RESEARCH = "research"
    DECIDE = "decide"
    EXECUTE = "execute"
    PROPAGATE = "propagate"
    LOG = "log"
    REBUILD = "rebuild"
    COMPLETE = "complete"


# Valid transitions
VALID_TRANSITIONS = {
    WorkflowStep.INIT: [WorkflowStep.RESEARCH],
    WorkflowStep.RESEARCH: [WorkflowStep.DECIDE],
    WorkflowStep.DECIDE: [WorkflowStep.EXECUTE, WorkflowStep.COMPLETE],  # COMPLETE if skip
    WorkflowStep.EXECUTE: [WorkflowStep.PROPAGATE],
    WorkflowStep.PROPAGATE: [WorkflowStep.LOG],
    WorkflowStep.LOG: [WorkflowStep.REBUILD],
    WorkflowStep.REBUILD: [WorkflowStep.COMPLETE],
    WorkflowStep.COMPLETE: [],
}


@dataclass
class SessionState:
    """State for a single workflow session."""

    session_id: str
    kg_root: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    current_step: WorkflowStep = WorkflowStep.INIT

    # Research results
    research_matches: List[Dict[str, Any]] = field(default_factory=list)
    research_intent: Optional[str] = None

    # Decision
    action_plan: List[Dict[str, Any]] = field(default_factory=list)
    decision_reasoning: Optional[str] = None

    # Execution results
    created_paths: List[str] = field(default_factory=list)
    updated_paths: List[str] = field(default_factory=list)
    deleted_paths: List[str] = field(default_factory=list)
    moved_paths: List[Dict[str, str]] = field(default_factory=list)

    # Propagation
    propagated_paths: List[str] = field(default_factory=list)

    # Journal
    journal_path: Optional[str] = None

    # Index
    index_rebuilt: bool = False
    entity_count: Optional[int] = None

    def can_transition_to(self, target: WorkflowStep) -> bool:
        """Check if transition to target step is valid."""
        return target in VALID_TRANSITIONS.get(self.current_step, [])

    def transition(self, target: WorkflowStep) -> bool:
        """Attempt to transition to target step.

        Returns True if successful, False if invalid transition.
        """
        if self.can_transition_to(target):
            self.current_step = target
            return True
        return False

    def force_transition(self, target: WorkflowStep) -> None:
        """Force transition to target step (for skipped steps).

        Use when workflow is out of order but we need to continue.
        """
        self.current_step = target

    def record_research(self, matches: List[Dict[str, Any]], intent: Optional[str] = None) -> None:
        """Record research results and transition to DECIDE step.

        Args:
            matches: List of match dictionaries from research
            intent: Optional intent description (e.g., "create", "update")
        """
        self.research_matches = matches
        self.research_intent = intent
        self.transition(WorkflowStep.DECIDE)

    def record_decision(self, action_plan: List[Dict[str, Any]], reasoning: Optional[str] = None) -> None:
        """Record decision and transition to EXECUTE step.

        Args:
            action_plan: List of planned actions
            reasoning: Optional reasoning for the decision
        """
        self.action_plan = action_plan
        self.decision_reasoning = reasoning
        self.transition(WorkflowStep.EXECUTE)

    def record_execution(
        self,
        created: Optional[List[str]] = None,
        updated: Optional[List[str]] = None,
        deleted: Optional[List[str]] = None,
        moved: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        """Record execution results and transition to PROPAGATE step.

        Args:
            created: List of created entity paths
            updated: List of updated entity paths
            deleted: List of deleted entity paths
            moved: List of move operations (source, target dicts)
        """
        if created:
            self.created_paths.extend(created)
        if updated:
            self.updated_paths.extend(updated)
        if deleted:
            self.deleted_paths.extend(deleted)
        if moved:
            self.moved_paths.extend(moved)
        self.transition(WorkflowStep.PROPAGATE)

    def record_propagation(self, propagated: List[str]) -> None:
        """Record propagated paths and transition to LOG step.

        Args:
            propagated: List of propagated summary paths
        """
        self.propagated_paths.extend(propagated)
        self.transition(WorkflowStep.LOG)

    def record_journal(self, journal_path: str) -> None:
        """Record journal entry and transition to REBUILD step.

        Args:
            journal_path: Path to the journal file
        """
        self.journal_path = journal_path
        self.transition(WorkflowStep.REBUILD)

    def record_rebuild(self, entity_count: int) -> None:
        """Record index rebuild and transition to COMPLETE step.

        Args:
            entity_count: Number of entities in the rebuilt index
        """
        self.index_rebuilt = True
        self.entity_count = entity_count
        self.transition(WorkflowStep.COMPLETE)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "session_id": self.session_id,
            "kg_root": self.kg_root,
            "created_at": self.created_at,
            "current_step": self.current_step.value,
            "research_matches_count": len(self.research_matches),
            "action_plan_count": len(self.action_plan),
            "created_paths": self.created_paths,
            "updated_paths": self.updated_paths,
            "deleted_paths": self.deleted_paths,
            "moved_paths": self.moved_paths,
            "propagated_paths": self.propagated_paths,
            "journal_path": self.journal_path,
            "index_rebuilt": self.index_rebuilt,
            "entity_count": self.entity_count,
        }


class SessionManager:
    """Manages workflow sessions."""

    def __init__(self):
        self._sessions: Dict[str, SessionState] = {}

    def create_session(self, kg_root: str) -> SessionState:
        """Create a new workflow session."""
        session_id = f"mcp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        session = SessionState(session_id=session_id, kg_root=kg_root)
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[SessionState]:
        """Get session by ID."""
        return self._sessions.get(session_id)

    def get_or_create_session(self, kg_root: str, session_id: Optional[str] = None) -> SessionState:
        """Get existing session or create new one."""
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        return self.create_session(kg_root)

    def remove_session(self, session_id: str) -> bool:
        """Remove a session."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all active sessions."""
        return [
            {
                "session_id": s.session_id,
                "kg_root": s.kg_root,
                "current_step": s.current_step.value,
                "created_at": s.created_at,
            }
            for s in self._sessions.values()
        ]


# Global session manager instance
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """Get or create the global session manager."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
