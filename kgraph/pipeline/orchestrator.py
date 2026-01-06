"""
Pipeline orchestrator for kgraph.

Coordinates the end-to-end flow from raw data to knowledge graph:
1. EXTRACT: Extract entities from raw data using LLM
2. RESEARCH: Find existing matches in knowledge graph
3. RECONCILE: Decide merge/update/create actions
4. STAGE: Queue operations for execution
5. APPLY: Execute operations against knowledge graph

The orchestrator manages:
- Session lifecycle
- Batch processing
- Checkpoints for resume/recovery
- Human review integration
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kgraph.core.config import KGraphConfig
from kgraph.core.storage import FilesystemStorage
from kgraph.pipeline.agents import (
    ExtractionAgent,
    ResearchAgent,
    DecisionAgent,
    ExtractedEntity,
    ReconcileDecision,
    AgentContext,
)
from kgraph.pipeline.staging import (
    StagingDatabase,
    QuestionQueue,
    create_reconcile_question,
)
from kgraph.pipeline.apply import OperationExecutor, ExecutionResult
from kgraph.pipeline.session import SessionManager, SessionState
from kgraph.pipeline.checkpoint import CheckpointManager, ResumableOperation
from kgraph.pipeline.audit import log_audit, log_error, init_audit_logger
from kgraph.pipeline.hooks import HookRegistry, PipelineEvent


@dataclass
class ProcessResult:
    """Result of processing a batch of items."""

    batch_id: str
    """Batch identifier"""

    items_processed: int
    """Number of items processed"""

    entities_extracted: int
    """Number of entities extracted"""

    operations_staged: int
    """Number of operations staged"""

    operations_applied: int
    """Number of operations applied"""

    operations_failed: int
    """Number of operations that failed"""

    questions_created: int
    """Number of questions created for review"""

    errors: List[str]
    """List of error messages"""


class Orchestrator:
    """
    Main pipeline orchestrator.

    Coordinates agents and manages the processing flow.
    """

    def __init__(
        self,
        config: KGraphConfig,
        kg_path: Path,
        data_dir: Optional[Path] = None,
        hooks: Optional[HookRegistry] = None,
    ):
        """
        Initialize orchestrator.

        Args:
            config: KGraph configuration
            kg_path: Path to knowledge graph root
            data_dir: Optional path to data directory (for sessions, staging, etc.)
            hooks: Optional hook registry for event callbacks
        """
        self.config = config
        self.kg_path = Path(kg_path)
        self.data_dir = data_dir or (self.kg_path / ".kgraph")

        # Initialize hook registry
        self.hooks = hooks or HookRegistry()

        # Ensure data directories exist
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Initialize storage
        self.storage = FilesystemStorage(kg_path, config)

        # Initialize staging database
        self.staging_db = StagingDatabase(self.data_dir / "staging.db")
        self.question_queue = QuestionQueue(self.data_dir / "staging.db")

        # Initialize agents
        self.extraction_agent = ExtractionAgent(config)
        self.research_agent = ResearchAgent(config, self.storage)
        self.decision_agent = DecisionAgent(config)

        # Initialize executor (pass hooks for entity events)
        self.executor = OperationExecutor(
            config, self.storage, self.staging_db, hooks=self.hooks
        )

        # Initialize session and checkpoint managers
        self.session_manager = SessionManager(self.data_dir / "sessions")
        self.checkpoint_manager = CheckpointManager(self.data_dir / "checkpoints")

        # Initialize audit logger
        init_audit_logger(self.data_dir / "audit")

    def process(
        self,
        items: List[Dict[str, Any]],
        source_name: Optional[str] = None,
        auto_apply: bool = False,
        use_llm: bool = True,
        batch_size: int = 10,
    ) -> ProcessResult:
        """
        Process raw items through the full pipeline.

        Args:
            items: Raw data items to process (e.g., emails, documents)
            source_name: Optional source identifier
            auto_apply: If True, automatically apply ready operations
            use_llm: If True, use LLM for ambiguous decisions
            batch_size: Number of items per extraction batch

        Returns:
            ProcessResult with summary statistics
        """
        # Create session
        session = self.session_manager.create_session(
            kg_path=str(self.kg_path),
            metadata={"source": source_name, "items_count": len(items)},
        )

        # Emit session start event
        self.hooks.emit_simple(
            "session_start",
            {
                "session_id": session.session_id,
                "kg_path": str(self.kg_path),
                "source": source_name,
                "items_count": len(items),
            },
            session_id=session.session_id,
        )

        # Start batch
        batch_id = self.session_manager.start_batch(
            source_file=source_name,
            items_total=len(items),
        )

        # Emit batch start event
        self.hooks.emit_simple(
            "batch_start",
            {
                "items_total": len(items),
                "source": source_name,
            },
            batch_id=batch_id,
            session_id=session.session_id,
        )

        result = ProcessResult(
            batch_id=batch_id,
            items_processed=0,
            entities_extracted=0,
            operations_staged=0,
            operations_applied=0,
            operations_failed=0,
            questions_created=0,
            errors=[],
        )

        try:
            # Phase 1: Extract entities
            self.session_manager.update_state(SessionState.EXTRACTING)
            entities = self._extract_phase(items, batch_id, batch_size)
            result.items_processed = len(items)
            result.entities_extracted = len(entities)

            if not entities:
                self.session_manager.complete_batch(batch_id)
                self.session_manager.complete_session()
                return result

            # Phase 2: Research existing matches
            self.session_manager.update_state(SessionState.RESEARCHING)
            entities_with_candidates = self._research_phase(entities)

            # Phase 3: Reconcile (decide actions)
            self.session_manager.update_state(SessionState.RECONCILING)
            decisions = self._reconcile_phase(
                entities_with_candidates,
                use_llm=use_llm,
            )

            # Phase 4: Stage operations
            self.session_manager.update_state(SessionState.STAGING)
            staged, questions = self._staging_phase(decisions, batch_id)
            result.operations_staged = staged
            result.questions_created = questions

            # Phase 5: Apply (if auto_apply)
            if auto_apply:
                self.session_manager.update_state(SessionState.APPLYING)
                summary = self.executor.execute_batch(batch_id=batch_id)
                result.operations_applied = summary.successful
                result.operations_failed = summary.failed
                result.errors.extend(summary.errors)

            # Update session stats
            self.session_manager.update_stats(
                operations_staged=result.operations_staged,
                operations_applied=result.operations_applied,
                operations_failed=result.operations_failed,
                questions_pending=self.question_queue.count_pending(batch_id),
            )

            # Complete or pause session
            if questions > 0:
                self.session_manager.update_state(SessionState.REVIEWING)
            else:
                self.session_manager.complete_batch(batch_id)
                self.session_manager.complete_session()

                # Emit batch and session complete events
                self.hooks.emit_simple(
                    "batch_complete",
                    {
                        "items_processed": result.items_processed,
                        "entities_extracted": result.entities_extracted,
                        "operations_staged": result.operations_staged,
                        "operations_applied": result.operations_applied,
                    },
                    batch_id=batch_id,
                    session_id=session.session_id,
                )
                self.hooks.emit_simple(
                    "session_complete",
                    {
                        "session_id": session.session_id,
                        "items_processed": result.items_processed,
                        "entities_extracted": result.entities_extracted,
                        "operations_applied": result.operations_applied,
                    },
                    session_id=session.session_id,
                )

        except Exception as e:
            log_error(e, {"batch_id": batch_id})
            result.errors.append(str(e))
            self.session_manager.fail_session(str(e))

            # Emit session failed event
            self.hooks.emit_simple(
                "session_failed",
                {
                    "session_id": session.session_id,
                    "error": str(e),
                },
                batch_id=batch_id,
                session_id=session.session_id,
            )

        return result

    def resume(
        self,
        session_id: str,
        auto_apply: bool = False,
    ) -> Optional[ProcessResult]:
        """
        Resume a paused or interrupted session.

        Args:
            session_id: Session to resume
            auto_apply: If True, automatically apply ready operations

        Returns:
            ProcessResult or None if session not found
        """
        session = self.session_manager.load_session(session_id)
        if not session:
            return None

        # Get latest checkpoint
        checkpoint = self.checkpoint_manager.get_latest_checkpoint(session_id)

        batch_id = session.current_batch_id or "resume"

        result = ProcessResult(
            batch_id=batch_id,
            items_processed=checkpoint.items_processed if checkpoint else 0,
            entities_extracted=checkpoint.entities_extracted if checkpoint else 0,
            operations_staged=checkpoint.operations_staged if checkpoint else 0,
            operations_applied=0,
            operations_failed=0,
            questions_created=0,
            errors=[],
        )

        try:
            state = SessionState(session.state)

            # Resume from appropriate phase
            if state == SessionState.REVIEWING:
                # Check if there are still pending questions
                pending = self.question_queue.count_pending(batch_id)
                if pending > 0:
                    return result  # Still need human review

                # All questions answered, continue to apply
                state = SessionState.STAGING

            if state in (SessionState.STAGING, SessionState.APPLYING):
                # Apply ready operations
                self.session_manager.update_state(SessionState.APPLYING)
                summary = self.executor.execute_batch(batch_id=batch_id)
                result.operations_applied = summary.successful
                result.operations_failed = summary.failed
                result.errors.extend(summary.errors)

                # Check for remaining questions
                pending = self.question_queue.count_pending(batch_id)
                if pending > 0:
                    self.session_manager.update_state(SessionState.REVIEWING)
                else:
                    self.session_manager.complete_batch(batch_id)
                    self.session_manager.complete_session()

        except Exception as e:
            log_error(e, {"session_id": session_id})
            result.errors.append(str(e))
            self.session_manager.fail_session(str(e))

        return result

    def review_next(
        self,
        batch_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get the next question for human review.

        Args:
            batch_id: Optional batch filter

        Returns:
            Question data or None if queue is empty
        """
        question = self.question_queue.get_next(batch_id)
        if not question:
            return None

        # Get linked operation for context
        op_data = None
        if question.staged_op_id:
            op_data = self.staging_db.get_operation(question.staged_op_id)

        return {
            "question_id": question.id,
            "batch_id": question.batch_id,
            "type": question.question_type,
            "text": question.question_text,
            "suggested": question.suggested_action,
            "confidence": question.confidence,
            "context": question.context,
            "operation": op_data,
        }

    def answer_question(
        self,
        question_id: int,
        answer: str,
    ) -> bool:
        """
        Answer a review question.

        Args:
            question_id: Question ID
            answer: User's answer

        Returns:
            True if answered successfully
        """
        success = self.question_queue.answer(question_id, answer)

        if success:
            # Update session stats
            session = self.session_manager.current
            if session:
                self.session_manager.update_stats(
                    questions_pending=self.question_queue.count_pending(),
                    questions_answered=session.questions_answered + 1,
                )

            # Emit question answered event
            self.hooks.emit_simple(
                "question_answered",
                {
                    "question_id": question_id,
                    "answer": answer,
                },
                session_id=session.session_id if session else None,
            )

        return success

    def get_status(self) -> Dict[str, Any]:
        """
        Get current pipeline status.

        Returns:
            Status summary
        """
        session = self.session_manager.current
        staging_counts = self.staging_db.count_by_status()
        question_counts = self.question_queue.count_by_status()

        return {
            "session": {
                "id": session.session_id if session else None,
                "state": session.state if session else None,
                "batches": len(session.batches) if session else 0,
                "entities_extracted": session.total_entities_extracted if session else 0,
            },
            "staging": staging_counts,
            "questions": question_counts,
            "index_size": self.research_agent.get_index_size(),
        }

    def _extract_phase(
        self,
        items: List[Dict[str, Any]],
        batch_id: str,
        batch_size: int,
    ) -> List[ExtractedEntity]:
        """Run extraction phase."""
        all_entities: List[ExtractedEntity] = []

        # Create agent context
        context = AgentContext(
            session_id=self.session_manager.current.session_id if self.session_manager.current else "unknown",
            batch_id=batch_id,
            config=self.config,
            prompts_path=self.config.prompts_path,
        )

        # Process in batches
        for i in range(0, len(items), batch_size):
            batch_items = items[i : i + batch_size]

            entities = self.extraction_agent.extract(batch_items, context)
            all_entities.extend(entities)

            # Update batch progress
            self.session_manager.update_batch(
                items_processed=min(i + batch_size, len(items)),
                entities_extracted=len(all_entities),
            )

            log_audit(
                "extract",
                "batch_complete",
                {
                    "batch_id": batch_id,
                    "items_start": i,
                    "items_end": min(i + batch_size, len(items)),
                    "entities_found": len(entities),
                },
            )

        return all_entities

    def _research_phase(
        self,
        entities: List[ExtractedEntity],
    ) -> List[Tuple[ExtractedEntity, List]]:
        """Run research phase."""
        return self.research_agent.research_batch(entities)

    def _reconcile_phase(
        self,
        entities_with_candidates: List[Tuple[ExtractedEntity, List]],
        use_llm: bool = True,
    ) -> List[ReconcileDecision]:
        """Run reconciliation phase."""
        return self.decision_agent.reconcile(
            entities_with_candidates,
            use_llm=use_llm,
        )

    def _staging_phase(
        self,
        decisions: List[ReconcileDecision],
        batch_id: str,
    ) -> Tuple[int, int]:
        """
        Stage operations and create questions for review.

        Returns:
            Tuple of (operations_staged, questions_created)
        """
        staged_count = 0
        question_count = 0

        for decision in decisions:
            # Determine initial status
            if decision.needs_review:
                status = "pending_review"
            else:
                status = "ready"

            # Stage the operation
            entity_data = decision.source_entity.to_dict() if decision.source_entity else {}
            candidates_data = [c.to_dict() for c in decision.candidates]

            op_id = self.staging_db.stage_operation(
                batch_id=batch_id,
                entity_name=decision.entity_name,
                action=decision.action,
                entity_data=entity_data,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                target_path=decision.target_path,
                candidates=candidates_data,
                status=status,
            )
            staged_count += 1

            # Create question if needs review
            if decision.needs_review:
                question = create_reconcile_question(
                    batch_id=batch_id,
                    entity_name=decision.entity_name,
                    candidates=candidates_data,
                    confidence=decision.confidence,
                    staged_op_id=op_id,
                )
                self.question_queue.add_question(
                    batch_id=question.batch_id,
                    question_type=question.question_type,
                    question_text=question.question_text,
                    staged_op_id=op_id,
                    context=question.context,
                    suggested_action=question.suggested_action,
                    confidence=question.confidence,
                )
                question_count += 1

                # Emit question created event
                self.hooks.emit_simple(
                    "question_created",
                    {
                        "entity_name": decision.entity_name,
                        "question_type": question.question_type,
                        "confidence": decision.confidence,
                        "candidates_count": len(candidates_data),
                    },
                    batch_id=batch_id,
                )

        return staged_count, question_count

    def invalidate_research_cache(self) -> None:
        """Invalidate the research agent's entity cache."""
        self.research_agent.invalidate_cache()
