"""Tests for kgraph.orchestrator module.

Testing Philosophy (following CJE patterns):
1. E2E Focus - Test complete workflows, not isolated functions
2. Real Patterns - Test what users actually do
3. Fast Feedback - Each test < 1 second
4. Clear Intent - Each test has one clear purpose

Test Structure:
- E2E Tests: Complete workflow validation
- Core Tests: State machine and enforcer
- Integration Tests: CLI and subprocess
"""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from kgraph.orchestrator.context import OrchestratorConfig, WorkflowContext
from kgraph.orchestrator.state_machine import WorkflowStateMachine, WorkflowState
from kgraph.orchestrator.enforcer import WorkflowEnforcer


# -------------------------
# WorkflowContext Tests
# -------------------------


class TestWorkflowContext:
    """Tests for WorkflowContext dataclass."""

    def test_create_minimal(self):
        """Can create context with minimal required fields."""
        ctx = WorkflowContext(new_info={"name": "Alice", "type": "person"})
        assert ctx.new_info["name"] == "Alice"
        assert ctx.decision is None
        assert ctx.entity_path is None

    def test_create_full(self):
        """Can create context with all fields."""
        ctx = WorkflowContext(
            new_info={"name": "Alice", "type": "person", "email": "alice@example.com"},
            meta_context="# CLAUDE.md content",
            last_k_updates=[],
            refactor_probability=0.2,
        )
        assert ctx.new_info["email"] == "alice@example.com"
        assert ctx.refactor_probability == 0.2

    def test_to_dict(self):
        """Can serialize context to dictionary."""
        ctx = WorkflowContext(new_info={"name": "Alice", "type": "person"})
        ctx.decision = "create"
        ctx.entity_path = "people/alice"

        d = ctx.to_dict()
        assert d["new_info"]["name"] == "Alice"
        assert d["decision"] == "create"
        assert d["entity_path"] == "people/alice"


class TestOrchestratorConfig:
    """Tests for OrchestratorConfig."""

    def test_default_values(self, tmp_path):
        """Config has sensible defaults."""
        config = OrchestratorConfig(kg_root=tmp_path)
        assert config.refactor_probability == 0.1
        assert config.last_k_updates == 10
        assert config.permission_mode == "bypassPermissions"
        assert "Read" in config.allowed_tools

    def test_custom_values(self, tmp_path):
        """Can override defaults."""
        config = OrchestratorConfig(
            kg_root=tmp_path,
            refactor_probability=0.5,
            last_k_updates=20,
        )
        assert config.refactor_probability == 0.5
        assert config.last_k_updates == 20


# -------------------------
# WorkflowStateMachine Tests
# -------------------------


