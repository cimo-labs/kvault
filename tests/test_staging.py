"""
Unit tests for staging layer.

Tests StagingDatabase and QuestionQueue:
- Operation staging and retrieval
- Status transitions
- Question creation and answering
- Batch operations
"""

from pathlib import Path
from typing import Any, Dict

import pytest

from kgraph.pipeline import StagingDatabase, QuestionQueue


class TestStagingDatabase:
    """Tests for StagingDatabase."""

    def test_create_database(self, staging_db: StagingDatabase) -> None:
        """Database is created with correct schema."""
        # Check we can get counts (tables exist)
        counts = staging_db.count_by_status()
        assert isinstance(counts, dict)

    def test_stage_operation(self, staging_db: StagingDatabase) -> None:
        """Stage a new operation."""
        op_id = staging_db.stage_operation(
            batch_id="batch_001",
            entity_name="Test Corporation",
            action="create",
            entity_data={"name": "Test Corporation", "industry": "tech"},
            confidence=0.85,
            reasoning="New entity, no matches found",
        )

        assert op_id > 0

        # Retrieve and verify
        op = staging_db.get_operation(op_id)
        assert op is not None
        assert op["entity_name"] == "Test Corporation"
        assert op["action"] == "create"
        assert op["confidence"] == 0.85
        assert op["status"] == "staged"

    def test_stage_merge_operation(self, staging_db: StagingDatabase) -> None:
        """Stage a merge operation with target path."""
        op_id = staging_db.stage_operation(
            batch_id="batch_002",
            entity_name="ACME Corp",
            action="merge",
            entity_data={"name": "ACME Corp"},
            confidence=0.95,
            reasoning="Fuzzy match to existing Acme Corporation",
            target_path="customers/strategic/acme_corporation",
            candidates=[
                {"path": "customers/strategic/acme_corporation", "score": 0.95}
            ],
        )

        op = staging_db.get_operation(op_id)

        assert op["action"] == "merge"
        assert op["target_path"] == "customers/strategic/acme_corporation"
        assert len(op["candidates_data"]) == 1

    def test_get_nonexistent_operation(self, staging_db: StagingDatabase) -> None:
        """Get operation that doesn't exist returns None."""
        op = staging_db.get_operation(99999)
        assert op is None

    def test_update_status(self, staging_db: StagingDatabase) -> None:
        """Update operation status."""
        op_id = staging_db.stage_operation(
            batch_id="batch_003",
            entity_name="Status Test",
            action="create",
            entity_data={"name": "Status Test"},
            confidence=0.8,
        )

        # Update to ready
        staging_db.update_status(op_id, "ready")
        op = staging_db.get_operation(op_id)
        assert op["status"] == "ready"

        # Update to applied
        staging_db.update_status(op_id, "applied")
        op = staging_db.get_operation(op_id)
        assert op["status"] == "applied"
        assert op["applied_at"] is not None

    def test_update_status_with_error(self, staging_db: StagingDatabase) -> None:
        """Update status with error message."""
        op_id = staging_db.stage_operation(
            batch_id="batch_004",
            entity_name="Error Test",
            action="create",
            entity_data={"name": "Error Test"},
            confidence=0.7,
        )

        staging_db.update_status(op_id, "failed", error_message="Validation error")
        op = staging_db.get_operation(op_id)

        assert op["status"] == "failed"
        assert op["error_message"] == "Validation error"

    def test_get_ready_operations(self, staging_db: StagingDatabase) -> None:
        """Get operations ready for execution."""
        batch = "batch_005"

        # Stage multiple operations
        staging_db.stage_operation(
            batch_id=batch,
            entity_name="Create Op",
            action="create",
            entity_data={},
            confidence=0.8,
            status="ready",
        )
        staging_db.stage_operation(
            batch_id=batch,
            entity_name="Merge Op",
            action="merge",
            entity_data={},
            confidence=0.95,
            status="ready",
            target_path="some/path",
        )
        staging_db.stage_operation(
            batch_id=batch,
            entity_name="Pending Op",
            action="create",
            entity_data={},
            confidence=0.6,
            status="pending_review",  # Not ready
        )

        ready = staging_db.get_ready_operations(batch_id=batch)

        assert len(ready) == 2
        # Should be ordered by priority (merge before create)
        assert ready[0]["action"] == "merge"
        assert ready[1]["action"] == "create"

    def test_get_batch_operations(self, staging_db: StagingDatabase) -> None:
        """Get all operations for a batch."""
        batch = "batch_006"

        staging_db.stage_operation(
            batch_id=batch,
            entity_name="Op 1",
            action="create",
            entity_data={},
            confidence=0.8,
        )
        staging_db.stage_operation(
            batch_id=batch,
            entity_name="Op 2",
            action="update",
            entity_data={},
            confidence=0.9,
            target_path="path",
        )
        staging_db.stage_operation(
            batch_id="other_batch",
            entity_name="Op 3",
            action="create",
            entity_data={},
            confidence=0.7,
        )

        ops = staging_db.get_batch_operations(batch)

        assert len(ops) == 2
        # Check both are from the correct batch
        for op in ops:
            assert op["batch_id"] == batch

    def test_get_batch_operations_by_status(self, staging_db: StagingDatabase) -> None:
        """Filter batch operations by status."""
        batch = "batch_007"

        staging_db.stage_operation(
            batch_id=batch,
            entity_name="Ready Op",
            action="create",
            entity_data={},
            confidence=0.8,
            status="ready",
        )
        staging_db.stage_operation(
            batch_id=batch,
            entity_name="Staged Op",
            action="create",
            entity_data={},
            confidence=0.7,
            status="staged",
        )

        ready_ops = staging_db.get_batch_operations(batch, status="ready")
        staged_ops = staging_db.get_batch_operations(batch, status="staged")

        assert len(ready_ops) == 1
        assert ready_ops[0]["entity_name"] == "Ready Op"
        assert len(staged_ops) == 1
        assert staged_ops[0]["entity_name"] == "Staged Op"

    def test_count_by_status(self, staging_db: StagingDatabase) -> None:
        """Count operations by status."""
        batch = "batch_008"

        # Stage with different statuses
        staging_db.stage_operation(
            batch_id=batch,
            entity_name="A",
            action="create",
            entity_data={},
            confidence=0.8,
            status="ready",
        )
        staging_db.stage_operation(
            batch_id=batch,
            entity_name="B",
            action="create",
            entity_data={},
            confidence=0.7,
            status="ready",
        )
        staging_db.stage_operation(
            batch_id=batch,
            entity_name="C",
            action="create",
            entity_data={},
            confidence=0.6,
            status="staged",
        )

        counts = staging_db.count_by_status()

        assert counts.get("ready", 0) >= 2
        assert counts.get("staged", 0) >= 1

    def test_count_by_batch(self, staging_db: StagingDatabase) -> None:
        """Count operations by status within a batch."""
        batch = "batch_009"

        staging_db.stage_operation(
            batch_id=batch,
            entity_name="X",
            action="create",
            entity_data={},
            confidence=0.8,
            status="applied",
        )
        staging_db.stage_operation(
            batch_id=batch,
            entity_name="Y",
            action="create",
            entity_data={},
            confidence=0.7,
            status="failed",
        )

        counts = staging_db.count_by_batch(batch)

        assert counts.get("applied", 0) == 1
        assert counts.get("failed", 0) == 1

    def test_get_recent_batches(self, staging_db: StagingDatabase) -> None:
        """Get summary of recent batches."""
        # Create operations in different batches
        staging_db.stage_operation(
            batch_id="recent_a",
            entity_name="A",
            action="create",
            entity_data={},
            confidence=0.8,
        )
        staging_db.stage_operation(
            batch_id="recent_b",
            entity_name="B",
            action="create",
            entity_data={},
            confidence=0.7,
        )

        batches = staging_db.get_recent_batches(limit=10)

        assert len(batches) >= 2
        # Each batch should have summary fields
        for batch in batches:
            assert "batch_id" in batch
            assert "total" in batch

    def test_priority_ordering(self, staging_db: StagingDatabase) -> None:
        """Operations are ordered by priority: merge < update < create."""
        batch = "batch_priority"

        # Stage in reverse priority order
        staging_db.stage_operation(
            batch_id=batch,
            entity_name="Create",
            action="create",
            entity_data={},
            confidence=0.8,
            status="ready",
        )
        staging_db.stage_operation(
            batch_id=batch,
            entity_name="Update",
            action="update",
            entity_data={},
            confidence=0.85,
            status="ready",
            target_path="path",
        )
        staging_db.stage_operation(
            batch_id=batch,
            entity_name="Merge",
            action="merge",
            entity_data={},
            confidence=0.95,
            status="ready",
            target_path="path",
        )

        ready = staging_db.get_ready_operations(batch_id=batch)

        assert len(ready) == 3
        assert ready[0]["action"] == "merge"   # Priority 1
        assert ready[1]["action"] == "update"  # Priority 2
        assert ready[2]["action"] == "create"  # Priority 3


