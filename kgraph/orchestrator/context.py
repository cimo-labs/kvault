"""
WorkflowContext - Shared state across all workflow steps.

This dataclass holds:
- Inputs: raw_input or new_info, meta_context, last_k_updates
- Step outputs: research_results, action_plan, executed_actions, etc.
- Refactor state: probability and results

Supports two input modes:
- Legacy (entity-centric): new_info dict with name, type, email, source, content
- New (hierarchy-based): raw_input with content and source, agent reasons about what to do
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from kgraph import LogEntry, MatchCandidate


# ============================================================================
# Hierarchy-Based Input Types (New)
# ============================================================================


@dataclass
class HierarchyInput:
    """Raw input for hierarchy-based processing.

    The agent receives unstructured content and reasons about what
    changes the knowledge hierarchy needs.
    """

    content: str
    """Raw content to process (any format: text, notes, transcript, etc.)"""

    source: str
    """Source identifier (e.g., 'imessage:2024-01-15', 'email:xyz', 'manual')"""

    hints: Optional[Dict[str, Any]] = None
    """Optional extraction hints (e.g., {'likely_people': ['Alice', 'Bob']})"""


@dataclass
class PlannedAction:
    """A single action in the execution plan.

    Produced by DECIDE step, executed by EXECUTE step.
    """

    action_type: Literal["create", "update", "delete", "move", "skip"]
    """Type of action to perform."""

    path: str
    """Target hierarchy path (e.g., 'people/contacts/john_doe')."""

    reasoning: str = ""
    """Explanation of why this action is needed."""

    confidence: float = 1.0
    """Confidence score (0.0-1.0)."""

    content: Optional[Dict[str, Any]] = None
    """Content to write (for create/update). Keys: 'summary', 'meta'."""

    target_path: Optional[str] = None
    """Destination path (for move operations)."""


@dataclass
class ActionPlan:
    """Output from DECIDE step: 0..N planned changes to the hierarchy.

    An empty plan (no actions) means the input doesn't warrant any changes.
    """

    actions: List[PlannedAction]
    """List of actions to execute."""

    overall_reasoning: str
    """High-level explanation of the plan."""

    @property
    def affected_paths(self) -> List[str]:
        """Paths that will be modified by this plan."""
        return [a.path for a in self.actions if a.action_type != "skip"]

    @property
    def has_creates(self) -> bool:
        """True if plan includes any create actions."""
        return any(a.action_type == "create" for a in self.actions)


@dataclass
class OrchestratorConfig:
    """Configuration for the headless orchestrator."""

    kg_root: Path
    meta_context_path: Optional[Path] = None  # CLAUDE.md path
    last_k_updates: int = 10  # Number of recent updates to load
    refactor_probability: float = 0.1  # Bernoulli(p) for refactor step
    max_retries: int = 3  # Retries per step
    timeout_seconds: int = 300  # Per-step timeout (5 minutes)

    # Permission mode for Claude CLI
    permission_mode: str = "bypassPermissions"  # Required for headless file writes

    # Whether to skip all permission checks (use with caution)
    dangerously_skip_permissions: bool = True  # Required for headless execution

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

    Supports two input modes:
    - Legacy: new_info dict (entity-centric, one entity per call)
    - New: raw_input HierarchyInput (hierarchy-based, 0..N actions per call)
    """

    # -------------------------
    # Inputs (set at start)
    # -------------------------

    # Legacy input (entity-centric) - optional for backward compatibility
    new_info: Optional[Dict[str, Any]] = None
    """Legacy: Information to process into the knowledge graph.

    Expected keys:
    - name: str - Entity name
    - type: str - Entity type (person, org, project, etc.)
    - email: Optional[str] - Email address for matching
    - source: str - Source identifier (e.g., "email:12345", "manual:2024-01-01")
    - content: Optional[str] - Additional context/content
    """

    # New input (hierarchy-based)
    raw_input: Optional[HierarchyInput] = None
    """New: Raw content for hierarchy-based processing."""

    # Context provided to agent
    meta_context: str = ""
    """CLAUDE.md content for workflow instructions."""

    root_summary: str = ""
    """Root _summary.md content (executive view of KB)."""

    hierarchy_tree: str = ""
    """KB structure tree (directory hierarchy)."""

    last_k_updates: List[LogEntry] = field(default_factory=list)
    """Recent k updates from logs.db for context."""

    @property
    def is_hierarchy_mode(self) -> bool:
        """True if using hierarchy-based input, False for legacy entity mode."""
        return self.raw_input is not None

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

    # Legacy (entity-centric)
    decision: Optional[Literal["create", "update", "skip", "merge"]] = None
    """Legacy: Action to take based on research results."""

    decision_confidence: Optional[float] = None
    """Legacy: Confidence score for the decision (0.0-1.0)."""

    decision_reasoning: Optional[str] = None
    """Legacy: Explanation of why this decision was made."""

    target_path: Optional[str] = None
    """Legacy: Target entity path for update/merge, or new path for create."""

    # New (hierarchy-based)
    action_plan: Optional[ActionPlan] = None
    """New: Plan with 0..N actions to execute."""

    # -------------------------
    # Step 3: EXECUTE outputs (was WRITE)
    # -------------------------

    # Legacy (entity-centric)
    entity_path: Optional[str] = None
    """Legacy: Final entity path after write operation."""

    meta_written: Optional[Dict[str, Any]] = None
    """Legacy: Metadata that was written to _meta.json."""

    summary_written: Optional[str] = None
    """Legacy: Summary content that was written to _summary.md."""

    entity_created: bool = False
    """Legacy: True if a new entity was created (vs updated)."""

    # New (hierarchy-based)
    executed_actions: List[Dict[str, Any]] = field(default_factory=list)
    """New: Record of each action executed from the plan."""

    created_paths: List[str] = field(default_factory=list)
    """New: Paths where new entities were created."""

    updated_paths: List[str] = field(default_factory=list)
    """New: Paths where existing entities were updated."""

    deleted_paths: List[str] = field(default_factory=list)
    """New: Paths where entities were deleted."""

    moved_paths: List[Dict[str, str]] = field(default_factory=list)
    """New: Move operations with 'source' and 'target' keys."""

    # -------------------------
    # Step 4: PROPAGATE outputs
    # -------------------------

    # New (hierarchy-based) - computed from created_paths + updated_paths
    propagation_roots: List[str] = field(default_factory=list)
    """New: Paths that triggered propagation (union of created + updated)."""

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
        result = {
            "session_id": self.session_id,
            "is_hierarchy_mode": self.is_hierarchy_mode,
            "propagated_paths": self.propagated_paths,
            "index_rebuilt": self.index_rebuilt,
            "should_refactor": self.should_refactor,
        }

        if self.is_hierarchy_mode:
            # Hierarchy-based output
            result.update({
                "raw_input": {
                    "content": self.raw_input.content[:200] + "..." if len(self.raw_input.content) > 200 else self.raw_input.content,
                    "source": self.raw_input.source,
                } if self.raw_input else None,
                "action_plan": {
                    "actions": [
                        {"action_type": a.action_type, "path": a.path}
                        for a in self.action_plan.actions
                    ],
                    "overall_reasoning": self.action_plan.overall_reasoning,
                } if self.action_plan else None,
                "executed_actions": self.executed_actions,
                "created_paths": self.created_paths,
                "updated_paths": self.updated_paths,
                "deleted_paths": self.deleted_paths,
                "moved_paths": self.moved_paths,
            })
        else:
            # Legacy entity-centric output
            result.update({
                "new_info": self.new_info,
                "decision": self.decision,
                "decision_confidence": self.decision_confidence,
                "entity_path": self.entity_path,
                "entity_created": self.entity_created,
            })

        return result