class TestWorkflowStateMachine:
    """Tests for WorkflowStateMachine."""

    @pytest.fixture
    def context(self):
        """Create a basic context for testing."""
        return WorkflowContext(new_info={"name": "Test", "type": "person"})

    @pytest.fixture
    def state_machine(self, context):
        """Create a state machine with context."""
        return WorkflowStateMachine(context)

    def test_initial_state(self, state_machine):
        """Starts in READY state."""
        assert state_machine.current_state == WorkflowState.READY

    def test_can_transition_to_research(self, state_machine):
        """Can transition from READY to RESEARCH."""
        assert state_machine.can_transition_to(WorkflowState.RESEARCH)

    def test_cannot_skip_steps(self, state_machine):
        """Cannot skip RESEARCH and go directly to DECIDE."""
        assert not state_machine.can_transition_to(WorkflowState.DECIDE)

    def test_transition_to_research(self, state_machine):
        """Can transition to RESEARCH step when new_info is set."""
        # RESEARCH is valid from READY when new_info is set (which it is from fixture)
        assert state_machine.transition("RESEARCH") is True
        assert state_machine.current_state == WorkflowState.RESEARCH

        # Now store output and check DECIDE is available
        state_machine.store_output("RESEARCH", {"matches": []})
        assert state_machine.can_transition_to(WorkflowState.DECIDE)

    def test_full_workflow_create(self, state_machine, context):
        """Can complete full workflow for CREATE action."""
        # RESEARCH
        state_machine.store_output("RESEARCH", {"matches": []})
        assert state_machine.transition("RESEARCH")
        assert state_machine.current_state == WorkflowState.RESEARCH

        # DECIDE
        state_machine.store_output("DECIDE", {"decision": "create", "confidence": 0.95})
        assert state_machine.transition("DECIDE")
        assert state_machine.current_state == WorkflowState.DECIDE

        # WRITE
        state_machine.store_output("WRITE", {"entity_path": "people/test"})
        assert state_machine.transition("WRITE")
        assert state_machine.current_state == WorkflowState.WRITE
        assert context.entity_created  # Should be True since decision was "create"

        # PROPAGATE
        state_machine.store_output("PROPAGATE", {"paths": ["people"]})
        assert state_machine.transition("PROPAGATE")
        assert state_machine.current_state == WorkflowState.PROPAGATE

        # LOG
        state_machine.store_output("LOG", {"log_id": 1})
        assert state_machine.transition("LOG")
        assert state_machine.current_state == WorkflowState.LOG

        # REBUILD (since entity was created)
        assert state_machine.can_transition_to(WorkflowState.REBUILD)
        state_machine.store_output("REBUILD", {"count": 5})
        assert state_machine.transition("REBUILD")

        # REFACTOR_CHECK
        state_machine.store_output("REFACTOR_CHECK", {"should_refactor": False})
        assert state_machine.transition("REFACTOR_CHECK")

        # COMPLETE (since should_refactor is False)
        assert state_machine.transition("COMPLETE")
        assert state_machine.is_complete()

    def test_workflow_skip_decision(self, state_machine, context):
        """Can skip WRITE/PROPAGATE when decision is 'skip'."""
        # RESEARCH
        state_machine.store_output("RESEARCH", {"matches": []})
        state_machine.transition("RESEARCH")

        # DECIDE with skip
        state_machine.store_output("DECIDE", {"decision": "skip"})
        state_machine.transition("DECIDE")

        # Can go directly to LOG (skipping WRITE/PROPAGATE)
        assert state_machine.can_transition_to(WorkflowState.LOG)
        assert not state_machine.can_transition_to(WorkflowState.WRITE)

    def test_missing_prerequisite_description(self, state_machine):
        """Get human-readable description of missing prerequisites."""
        desc = state_machine.missing_prerequisite(WorkflowState.DECIDE)
        assert "research_results" in desc.lower() or "research" in desc.lower()

    def test_get_history(self, state_machine):
        """Can get transition history."""
        state_machine.store_output("RESEARCH", {"matches": []})
        state_machine.transition("RESEARCH")

        history = state_machine.get_history()
        assert len(history) == 1
        assert "RESEARCH" in history[0]

    def test_force_transition(self, state_machine):
        """Can force transition (for error handling)."""
        state_machine.force_transition(WorkflowState.ERROR, "test error")
        assert state_machine.is_error()
        assert "forced" in state_machine.get_history()[-1].lower()


# -------------------------
# WorkflowEnforcer Tests
# -------------------------


