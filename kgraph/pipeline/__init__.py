"""
kgraph pipeline - Agent orchestration for knowledge graph construction.

This module provides the complete pipeline for processing raw data
into structured knowledge graph entries:

    Raw Data → Extract → Research → Reconcile → Stage → Apply → KG

Main entry point:
    >>> from kgraph.pipeline import Orchestrator
    >>> orchestrator = Orchestrator(config, kg_path)
    >>> result = orchestrator.process(items)

For lower-level access:
    >>> from kgraph.pipeline.agents import ExtractionAgent, ResearchAgent, DecisionAgent
    >>> from kgraph.pipeline.staging import StagingDatabase, QuestionQueue
    >>> from kgraph.pipeline.apply import OperationExecutor
"""

# Main orchestrator
from kgraph.pipeline.orchestrator import Orchestrator, ProcessResult

# Session management
from kgraph.pipeline.session import (
    SessionManager,
    SessionState,
    SessionData,
    BatchInfo,
)

# Checkpoint management
from kgraph.pipeline.checkpoint import (
    CheckpointManager,
    CheckpointData,
    ResumableOperation,
)

# Re-export from submodules for convenience
from kgraph.pipeline.agents import (
    ExtractionAgent,
    MockExtractionAgent,
    ResearchAgent,
    DecisionAgent,
    ExtractedEntity,
    ReconcileDecision,
    AgentContext,
)

from kgraph.pipeline.staging import (
    StagingDatabase,
    QuestionQueue,
    PendingQuestion,
    create_reconcile_question,
)

from kgraph.pipeline.apply import (
    OperationExecutor,
    ExecutionResult,
)

from kgraph.pipeline.audit import (
    AuditLogger,
    log_audit,
    log_error,
    init_audit_logger,
)

from kgraph.pipeline.hooks import (
    HookRegistry,
    PipelineEvent,
    PipelineHook,
    HookError,
    create_logging_hook,
    create_counter_hook,
)

__all__ = [
    # Main orchestrator
    "Orchestrator",
    "ProcessResult",
    # Session management
    "SessionManager",
    "SessionState",
    "SessionData",
    "BatchInfo",
    # Checkpoint management
    "CheckpointManager",
    "CheckpointData",
    "ResumableOperation",
    # Agents
    "ExtractionAgent",
    "MockExtractionAgent",
    "ResearchAgent",
    "DecisionAgent",
    "ExtractedEntity",
    "ReconcileDecision",
    "AgentContext",
    # Staging
    "StagingDatabase",
    "QuestionQueue",
    "PendingQuestion",
    "create_reconcile_question",
    # Apply
    "OperationExecutor",
    "ExecutionResult",
    # Audit
    "AuditLogger",
    "log_audit",
    "log_error",
    "init_audit_logger",
    # Hooks
    "HookRegistry",
    "PipelineEvent",
    "PipelineHook",
    "HookError",
    "create_logging_hook",
    "create_counter_hook",
]
