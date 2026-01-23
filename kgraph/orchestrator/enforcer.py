"""
WorkflowEnforcer - Hook implementations for mandatory step enforcement.

Provides PreToolUse and PostToolUse hooks that:
1. Block tool execution if step prerequisites are not met
2. Validate step outputs and transition state machine
3. Log all decisions for auditability

Hook Interface (compatible with Claude Agent SDK):
    async def hook(input_data: dict, tool_use_id: str | None, context: Any) -> dict
"""

import re
from typing import Any, Dict, Optional

from kgraph import ObservabilityLogger
from kgraph.orchestrator.state_machine import WorkflowStateMachine, WorkflowState


class WorkflowEnforcer:
    """Enforces 6-step workflow via tool use hooks.

    Maps tool calls to workflow steps and blocks out-of-order execution.

    Tool-to-step mapping:
    - Grep/Read on index.db → RESEARCH
    - Write to _meta.json or _summary.md → WRITE
    - Edit to ancestor _summary.md → PROPAGATE
    - Bash with rebuild_index.py → REBUILD
    - Write to journal/*/log.md → LOG

    Usage:
        state_machine = WorkflowStateMachine(context)
        enforcer = WorkflowEnforcer(state_machine, logger)

        # In Claude SDK options:
        options = ClaudeAgentOptions(
            hooks={
                "PreToolUse": [HookMatcher(hooks=[enforcer.pre_tool_gate])],
                "PostToolUse": [HookMatcher(hooks=[enforcer.post_tool_verify])],
            }
        )
    """

    def __init__(
        self,
        state_machine: WorkflowStateMachine,
        logger: Optional[ObservabilityLogger] = None,
        kg_root: Optional[str] = None,
    ):
        """Initialize enforcer.

        Args:
            state_machine: WorkflowStateMachine to enforce
            logger: Optional ObservabilityLogger for audit trail
            kg_root: Optional knowledge graph root path for path matching
        """
        self.sm = state_machine
        self.logger = logger
        self.kg_root = kg_root or ""
        self.pending_tool_use_id: Optional[str] = None
        self._step_outputs: Dict[str, Any] = {}

    def _tool_to_step(self, tool_name: str, tool_input: Dict[str, Any]) -> Optional[str]:
        """Map tool execution to workflow step.

        Args:
            tool_name: Name of the tool being used
            tool_input: Tool input parameters

        Returns:
            Step name if tool maps to a step, None otherwise
        """
        # Grep/Read against index.db → RESEARCH
        if tool_name in ("Grep", "Read"):
            path = tool_input.get("path", "") or tool_input.get("file_path", "")
            if "index.db" in path or ".kgraph" in path:
                return "RESEARCH"

        # Bash with sqlite3 on index.db → RESEARCH
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            if "index.db" in command and ("sqlite3" in command or "SELECT" in command):
                return "RESEARCH"

        # Write to _meta.json or _summary.md → WRITE
        if tool_name == "Write":
            file_path = tool_input.get("file_path", "")
            if "_meta.json" in file_path or "_summary.md" in file_path:
                # Check if this is the entity write (not propagate)
                # Propagate writes to ancestor paths (shorter than entity path)
                if self.sm.context.entity_path:
                    entity_parts = self.sm.context.entity_path.split("/")
                    file_parts = file_path.replace(self.kg_root, "").strip("/").split("/")
                    # If writing to a path with fewer parts, it's propagation
                    if len(file_parts) < len(entity_parts):
                        return "PROPAGATE"
                return "WRITE"

        # Edit to _summary.md → could be WRITE or PROPAGATE
        if tool_name == "Edit":
            file_path = tool_input.get("file_path", "")
            if "_summary.md" in file_path:
                # If entity_path is set and this is an ancestor, it's PROPAGATE
                if self.sm.context.entity_path:
                    entity_parts = self.sm.context.entity_path.split("/")
                    file_parts = file_path.replace(self.kg_root, "").strip("/").split("/")
                    if len(file_parts) < len(entity_parts):
                        return "PROPAGATE"
                return "WRITE"

        # Write to journal/*/log.md → LOG
        if tool_name in ("Write", "Edit"):
            file_path = tool_input.get("file_path", "")
            if "journal/" in file_path and "log.md" in file_path:
                return "LOG"

        # Bash with rebuild_index.py → REBUILD
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            if "rebuild_index" in command:
                return "REBUILD"

        return None

    def _get_expected_states_for_step(self, step: str) -> list[WorkflowState]:
        """Get valid current states from which a step can be executed.

        Args:
            step: Step name

        Returns:
            List of valid source states
        """
        step_prerequisites = {
            "RESEARCH": [WorkflowState.READY],
            "DECIDE": [WorkflowState.RESEARCH],
            "WRITE": [WorkflowState.DECIDE],
            "PROPAGATE": [WorkflowState.WRITE],
            "LOG": [WorkflowState.PROPAGATE, WorkflowState.DECIDE],  # Can skip WRITE if decision=skip
            "REBUILD": [WorkflowState.LOG],
            "REFACTOR_CHECK": [WorkflowState.REBUILD, WorkflowState.LOG],
            "EXEC_REFACTOR": [WorkflowState.REFACTOR_CHECK],
        }
        return step_prerequisites.get(step.upper(), [])

    async def pre_tool_gate(
        self,
        input_data: Dict[str, Any],
        tool_use_id: Optional[str],
        context: Any,
    ) -> Dict[str, Any]:
        """Gate: Block tool execution if step requirements not met.

        This hook runs BEFORE a tool is executed. It checks if the tool
        corresponds to a workflow step and if that step is allowed given
        the current state.

        Args:
            input_data: Contains tool_name and tool_input
            tool_use_id: Unique ID for this tool use
            context: Claude SDK context

        Returns:
            Empty dict to allow, or dict with permissionDecision="deny" to block
        """
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Map tool to expected workflow step
        expected_step = self._tool_to_step(tool_name, tool_input)

        if expected_step:
            target_state = self.sm.STEP_TO_STATE.get(expected_step)

            if target_state and not self.sm.can_transition_to(target_state):
                # Log the blocked attempt
                if self.logger:
                    self.logger.log_error(
                        error_type="workflow_violation",
                        entity=self.sm.context.new_info.get("name", "unknown"),
                        details={
                            "tool": tool_name,
                            "attempted_step": expected_step,
                            "current_state": self.sm.current_state.name,
                            "missing": self.sm.missing_prerequisite(target_state),
                        },
                        resolution="blocked",
                    )

                # Block execution
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"Workflow violation: Cannot execute {tool_name} ({expected_step}) "
                            f"in state {self.sm.current_state.name}. "
                            f"Prerequisite: {self.sm.missing_prerequisite(target_state)}"
                        ),
                    }
                }

        self.pending_tool_use_id = tool_use_id
        return {}  # Allow execution

    async def post_tool_verify(
        self,
        input_data: Dict[str, Any],
        tool_use_id: Optional[str],
        context: Any,
    ) -> Dict[str, Any]:
        """Verify: Validate output and transition state.

        This hook runs AFTER a tool is executed. It extracts relevant
        outputs, validates them, and transitions the state machine.

        Args:
            input_data: Contains tool_name, tool_input, and tool_response
            tool_use_id: Unique ID for this tool use
            context: Claude SDK context

        Returns:
            Empty dict normally, or dict with systemMessage on validation issues
        """
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        tool_response = input_data.get("tool_response")

        # Map tool to workflow step
        step = self._tool_to_step(tool_name, tool_input)

        if step:
            # Extract step-specific data from tool response
            extracted_data = self._extract_step_data(step, tool_name, tool_input, tool_response)

            # Validate the output
            validation_result = self.sm.validate_step_output(step, extracted_data)

            if validation_result.is_valid:
                # Store outputs in context
                self.sm.store_output(step, validation_result.extracted_data or extracted_data)

                # Transition state machine
                target_state = self.sm.STEP_TO_STATE.get(step)
                if target_state and self.sm.can_transition_to(target_state):
                    self.sm.transition(step)

                    # Log successful step
                    if self.logger:
                        self._log_step_completion(step, extracted_data)
            else:
                # Log validation warning (but don't block - it's already executed)
                return {
                    "systemMessage": (
                        f"Step {step} output validation warning: {validation_result.message}"
                    )
                }

        return {}

    def _extract_step_data(
        self,
        step: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_response: Any,
    ) -> Dict[str, Any]:
        """Extract step-specific data from tool response.

        Args:
            step: Workflow step name
            tool_name: Tool that was executed
            tool_input: Tool input parameters
            tool_response: Raw tool response

        Returns:
            Dict with step-specific data
        """
        response_str = str(tool_response) if tool_response else ""

        if step == "RESEARCH":
            # Try to extract match info from response
            matches = []
            # Look for patterns like "Found N matches" or entity paths
            match_count = re.search(r"Found\s+(\d+)\s+match", response_str, re.IGNORECASE)
            if match_count:
                # Placeholder - actual matches would come from structured output
                pass
            return {"matches": matches}

        elif step == "DECIDE":
            # Extract decision from context or response
            decision = None
            for action in ["create", "update", "skip", "merge"]:
                if action.lower() in response_str.lower():
                    decision = action
                    break
            return {
                "decision": decision or self.sm.context.decision,
                "confidence": self.sm.context.decision_confidence,
                "reasoning": self.sm.context.decision_reasoning,
            }

        elif step == "WRITE":
            # Extract entity path from tool input
            file_path = tool_input.get("file_path", "")
            # Normalize to entity path (remove _meta.json or _summary.md)
            entity_path = re.sub(r"/_?(meta\.json|summary\.md)$", "", file_path)
            entity_path = entity_path.replace(self.kg_root, "").strip("/")
            return {"entity_path": entity_path}

        elif step == "PROPAGATE":
            # Track propagated path
            file_path = tool_input.get("file_path", "")
            propagated = file_path.replace(self.kg_root, "").strip("/")
            propagated = re.sub(r"/_summary\.md$", "", propagated)
            existing = self.sm.context.propagated_paths or []
            return {"paths": existing + [propagated]}

        elif step == "LOG":
            file_path = tool_input.get("file_path", "")
            return {"journal_path": file_path}

        elif step == "REBUILD":
            # Try to extract count from output
            count_match = re.search(r"(\d+)\s+entit", response_str)
            count = int(count_match.group(1)) if count_match else None
            return {"count": count}

        return {}

    def _log_step_completion(self, step: str, data: Dict[str, Any]) -> None:
        """Log step completion to observability database.

        Args:
            step: Step name
            data: Step output data
        """
        if not self.logger:
            return

        entity_name = self.sm.context.new_info.get("name", "unknown")

        if step == "RESEARCH":
            matches = data.get("matches", [])
            self.logger.log_research(
                entity=entity_name,
                query=entity_name.lower(),
                matches=[m if isinstance(m, dict) else {"match": str(m)} for m in matches],
                decision="pending",
            )

        elif step == "DECIDE":
            self.logger.log_decide(
                entity=entity_name,
                action=data.get("decision", "unknown"),
                reasoning=data.get("reasoning", ""),
                confidence=data.get("confidence"),
            )

        elif step == "WRITE":
            self.logger.log_write(
                path=data.get("entity_path", ""),
                change_type="create" if self.sm.context.entity_created else "update",
                diff_summary=f"Wrote entity for {entity_name}",
            )

        elif step == "PROPAGATE":
            self.logger.log_propagate(
                from_path=self.sm.context.entity_path or "",
                updated_paths=data.get("paths", []),
                reasoning="Updated ancestor summaries",
            )

    def get_workflow_status(self) -> Dict[str, Any]:
        """Get current workflow status for debugging.

        Returns:
            Dict with current state, history, and context summary
        """
        return {
            "current_state": self.sm.current_state.name,
            "step_name": self.sm.get_current_step_name(),
            "is_complete": self.sm.is_complete(),
            "is_error": self.sm.is_error(),
            "history": self.sm.get_history(),
            "valid_transitions": [s.name for s in self.sm.get_valid_transitions()],
            "context": {
                "entity": self.sm.context.new_info.get("name"),
                "decision": self.sm.context.decision,
                "entity_path": self.sm.context.entity_path,
                "propagated_count": len(self.sm.context.propagated_paths or []),
            },
        }