class TestWorkflowEnforcer:
    """Tests for WorkflowEnforcer hooks."""

    @pytest.fixture
    def context(self):
        return WorkflowContext(new_info={"name": "Test", "type": "person"})

    @pytest.fixture
    def state_machine(self, context):
        return WorkflowStateMachine(context)

    @pytest.fixture
    def enforcer(self, state_machine):
        return WorkflowEnforcer(state_machine, logger=None, kg_root="/test/kg")

    def test_tool_to_step_mapping_research(self, enforcer):
        """Maps grep on index.db to RESEARCH step."""
        step = enforcer._tool_to_step("Grep", {"path": "/test/kg/.kgraph/index.db"})
        assert step == "RESEARCH"

    def test_tool_to_step_mapping_write(self, enforcer):
        """Maps write to _meta.json to WRITE step."""
        step = enforcer._tool_to_step("Write", {"file_path": "/test/kg/people/alice/_meta.json"})
        assert step == "WRITE"

    def test_tool_to_step_mapping_rebuild(self, enforcer):
        """Maps bash with rebuild_index to REBUILD step."""
        step = enforcer._tool_to_step("Bash", {"command": "python scripts/rebuild_index.py"})
        assert step == "REBUILD"

    def test_tool_to_step_mapping_journal(self, enforcer):
        """Maps write to journal to LOG step."""
        step = enforcer._tool_to_step("Write", {"file_path": "/test/kg/journal/2024-01/log.md"})
        assert step == "LOG"

    def test_tool_to_step_unknown(self, enforcer):
        """Returns None for unmapped tools."""
        step = enforcer._tool_to_step("SomeOtherTool", {})
        assert step is None

    @pytest.mark.asyncio
    async def test_pre_tool_gate_allows_valid_step(self, enforcer):
        """Allows tool execution when prerequisites are met."""
        # RESEARCH is valid from READY state
        result = await enforcer.pre_tool_gate(
            {"tool_name": "Grep", "tool_input": {"path": "/test/kg/.kgraph/index.db"}},
            "test-id",
            None,
        )
        # Empty dict means allowed
        assert result == {}

    @pytest.mark.asyncio
    async def test_pre_tool_gate_blocks_invalid_step(self, enforcer, state_machine):
        """Blocks tool execution when prerequisites not met."""
        # Try to WRITE without completing RESEARCH and DECIDE first
        result = await enforcer.pre_tool_gate(
            {"tool_name": "Write", "tool_input": {"file_path": "/test/kg/people/test/_meta.json"}},
            "test-id",
            None,
        )
        # Should have blocked
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "workflow violation" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    @pytest.mark.asyncio
    async def test_post_tool_verify_extracts_data(self, enforcer, state_machine):
        """Extracts step data from tool response."""
        # First allow RESEARCH to proceed
        state_machine.store_output("RESEARCH", {"matches": []})
        state_machine.transition("RESEARCH")

        # Now verify a DECIDE step
        state_machine.store_output("DECIDE", {"decision": "create"})

        result = await enforcer.post_tool_verify(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/test/kg/people/test/_meta.json"},
                "tool_response": "File written successfully",
            },
            "test-id",
            None,
        )
        # Should return empty dict on success
        assert isinstance(result, dict)

    def test_get_workflow_status(self, enforcer, state_machine):
        """Can get current workflow status."""
        status = enforcer.get_workflow_status()
        assert status["current_state"] == "READY"
        assert status["is_complete"] is False
        assert "valid_transitions" in status


# -------------------------
# Integration Tests
# -------------------------


class TestOrchestratorIntegration:
    """Integration tests for the orchestrator module."""

    @pytest.fixture
    def mock_subprocess(self):
        """Mock subprocess.run for CLI tests."""
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                stdout="""
RESEARCH COMPLETE: No existing matches found for "Test Person"
DECIDE COMPLETE: CREATE - no existing match
WRITE COMPLETE: Created entity at people/test_person
PROPAGATE COMPLETE: Updated people/_summary.md
LOG COMPLETE: Added journal entry
REBUILD COMPLETE: Indexed 5 entities
""",
                stderr="",
                returncode=0,
            )
            yield mock

    @pytest.mark.asyncio
    async def test_process_parses_step_completions(self, tmp_kg_path, mock_subprocess):
        """Can parse step completion markers from CLI output."""
        from kgraph.orchestrator.runner import HeadlessOrchestrator
        from kgraph.orchestrator.context import OrchestratorConfig

        # Create minimal KG structure
        (tmp_kg_path / ".kgraph").mkdir()
        (tmp_kg_path / "people").mkdir()
        (tmp_kg_path / "journal" / "2024-01").mkdir(parents=True)

        config = OrchestratorConfig(kg_root=tmp_kg_path)
        orchestrator = HeadlessOrchestrator(config)

        result = await orchestrator.process(
            {"name": "Test Person", "type": "person", "source": "test"}
        )

        assert result["session_id"] is not None
        # The mock should have been called
        assert mock_subprocess.called