class TestQuestionQueue:
    """Tests for QuestionQueue."""

    def test_add_question(
        self,
        staging_db: StagingDatabase,
        question_queue: QuestionQueue,
    ) -> None:
        """Add a question for review."""
        # First stage an operation
        op_id = staging_db.stage_operation(
            batch_id="q_batch_001",
            entity_name="Ambiguous Corp",
            action="merge",
            entity_data={"name": "Ambiguous Corp"},
            confidence=0.65,
            status="pending_review",
        )

        # Create question for it
        q_id = question_queue.add_question(
            batch_id="q_batch_001",
            staged_op_id=op_id,
            question_type="confirm_merge",
            question_text="Should 'Ambiguous Corp' merge with 'Ambiguous Corporation'?",
            suggested_action="merge",
            confidence=0.65,
        )

        assert q_id > 0

        # Retrieve and verify
        question = question_queue.get_question(q_id)
        assert question is not None
        assert question.question_type == "confirm_merge"
        assert question.status == "pending"

    def test_get_pending(
        self,
        staging_db: StagingDatabase,
        question_queue: QuestionQueue,
    ) -> None:
        """Get pending questions for a batch."""
        batch = "q_batch_002"

        # Create staged operations
        op1 = staging_db.stage_operation(
            batch_id=batch,
            entity_name="Q1",
            action="merge",
            entity_data={},
            confidence=0.6,
            status="pending_review",
        )
        op2 = staging_db.stage_operation(
            batch_id=batch,
            entity_name="Q2",
            action="merge",
            entity_data={},
            confidence=0.7,
            status="pending_review",
        )

        # Create questions
        question_queue.add_question(
            batch_id=batch,
            staged_op_id=op1,
            question_type="confirm",
            question_text="Q1?",
        )
        question_queue.add_question(
            batch_id=batch,
            staged_op_id=op2,
            question_type="confirm",
            question_text="Q2?",
        )

        pending = question_queue.get_pending(batch_id=batch)

        assert len(pending) == 2

    def test_answer(
        self,
        staging_db: StagingDatabase,
        question_queue: QuestionQueue,
    ) -> None:
        """Answer a pending question."""
        batch = "q_batch_003"

        op_id = staging_db.stage_operation(
            batch_id=batch,
            entity_name="ToAnswer",
            action="merge",
            entity_data={},
            confidence=0.65,
            status="pending_review",
        )

        q_id = question_queue.add_question(
            batch_id=batch,
            staged_op_id=op_id,
            question_type="confirm",
            question_text="Confirm merge?",
        )

        # Answer the question
        question_queue.answer(q_id, "approve")

        question = question_queue.get_question(q_id)
        assert question.status == "answered"
        assert question.user_answer == "approve"

    def test_skip(
        self,
        staging_db: StagingDatabase,
        question_queue: QuestionQueue,
    ) -> None:
        """Skip a pending question."""
        batch = "q_batch_004"

        op_id = staging_db.stage_operation(
            batch_id=batch,
            entity_name="ToSkip",
            action="create",
            entity_data={},
            confidence=0.55,
            status="pending_review",
        )

        q_id = question_queue.add_question(
            batch_id=batch,
            staged_op_id=op_id,
            question_type="confirm",
            question_text="Confirm create?",
        )

        # Skip the question
        question_queue.skip(q_id)

        question = question_queue.get_question(q_id)
        assert question.status == "skipped"

    def test_count_pending(
        self,
        staging_db: StagingDatabase,
        question_queue: QuestionQueue,
    ) -> None:
        """Count pending questions."""
        batch = "q_batch_005"

        for i in range(3):
            op_id = staging_db.stage_operation(
                batch_id=batch,
                entity_name=f"Entity {i}",
                action="create",
                entity_data={},
                confidence=0.5,
                status="pending_review",
            )
            question_queue.add_question(
                batch_id=batch,
                staged_op_id=op_id,
                question_type="confirm",
                question_text=f"Question {i}?",
            )

        count = question_queue.count_pending(batch_id=batch)
        assert count == 3

    def test_question_priority(
        self,
        staging_db: StagingDatabase,
        question_queue: QuestionQueue,
    ) -> None:
        """Questions are ordered by priority (lower confidence = lower priority number = first)."""
        batch = "q_batch_006"

        # Create questions with different confidences
        # Lower confidence -> higher urgency -> lower priority number
        op1 = staging_db.stage_operation(
            batch_id=batch,
            entity_name="High Confidence",
            action="create",
            entity_data={},
            confidence=0.9,
            status="pending_review",
        )
        op2 = staging_db.stage_operation(
            batch_id=batch,
            entity_name="Low Confidence",
            action="merge",
            entity_data={},
            confidence=0.3,
            status="pending_review",
        )

        # High confidence = priority 90
        question_queue.add_question(
            batch_id=batch,
            staged_op_id=op1,
            question_type="confirm",
            question_text="High confidence?",
            confidence=0.9,
        )
        # Low confidence = priority 30 (more urgent, comes first)
        question_queue.add_question(
            batch_id=batch,
            staged_op_id=op2,
            question_type="confirm",
            question_text="Low confidence?",
            confidence=0.3,
        )

        pending = question_queue.get_pending(batch_id=batch)

        # Lower priority number (lower confidence) should come first
        assert len(pending) == 2
        assert pending[0].question_text == "Low confidence?"
