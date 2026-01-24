"""
WorkflowStateMachine - Enforces mandatory 6-step workflow sequence.

Supports two modes:
1. Legacy (entity-centric): READY → RESEARCH → DECIDE → WRITE → PROPAGATE → LOG → REBUILD
2. Hierarchy-based: READY → RESEARCH → DECIDE → EXECUTE → PROPAGATE → LOG → REBUILD

In hierarchy mode:
- EXECUTE replaces WRITE
- EXECUTE can handle multiple actions from an ActionPlan
- State tracks completion across multiple actions

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
    WRITE = auto()      # Legacy: single entity write
    EXECUTE = auto()    # New: multi-action execution (alias for WRITE in hierarchy mode)
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
            # Legacy: new_info must be set
            # Hierarchy: raw_input must be set
            WorkflowState.RESEARCH: lambda ctx: (
                ctx.new_info is not None or ctx.raw_input is not None
            ),
        },
        WorkflowState.RESEARCH: {
            WorkflowState.DECIDE: lambda ctx: ctx.research_results is not None,
        },
        WorkflowState.DECIDE: {
            # Legacy: proceed to WRITE if decision requires it
            WorkflowState.WRITE: lambda ctx: (
                not ctx.is_hierarchy_mode
                and ctx.decision in ("create", "update", "merge")
            ),
            # Hierarchy: proceed to EXECUTE if plan has actions
            WorkflowState.EXECUTE: lambda ctx: (
                ctx.is_hierarchy_mode
                and ctx.action_plan is not None
                and len(ctx.action_plan.actions) > 0
            ),
            # Can skip to LOG if decision is "skip" (legacy) or plan is empty (hierarchy)
            WorkflowState.LOG: lambda ctx: (
                (not ctx.is_hierarchy_mode and ctx.decision == "skip")
                or (ctx.is_hierarchy_mode and ctx.action_plan is not None and len(ctx.action_plan.actions) == 0)
            ),
        },
        WorkflowState.WRITE: {
            # Legacy: proceed when entity_path is set
            WorkflowState.PROPAGATE: lambda ctx: ctx.entity_path is not None,
        },
        WorkflowState.EXECUTE: {
            # Hierarchy: proceed when all planned actions are executed
            WorkflowState.PROPAGATE: lambda ctx: (
                ctx.action_plan is not None
                and len(ctx.executed_actions) >= len(ctx.action_plan.actions)
            ),
        },
        WorkflowState.PROPAGATE: {
            WorkflowState.LOG: lambda ctx: ctx.propagated_paths is not None,
        },
        WorkflowState.LOG: {
            # Legacy: REBUILD only if new entity was created
            # Hierarchy: REBUILD if any creates happened
            WorkflowState.REBUILD: lambda ctx: (
                ctx.entity_created
                or (ctx.is_hierarchy_mode and len(ctx.created_paths) > 0)
            ),
            # Skip REBUILD if no new entities
            WorkflowState.REFACTOR_CHECK: lambda ctx: (
                (not ctx.is_hierarchy_mode and not ctx.entity_created)
                or (ctx.is_hierarchy_mode and len(ctx.created_paths) == 0)
            ),
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
        "EXECUTE": WorkflowState.EXECUTE,  # New: hierarchy mode
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
            if self.context.is_hierarchy_mode:
                # Hierarchy mode: store action plan
                from kgraph.orchestrator.context import ActionPlan, PlannedAction
                plan_data = data.get("action_plan") or data
                if isinstance(plan_data, ActionPlan):
                    self.context.action_plan = plan_data
                elif isinstance(plan_data, dict):
                    # Parse from dict
                    actions = []
                    for a in plan_data.get("actions", []):
                        actions.append(PlannedAction(
                            action_type=a.get("action_type", "skip"),
                            path=a.get("path", ""),
                            reasoning=a.get("reasoning", ""),
                            confidence=a.get("confidence", 0.0),
                            content=a.get("content"),
                            target_path=a.get("target_path"),
                        ))
                    self.context.action_plan = ActionPlan(
                        actions=actions,
                        overall_reasoning=plan_data.get("overall_reasoning", ""),
                    )
            else:
                # Legacy mode: store single decision
                self.context.decision = data.get("decision")
                self.context.decision_confidence = data.get("confidence")
                self.context.decision_reasoning = data.get("reasoning")
                self.context.target_path = data.get("target_path")

        elif step == "WRITE":
            # Legacy mode: single entity write
            self.context.entity_path = data.get("entity_path")
            self.context.meta_written = data.get("meta")
            self.context.summary_written = data.get("summary")
            self.context.entity_created = self.context.decision == "create"

        elif step == "EXECUTE":
            # Hierarchy mode: accumulate executed actions
            action = data.get("action")
            if action:
                self.context.executed_actions.append(action)
                action_type = action.get("action_type")
                path = action.get("path")
                if action_type == "create" and path:
                    self.context.created_paths.append(path)
                elif action_type == "update" and path:
                    self.context.updated_paths.append(path)

            # Check if all actions are complete, set propagation roots
            if (
                self.context.action_plan
                and len(self.context.executed_actions) >= len(self.context.action_plan.actions)
            ):
                self.context.propagation_roots = list(set(
                    self.context.created_paths + self.context.updated_paths
                ))

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

        elif step == "EXECUTE":
            # Execute must return action info (single action from plan)
            if isinstance(output, dict) and output.get("action"):
                action = output["action"]
                if isinstance(action, dict) and action.get("action_type") and action.get("path"):
                    return ValidationResult(True, "OK", output)
            # Also accept action directly (not wrapped)
            if isinstance(output, dict) and output.get("action_type") and output.get("path"):
                return ValidationResult(True, "OK", {"action": output})
            return ValidationResult(
                False,
                "Execute must return dict with 'action' key containing action_type and path",
            )

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