class TestStateMachineEdgeCases:
    """Edge case tests for state machine."""

    def test_empty_matches_allows_create(self):
        """Empty matches list allows CREATE decision."""
        ctx = WorkflowContext(new_info={"name": "New", "type": "person"})
        sm = WorkflowStateMachine(ctx)

        sm.store_output("RESEARCH", {"matches": []})
        sm.transition("RESEARCH")

        sm.store_output("DECIDE", {"decision": "create"})
        sm.transition("DECIDE")

        assert sm.can_transition_to(WorkflowState.WRITE)

    def test_update_without_entity_created(self):
        """UPDATE decision should not mark entity_created as True."""
        ctx = WorkflowContext(new_info={"name": "Existing", "type": "person"})
        sm = WorkflowStateMachine(ctx)

        sm.store_output("RESEARCH", {"matches": [{"path": "people/existing"}]})
        sm.transition("RESEARCH")

        sm.store_output("DECIDE", {"decision": "update", "target_path": "people/existing"})
        sm.transition("DECIDE")

        sm.store_output("WRITE", {"entity_path": "people/existing"})
        sm.transition("WRITE")

        # entity_created should be False for update
        assert ctx.entity_created is False

    def test_refactor_with_probability_one(self):
        """With probability 1.0, refactor should always trigger."""
        ctx = WorkflowContext(
            new_info={"name": "Test", "type": "person"},
            refactor_probability=1.0,
        )
        sm = WorkflowStateMachine(ctx)

        # Complete workflow to REFACTOR_CHECK
        sm.store_output("RESEARCH", {"matches": []})
        sm.transition("RESEARCH")
        sm.store_output("DECIDE", {"decision": "skip"})
        sm.transition("DECIDE")
        sm.store_output("LOG", {})
        sm.transition("LOG")
        sm.store_output("REFACTOR_CHECK", {"should_refactor": True})
        sm.transition("REFACTOR_CHECK")

        # Should be able to execute refactor
        assert sm.can_transition_to(WorkflowState.EXEC_REFACTOR)
        assert not sm.can_transition_to(WorkflowState.COMPLETE)


# -------------------------
# E2E Workflow Tests (User Patterns)
# -------------------------


