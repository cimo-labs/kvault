"""
WorkflowContext - Shared state across all workflow steps.

This dataclass holds:
- Inputs: new_info, meta_context, last_k_updates
- Step outputs: research_results, decision, entity_path, etc.
- Refactor state: probability and results
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from kgraph import LogEntry, MatchCandidate


@dataclass
class OrchestratorConfig:
    """Configuration for the headless orchestrator."""

    kg_root: Path
    meta_context_path: Optional[Path] = None  # CLAUDE.md path
    last_k_updates: int = 10  # Number of recent updates to load
    refactor_probability: float = 0.1  # Bernoulli(p) for refactor step
    max_retries: int = 3  # Retries per step
    timeout_seconds: int = 300  # Per-step timeout (5 minutes)

    # Permission mode for Claude SDK
    permission_mode: str = "acceptEdits"

    # Tools allowed during orchestration
    allowed_tools: List[str] = field(
        default_factory=lambda: [
            "Read",
            "Write",
            "Edit",
            "Grep",
            "Glob",
            "Bash",
        ]
    )


@dataclass
class WorkflowContext:
    """Shared state across all workflow steps.

    Populated incrementally as the workflow progresses through steps.
    Used by the state machine to check prerequisites and by the
    enforcer to validate step outputs.
    """

    # -------------------------
    # Inputs (set at start)
    # -------------------------

    new_info: Dict[str, Any]
    """Information to process into the knowledge graph.

    Expected keys:
    - name: str - Entity name
    - type: str - Entity type (person, org, project, etc.)
    - email: Optional[str] - Email address for matching
    - source: str - Source identifier (e.g., "email:12345", "manual:2024-01-01")
    - content: Optional[str] - Additional context/content
    """

    meta_context: str = ""
    """CLAUDE.md content for workflow instructions."""

    last_k_updates: List[LogEntry] = field(default_factory=list)
    """Recent k updates from logs.db for context."""

    # -------------------------
    # Step 1: RESEARCH outputs
    # -------------------------

    research_results: Optional[List[MatchCandidate]] = None
    """Match candidates from EntityResearcher."""

    existing_entity_path: Optional[str] = None
    """Path to best matching existing entity, if any."""

    # -------------------------
    # Step 2: DECIDE outputs
    # -------------------------

    decision: Optional[Literal["create", "update", "skip", "merge"]] = None
    """Action to take based on research results."""

    decision_confidence: Optional[float] = None
    """Confidence score for the decision (0.0-1.0)."""

    decision_reasoning: Optional[str] = None
    """Explanation of why this decision was made."""

    target_path: Optional[str] = None
    """Target entity path for update/merge, or new path for create."""

    # -------------------------
    # Step 3: WRITE outputs
    # -------------------------

    entity_path: Optional[str] = None
    """Final entity path after write operation."""

    meta_written: Optional[Dict[str, Any]] = None
    """Metadata that was written to _meta.json."""

    summary_written: Optional[str] = None
    """Summary content that was written to _summary.md."""

    entity_created: bool = False
    """True if a new entity was created (vs updated)."""

    # -------------------------
    # Step 4: PROPAGATE outputs
    # -------------------------

    propagated_paths: Optional[List[str]] = None
    """List of ancestor paths whose summaries were updated."""

    # -------------------------
    # Step 5: LOG outputs
    # -------------------------

    log_entry_id: Optional[int] = None
    """ID of the log entry in logs.db."""

    journal_entry_path: Optional[str] = None
    """Path to journal entry (e.g., journal/2024-01/log.md)."""

    # -------------------------
    # Step 6: REBUILD outputs
    # -------------------------

    index_count: Optional[int] = None
    """Number of entities in index after rebuild."""

    index_rebuilt: bool = False
    """True if index was rebuilt."""

    # -------------------------
    # Refactor (stochastic)
    # -------------------------

    refactor_probability: float = 0.1
    """Probability of triggering refactor step."""

    should_refactor: bool = False
    """True if refactor was triggered (sampled at end of workflow)."""

    refactor_opportunities: Optional[List[Dict[str, Any]]] = None
    """Identified refactor opportunities."""

    refactor_results: Optional[List[Dict[str, Any]]] = None
    """Results of executed refactors."""

    # -------------------------
    # Session tracking
    # -------------------------

    session_id: Optional[str] = None
    """Observability session ID for this workflow run."""

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary for serialization."""
        return {
            "new_info": self.new_info,
            "decision": self.decision,
            "decision_confidence": self.decision_confidence,
            "entity_path": self.entity_path,
            "entity_created": self.entity_created,
            "propagated_paths": self.propagated_paths,
            "index_rebuilt": self.index_rebuilt,
            "should_refactor": self.should_refactor,
            "session_id": self.session_id,
        }
