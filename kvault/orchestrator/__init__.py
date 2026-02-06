"""
kvault.orchestrator - Headless Claude Code orchestrator for mandatory workflow.

Provides hard enforcement of the 6-step kvault workflow via Agent SDK hooks:
1. RESEARCH - Query index for existing entities
2. DECIDE - Determine action (create/update/skip)
3. WRITE - Create/update entity files
4. PROPAGATE - Update ancestor summaries
5. LOG - Add journal entry
6. REBUILD - Rebuild index if entity created

Plus stochastic refactoring: Bernoulli(p) triggers cleanup opportunities.
"""

from kvault.orchestrator.context import WorkflowContext, OrchestratorConfig
from kvault.orchestrator.state_machine import WorkflowStateMachine, WorkflowState
from kvault.orchestrator.enforcer import WorkflowEnforcer
from kvault.orchestrator.runner import HeadlessOrchestrator

__all__ = [
    "WorkflowContext",
    "OrchestratorConfig",
    "WorkflowStateMachine",
    "WorkflowState",
    "WorkflowEnforcer",
    "HeadlessOrchestrator",
]