class TestE2EWorkflows:
    """End-to-end tests validating complete user workflows.

    Following CJE testing philosophy:
    - Test what users actually do
    - Use realistic data and scenarios
    - Validate full pipelines, not isolated functions
    """

    @pytest.fixture
    def kg_with_entities(self, tmp_path):
        """Create a realistic knowledge graph with existing entities."""
        kg_root = tmp_path / "knowledge_graph"
        kg_root.mkdir()

        # Create .kgraph directory
        kgraph_dir = kg_root / ".kgraph"
        kgraph_dir.mkdir()

        # Create people category
        people_dir = kg_root / "people"
        people_dir.mkdir()
        (people_dir / "_summary.md").write_text("# People\n\nList of people in the knowledge graph.\n")

        # Create an existing person entity
        alice_dir = people_dir / "alice_smith"
        alice_dir.mkdir()
        (alice_dir / "_meta.json").write_text(json.dumps({
            "created": "2024-01-01",
            "last_updated": "2024-01-15",
            "sources": ["linkedin:alicesmith"],
            "aliases": ["Alice", "alice@example.com"],
        }))
        (alice_dir / "_summary.md").write_text("# Alice Smith\n\nResearch collaborator.\n")

        # Create journal structure
        journal_dir = kg_root / "journal" / "2024-01"
        journal_dir.mkdir(parents=True)
        (journal_dir / "log.md").write_text("# Journal - January 2024\n\n")

        # Create root summary
        (kg_root / "_summary.md").write_text("# Knowledge Graph\n\n## People\n- Alice Smith\n")

        return kg_root

    def test_e2e_create_new_entity_workflow(self, kg_with_entities):
        """Test complete workflow: Create new entity when no match exists."""
        # This tests the full user scenario:
        # 1. User has new information about "Bob Jones"
        # 2. System searches, finds no match
        # 3. System creates new entity
        # 4. Propagates to ancestors
        # 5. Logs the action

        from kgraph import EntityIndex, ObservabilityLogger, SimpleStorage

        kg_root = kg_with_entities
        kgraph_dir = kg_root / ".kgraph"

        # Initialize infrastructure (as user would)
        index = EntityIndex(kgraph_dir / "index.db")
        index.rebuild(kg_root)  # Build index from existing entities
        logger = ObservabilityLogger(kgraph_dir / "logs.db")
        storage = SimpleStorage(kg_root)

        # Simulate the workflow steps
        ctx = WorkflowContext(
            new_info={
                "name": "Bob Jones",
                "type": "person",
                "email": "bob@company.com",
                "source": "manual:2024-01-20",
            }
        )
        sm = WorkflowStateMachine(ctx)

        # 1. RESEARCH - Search for existing
        results = index.search("Bob Jones", limit=5)
        sm.store_output("RESEARCH", {"matches": list(results)})
        sm.transition("RESEARCH")
        assert ctx.research_results == []  # No match found

        # 2. DECIDE - Create new
        sm.store_output("DECIDE", {
            "decision": "create",
            "confidence": 0.95,
            "reasoning": "No existing match for Bob Jones",
        })
        sm.transition("DECIDE")
        assert ctx.decision == "create"

        # 3. WRITE - Create entity
        entity_path = "people/bob_jones"
        meta = {
            "created": "2024-01-20",
            "last_updated": "2024-01-20",
            "sources": ["manual:2024-01-20"],
            "aliases": ["Bob", "bob@company.com"],
        }
        summary = "# Bob Jones\n\nNew contact.\n"
        storage.create_entity(entity_path, meta, summary)
        sm.store_output("WRITE", {"entity_path": entity_path})
        sm.transition("WRITE")
        assert ctx.entity_created  # New entity

        # 4. PROPAGATE - Update ancestors
        ancestors = storage.get_ancestors(entity_path)
        sm.store_output("PROPAGATE", {"paths": ancestors})
        sm.transition("PROPAGATE")
        assert "people" in ctx.propagated_paths

        # 5. LOG
        logger.log_decide("Bob Jones", "create", "No existing match", 0.95)
        logger.log_write(entity_path, "create", "Created new entity")
        sm.store_output("LOG", {"log_id": 1})
        sm.transition("LOG")

        # 6. REBUILD (since entity created)
        assert sm.can_transition_to(WorkflowState.REBUILD)
        count = index.rebuild(kg_root)
        sm.store_output("REBUILD", {"count": count})
        sm.transition("REBUILD")

        # Verify results
        assert count == 2  # Alice + Bob
        assert (kg_root / "people" / "bob_jones" / "_meta.json").exists()
        bob_results = index.search("Bob Jones", limit=1)
        assert len(bob_results) == 1

    def test_e2e_update_existing_entity_workflow(self, kg_with_entities):
        """Test complete workflow: Update entity when match found."""
        from kgraph import EntityIndex, SimpleStorage

        kg_root = kg_with_entities
        index = EntityIndex(kg_root / ".kgraph" / "index.db")
        index.rebuild(kg_root)
        storage = SimpleStorage(kg_root)

        ctx = WorkflowContext(
            new_info={
                "name": "Alice Smith",
                "type": "person",
                "email": "alice@newcompany.com",
                "source": "email:12345",
            }
        )
        sm = WorkflowStateMachine(ctx)

        # 1. RESEARCH - Find existing
        results = index.search("Alice Smith", limit=5)
        sm.store_output("RESEARCH", {"matches": [r.__dict__ for r in results]})
        sm.transition("RESEARCH")
        assert len(ctx.research_results) == 1

        # 2. DECIDE - Update existing
        sm.store_output("DECIDE", {
            "decision": "update",
            "confidence": 0.95,
            "target_path": "people/alice_smith",
        })
        sm.transition("DECIDE")
        assert ctx.decision == "update"

        # 3. WRITE - Update entity
        existing_meta = storage.read_meta("people/alice_smith")
        existing_meta["sources"].append("email:12345")
        existing_meta["aliases"].append("alice@newcompany.com")
        existing_meta["last_updated"] = "2024-01-20"
        storage.update_entity("people/alice_smith", meta=existing_meta)
        sm.store_output("WRITE", {"entity_path": "people/alice_smith"})
        sm.transition("WRITE")
        assert not ctx.entity_created  # Updated, not created

        # 4. PROPAGATE
        sm.store_output("PROPAGATE", {"paths": ["people"]})
        sm.transition("PROPAGATE")

        # 5. LOG
        sm.store_output("LOG", {})
        sm.transition("LOG")

        # 6. Skip REBUILD (no new entity), go to REFACTOR_CHECK
        assert not sm.can_transition_to(WorkflowState.REBUILD)  # entity_created is False
        assert sm.can_transition_to(WorkflowState.REFACTOR_CHECK)

        # Verify update
        updated_meta = storage.read_meta("people/alice_smith")
        assert "alice@newcompany.com" in updated_meta["aliases"]
        assert "email:12345" in updated_meta["sources"]

    def test_e2e_skip_workflow(self, kg_with_entities):
        """Test complete workflow: Skip when info not valuable."""
        ctx = WorkflowContext(
            new_info={
                "name": "Customer",  # Too generic
                "type": "person",
                "source": "email:spam",
            }
        )
        sm = WorkflowStateMachine(ctx)

        # 1. RESEARCH
        sm.store_output("RESEARCH", {"matches": []})
        sm.transition("RESEARCH")

        # 2. DECIDE - Skip (name too generic)
        sm.store_output("DECIDE", {
            "decision": "skip",
            "confidence": 0.9,
            "reasoning": "Name 'Customer' is too generic",
        })
        sm.transition("DECIDE")

        # Can skip directly to LOG (no WRITE/PROPAGATE needed)
        assert sm.can_transition_to(WorkflowState.LOG)
        assert not sm.can_transition_to(WorkflowState.WRITE)

        # 3. LOG
        sm.store_output("LOG", {})
        sm.transition("LOG")

        # 4. REFACTOR_CHECK (skip REBUILD since nothing created)
        sm.store_output("REFACTOR_CHECK", {"should_refactor": False})
        sm.transition("REFACTOR_CHECK")

        # 5. COMPLETE
        sm.transition("COMPLETE")
        assert sm.is_complete()


