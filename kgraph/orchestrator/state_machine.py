"""
WorkflowStateMachine - Enforces mandatory 6-step workflow sequence.

States:
    READY → RESEARCH → DECIDE → WRITE → PROPAGATE → LOG → REBUILD
                                                           ↓
                                                   REFACTOR_CHECK
                                                      ↓      ↓
                                              EXEC_REFACTOR  COMPLETE
                                                      ↓
                                                   COMPLETE

Transitions are only allowed when prerequisites are met.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple

from kgraph.orchestrator.context import WorkflowContext


class WorkflowState(Enum):
    """States in the 6-step workflow."""

    READY = auto()
    RESEARCH = auto()
    DECIDE = auto()
    WRITE = auto()
    PROPAGATE = auto()
    LOG = auto()
    REBUILD = auto()
    REFACTOR_CHECK = auto()
    EXEC_REFACTOR = auto()
    COMPLETE = auto()
    ERROR = auto()


@dataclass
class ValidationResult:
    """Result of validating a step's output."""

    is_valid: bool
    message: str
    extracted_data: Optional[Dict[str, Any]] = None


# Type alias for prerequisite functions
Prerequisite = Callable[[WorkflowContext], bool]


class WorkflowStateMachine:
    """State machine enforcing mandatory workflow sequence.

    Transitions are only allowed when prerequisites are met.
    Each step must complete successfully before the next can begin.

    Usage:
        context = WorkflowContext(new_info={...})
        sm = WorkflowStateMachine(context)

        # Check if we can proceed to next step
        if sm.can_transition_to(WorkflowState.RESEARCH):
            # ... execute research step ...
            sm.store_output("RESEARCH", {"matches": [...]})
            sm.transition("RESEARCH")

        # Or use step names directly
        sm.transition("DECIDE")  # Will fail if RESEARCH not complete
    """

    # Define valid transitions and their prerequisites
    # Format: {from_state: {to_state: prerequisite_function}}
    TRANSITIONS: Dict[WorkflowState, Dict[WorkflowState, Prerequisite]] = {
        WorkflowState.READY: {
            WorkflowState.RESEARCH: lambda ctx: ctx.new_info is not None,
        },
        WorkflowState.RESEARCH: {
            WorkflowState.DECIDE: lambda ctx: ctx.research_results is not None,
        },
        WorkflowState.DECIDE: {
            # Can proceed to WRITE if decision requires it
            WorkflowState.WRITE: lambda ctx: ctx.decision in ("create", "update", "merge"),
            # Can skip to LOG if decision is "skip"
            WorkflowState.LOG: lambda ctx: ctx.decision == "skip",
        },
        WorkflowState.WRITE: {
            WorkflowState.PROPAGATE: lambda ctx: ctx.entity_path is not None,
        },
        WorkflowState.PROPAGATE: {
            WorkflowState.LOG: lambda ctx: ctx.propagated_paths is not None,
        },
        WorkflowState.LOG: {
            # REBUILD only if new entity was created
            WorkflowState.REBUILD: lambda ctx: ctx.entity_created,
            # Skip REBUILD if just updating
            WorkflowState.REFACTOR_CHECK: lambda ctx: not ctx.entity_created,
        },
        WorkflowState.REBUILD: {
            WorkflowState.REFACTOR_CHECK: lambda ctx: True,
        },
        WorkflowState.REFACTOR_CHECK: {
            WorkflowState.EXEC_REFACTOR: lambda ctx: ctx.should_refactor,
            WorkflowState.COMPLETE: lambda ctx: not ctx.should_refactor,
        },
        WorkflowState.EXEC_REFACTOR: {
            WorkflowState.COMPLETE: lambda ctx: True,
        },
    }

    # Map step names to states
    STEP_TO_STATE: Dict[str, WorkflowState] = {
        "RESEARCH": WorkflowState.RESEARCH,
        "DECIDE": WorkflowState.DECIDE,
        "WRITE": WorkflowState.WRITE,
        "PROPAGATE": WorkflowState.PROPAGATE,
        "LOG": WorkflowState.LOG,
        "REBUILD": WorkflowState.REBUILD,
        "REFACTOR_CHECK": WorkflowState.REFACTOR_CHECK,
        "EXEC_REFACTOR": WorkflowState.EXEC_REFACTOR,
        "COMPLETE": WorkflowState.COMPLETE,
    }

    def __init__(self, context: WorkflowContext):
        """Initialize state machine with context.

        Args:
            context: WorkflowContext to track state
        """
        self.context = context
        self.current_state = WorkflowState.READY
        self.history: List[Tuple[WorkflowState, str]] = []

    def can_transition_to(self, target: WorkflowState) -> bool:
        """Check if transition to target state is allowed.

        Args:
            target: Target state to transition to

        Returns:
            True if transition is allowed, False otherwise
        """
        if self.current_state not in self.TRANSITIONS:
            return False

        valid_targets = self.TRANSITIONS[self.current_state]

        if target not in valid_targets:
            return False

        # Check prerequisite
        prerequisite = valid_targets[target]
        return prerequisite(self.context)

    def get_valid_transitions(self) -> List[WorkflowState]:
        """Get list of states we can currently transition to.

        Returns:
            List of valid target states
        """
        if self.current_state not in self.TRANSITIONS:
            return []

        valid = []
        for target, prereq in self.TRANSITIONS[self.current_state].items():
            if prereq(self.context):
                valid.append(target)
        return valid

    def missing_prerequisite(self, target: WorkflowState) -> str:
        """Return description of missing prerequisite for transition.

        Args:
            target: Target state we're trying to reach

        Returns:
            Human-readable description of what's missing
        """
        prereq_descriptions = {
            WorkflowState.RESEARCH: "new_info must be set",
            WorkflowState.DECIDE: "research_results must be populated (run RESEARCH first)",
            WorkflowState.WRITE: "decision must be create/update/merge (run DECIDE first)",
            WorkflowState.PROPAGATE: "entity_path must be set (run WRITE first)",
            WorkflowState.LOG: "propagated_paths must be set (run PROPAGATE first), or decision must be 'skip'",
            WorkflowState.REBUILD: "entity_created must be True (new entity was created)",
            WorkflowState.REFACTOR_CHECK: "REBUILD must complete, or entity was not created",
            WorkflowState.EXEC_REFACTOR: "should_refactor must be True (Bernoulli sampled)",
            WorkflowState.COMPLETE: "REFACTOR_CHECK must complete",
        }
        return prereq_descriptions.get(target, "unknown prerequisite")

    def transition(self, step_name: str) -> bool:
        """Attempt to transition based on step name.

        Args:
            step_name: Name of the step (e.g., "RESEARCH", "DECIDE")

        Returns:
            True if transition succeeded, False otherwise
        """
        target = self.STEP_TO_STATE.get(step_name.upper())
        if not target:
            return False

        if self.can_transition_to(target):
            self.history.append((self.current_state, f"to {target.name}"))
            self.current_state = target
            return True

        return False

    def force_transition(self, target: WorkflowState, reason: str = "forced") -> None:
        """Force transition to a state (bypasses prerequisites).

        Use with caution - primarily for error handling.

        Args:
            target: Target state
            reason: Reason for forcing the transition
        """
        self.history.append((self.current_state, f"forced to {target.name}: {reason}"))
        self.current_state = target

    def store_output(self, step: str, data: Dict[str, Any]) -> None:
        """Store step output in context.

        Args:
            step: Step name
            data: Output data from the step
        """
        step = step.upper()

        if step == "RESEARCH":
            self.context.research_results = data.get("matches", [])
            self.context.existing_entity_path = data.get("best_match_path")

        elif step == "DECIDE":
            self.context.decision = data.get("decision")
            self.context.decision_confidence = data.get("confidence")
            self.context.decision_reasoning = data.get("reasoning")
            self.context.target_path = data.get("target_path")

        elif step == "WRITE":
            self.context.entity_path = data.get("entity_path")
            self.context.meta_written = data.get("meta")
            self.context.summary_written = data.get("summary")
            self.context.entity_created = self.context.decision == "create"

        elif step == "PROPAGATE":
            self.context.propagated_paths = data.get("paths", [])

        elif step == "LOG":
            self.context.log_entry_id = data.get("log_id")
            self.context.journal_entry_path = data.get("journal_path")

        elif step == "REBUILD":
            self.context.index_count = data.get("count")
            self.context.index_rebuilt = True

        elif step == "REFACTOR_CHECK":
            self.context.should_refactor = data.get("should_refactor", False)
            self.context.refactor_opportunities = data.get("opportunities")

        elif step == "EXEC_REFACTOR":
            self.context.refactor_results = data.get("results")

    def validate_step_output(self, step: str, output: Any) -> ValidationResult:
        """Validate that step output meets requirements.

        Args:
            step: Step name
            output: Raw output from the step

        Returns:
            ValidationResult with is_valid, message, and extracted_data
        """
        step = step.upper()

        if step == "RESEARCH":
            # Research must return a list (even if empty)
            if isinstance(output, dict) and "matches" in output:
                return ValidationResult(True, "OK", output)
            if isinstance(output, (list, tuple)):
                return ValidationResult(True, "OK", {"matches": list(output)})
            return ValidationResult(False, "Research must return list of matches or dict with 'matches' key")

        elif step == "DECIDE":
            # Decision must be one of the valid actions
            if isinstance(output, dict):
                decision = output.get("decision")
                if decision in ("create", "update", "skip", "merge"):
                    return ValidationResult(True, "OK", output)
            return ValidationResult(
                False, "Decision must be dict with 'decision' key (create/update/skip/merge)"
            )

        elif step == "WRITE":
            # Write must have created a path
            if isinstance(output, dict) and output.get("entity_path"):
                return ValidationResult(True, "OK", output)
            return ValidationResult(False, "Write must return dict with 'entity_path' key")

        elif step == "PROPAGATE":
            # Propagate should return list of updated paths
            if isinstance(output, dict):
                return ValidationResult(True, "OK", output)
            if isinstance(output, list):
                return ValidationResult(True, "OK", {"paths": output})
            return ValidationResult(False, "Propagate must return dict with 'paths' key or list")

        elif step == "LOG":
            return ValidationResult(True, "OK", output if isinstance(output, dict) else {})

        elif step == "REBUILD":
            return ValidationResult(True, "OK", output if isinstance(output, dict) else {})

        elif step == "REFACTOR_CHECK":
            if isinstance(output, dict) and "should_refactor" in output:
                return ValidationResult(True, "OK", output)
            return ValidationResult(True, "OK", {"should_refactor": False})

        elif step == "EXEC_REFACTOR":
            return ValidationResult(True, "OK", output if isinstance(output, dict) else {"results": []})

        return ValidationResult(False, f"Unknown step: {step}")

    def get_current_step_name(self) -> str:
        """Get human-readable name of current step.

        Returns:
            Step name string
        """
        for name, state in self.STEP_TO_STATE.items():
            if state == self.current_state:
                return name
        return self.current_state.name

    def is_complete(self) -> bool:
        """Check if workflow has completed.

        Returns:
            True if in COMPLETE state
        """
        return self.current_state == WorkflowState.COMPLETE

    def is_error(self) -> bool:
        """Check if workflow is in error state.

        Returns:
            True if in ERROR state
        """
        return self.current_state == WorkflowState.ERROR

    def get_history(self) -> List[str]:
        """Get human-readable history of state transitions.

        Returns:
            List of transition descriptions
        """
        return [f"{from_state.name} {desc}" for from_state, desc in self.history]