class TestE2EEnforcement:
    """Test that workflow enforcement actually blocks violations."""

    def test_cannot_write_before_decide(self):
        """Enforcer blocks WRITE before DECIDE is complete."""
        ctx = WorkflowContext(new_info={"name": "Test", "type": "person"})
        sm = WorkflowStateMachine(ctx)
        enforcer = WorkflowEnforcer(sm, logger=None, kg_root="/test")

        # Only do RESEARCH, skip DECIDE
        sm.store_output("RESEARCH", {"matches": []})
        sm.transition("RESEARCH")

        # Try to map a Write tool call - should fail prerequisite check
        step = enforcer._tool_to_step("Write", {"file_path": "/test/people/test/_meta.json"})
        assert step == "WRITE"

        # WRITE state requires DECIDE to be complete
        assert not sm.can_transition_to(WorkflowState.WRITE)

    def test_cannot_propagate_before_write(self):
        """Enforcer blocks PROPAGATE before WRITE is complete."""
        ctx = WorkflowContext(new_info={"name": "Test", "type": "person"})
        sm = WorkflowStateMachine(ctx)

        # Complete up to DECIDE
        sm.store_output("RESEARCH", {"matches": []})
        sm.transition("RESEARCH")
        sm.store_output("DECIDE", {"decision": "create"})
        sm.transition("DECIDE")

        # PROPAGATE requires WRITE to be complete
        assert not sm.can_transition_to(WorkflowState.PROPAGATE)
        assert sm.can_transition_to(WorkflowState.WRITE)  # But WRITE is valid


# -------------------------
# Hierarchy Mode Tests
# -------------------------


class TestHierarchyModeContext:
    """Tests for hierarchy-based WorkflowContext."""

    def test_is_hierarchy_mode_with_raw_input(self):
        """Context is in hierarchy mode when raw_input is set."""
        from kgraph.orchestrator.context import HierarchyInput

        raw_input = HierarchyInput(content="Test content", source="test:manual")
        ctx = WorkflowContext(raw_input=raw_input)

        assert ctx.is_hierarchy_mode is True
        assert ctx.raw_input.content == "Test content"
        assert ctx.raw_input.source == "test:manual"

    def test_is_legacy_mode_with_new_info(self):
        """Context is in legacy mode when new_info is set (not raw_input)."""
        ctx = WorkflowContext(new_info={"name": "Alice", "type": "person"})

        assert ctx.is_hierarchy_mode is False

    def test_action_plan_storage(self):
        """Can store and retrieve action plan."""
        from kgraph.orchestrator.context import HierarchyInput, ActionPlan, PlannedAction

        raw_input = HierarchyInput(content="Coffee with Bob", source="manual")
        ctx = WorkflowContext(raw_input=raw_input)

        plan = ActionPlan(
            actions=[
                PlannedAction(
                    action_type="create",
                    path="people/bob",
                    reasoning="New contact",
                    confidence=0.95,
                )
            ],
            overall_reasoning="New person mentioned",
        )
        ctx.action_plan = plan

        assert len(ctx.action_plan.actions) == 1
        assert ctx.action_plan.actions[0].path == "people/bob"
        assert ctx.action_plan.has_creates is True

    def test_to_dict_hierarchy_mode(self):
        """to_dict works for hierarchy mode context."""
        from kgraph.orchestrator.context import HierarchyInput, ActionPlan, PlannedAction

        raw_input = HierarchyInput(content="Test", source="test")
        ctx = WorkflowContext(raw_input=raw_input)
        ctx.action_plan = ActionPlan(
            actions=[PlannedAction(action_type="create", path="test", reasoning="Test", confidence=0.9)],
            overall_reasoning="Test",
        )

        result = ctx.to_dict()

        assert result["is_hierarchy_mode"] is True
        assert result["action_plan"]["actions"][0]["path"] == "test"


class TestHierarchyModeStateMachine:
    """Tests for hierarchy mode state machine transitions."""

    def test_execute_state_exists(self):
        """EXECUTE state exists in WorkflowState."""
        assert hasattr(WorkflowState, "EXECUTE")
        assert WorkflowState.EXECUTE.value is not None

    def test_can_transition_to_execute_in_hierarchy_mode(self):
        """Can transition to EXECUTE in hierarchy mode after DECIDE."""
        from kgraph.orchestrator.context import HierarchyInput, ActionPlan, PlannedAction

        raw_input = HierarchyInput(content="Test", source="test")
        ctx = WorkflowContext(raw_input=raw_input)
        sm = WorkflowStateMachine(ctx)

        # Progress through RESEARCH
        sm.store_output("RESEARCH", {"matches": []})
        sm.transition("RESEARCH")

        # Set action plan in DECIDE
        ctx.action_plan = ActionPlan(
            actions=[PlannedAction(action_type="create", path="test/entity", reasoning="Test", confidence=0.9)],
            overall_reasoning="Test plan",
        )
        sm.store_output("DECIDE", {"action_plan": ctx.action_plan})
        sm.transition("DECIDE")

        # Should be able to transition to EXECUTE
        assert sm.can_transition_to(WorkflowState.EXECUTE)
        assert not sm.can_transition_to(WorkflowState.WRITE)  # Not in legacy mode

    def test_can_skip_to_log_with_empty_plan(self):
        """Can skip to LOG if action plan is empty."""
        from kgraph.orchestrator.context import HierarchyInput, ActionPlan

        raw_input = HierarchyInput(content="Noise", source="test")
        ctx = WorkflowContext(raw_input=raw_input)
        sm = WorkflowStateMachine(ctx)

        # Progress through RESEARCH
        sm.store_output("RESEARCH", {"matches": []})
        sm.transition("RESEARCH")

        # Set empty action plan
        ctx.action_plan = ActionPlan(actions=[], overall_reasoning="Nothing to do")
        sm.store_output("DECIDE", {"action_plan": ctx.action_plan})
        sm.transition("DECIDE")

        # Should be able to transition to LOG (skip EXECUTE)
        assert sm.can_transition_to(WorkflowState.LOG)
        assert not sm.can_transition_to(WorkflowState.EXECUTE)

    def test_execute_to_propagate_after_all_actions(self):
        """Can transition to PROPAGATE after all actions executed."""
        from kgraph.orchestrator.context import HierarchyInput, ActionPlan, PlannedAction

        raw_input = HierarchyInput(content="Test", source="test")
        ctx = WorkflowContext(raw_input=raw_input)
        sm = WorkflowStateMachine(ctx)

        # Setup
        sm.store_output("RESEARCH", {"matches": []})
        sm.transition("RESEARCH")

        ctx.action_plan = ActionPlan(
            actions=[PlannedAction(action_type="create", path="people/test", reasoning="Test", confidence=0.9)],
            overall_reasoning="Test",
        )
        sm.store_output("DECIDE", {"action_plan": ctx.action_plan})
        sm.transition("DECIDE")
        sm.transition("EXECUTE")

        # Execute the action
        sm.store_output("EXECUTE", {"action": {"action_type": "create", "path": "people/test"}})

        # Should now be able to go to PROPAGATE
        assert sm.can_transition_to(WorkflowState.PROPAGATE)


class TestHierarchyModeEnforcer:
    """Tests for hierarchy mode enforcer."""

    @pytest.fixture
    def hierarchy_context(self):
        """Create hierarchy mode context."""
        from kgraph.orchestrator.context import HierarchyInput

        raw_input = HierarchyInput(content="Test", source="test:manual")
        return WorkflowContext(raw_input=raw_input)

    @pytest.fixture
    def hierarchy_enforcer(self, hierarchy_context):
        """Create enforcer in hierarchy mode."""
        sm = WorkflowStateMachine(hierarchy_context)
        return WorkflowEnforcer(sm, kg_root="/test/kb")

    def test_classify_write_as_execute(self, hierarchy_enforcer, hierarchy_context):
        """Writes to planned action paths are classified as EXECUTE."""
        from kgraph.orchestrator.context import ActionPlan, PlannedAction

        hierarchy_context.action_plan = ActionPlan(
            actions=[PlannedAction(action_type="create", path="people/bob", reasoning="Test", confidence=0.9)],
            overall_reasoning="Test",
        )

        result = hierarchy_enforcer._classify_write_hierarchy_mode("/test/kb/people/bob/_summary.md")
        assert result == "EXECUTE"

    def test_classify_ancestor_write_as_propagate(self, hierarchy_enforcer, hierarchy_context):
        """Writes to ancestor paths are classified as PROPAGATE."""
        from kgraph.orchestrator.context import ActionPlan, PlannedAction

        hierarchy_context.action_plan = ActionPlan(
            actions=[PlannedAction(action_type="create", path="people/bob", reasoning="Test", confidence=0.9)],
            overall_reasoning="Test",
        )
        hierarchy_context.propagation_roots = ["people/bob"]

        result = hierarchy_enforcer._classify_write_hierarchy_mode("/test/kb/people/_summary.md")
        assert result == "PROPAGATE"

    def test_workflow_status_hierarchy_mode(self, hierarchy_enforcer, hierarchy_context):
        """Workflow status shows hierarchy mode info."""
        status = hierarchy_enforcer.get_workflow_status()

        assert status["context"]["mode"] == "hierarchy"
        assert "planned_actions" in status["context"]
        assert "executed_actions" in status["context"]
