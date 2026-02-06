"""Tests for ObservabilityLogger."""

import json
import pytest
from pathlib import Path

from kvault.core.observability import ObservabilityLogger, LogEntry


class TestObservabilityLogger:
    """Tests for ObservabilityLogger class."""

    def test_init_creates_database(self, tmp_path):
        """Test that initialization creates database file."""
        db_path = tmp_path / "logs.db"
        logger = ObservabilityLogger(db_path)
        assert db_path.exists()

    def test_new_session(self, tmp_path):
        """Test creating a new session."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        session1 = logger.session_id
        session2 = logger.new_session()

        assert session1 != session2
        assert logger.session_id == session2

    def test_log_basic(self, tmp_path):
        """Test basic logging."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        logger.log("input", {"items": [1, 2, 3]})

        entries = logger.get_session()
        assert len(entries) == 1
        assert entries[0].phase == "input"
        assert entries[0].data["items"] == [1, 2, 3]

    def test_log_invalid_phase(self, tmp_path):
        """Test logging with invalid phase raises error."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        with pytest.raises(ValueError, match="Invalid phase"):
            logger.log("invalid_phase", {})

    def test_log_input(self, tmp_path):
        """Test log_input convenience method."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        logger.log_input([{"name": "Alice"}, {"name": "Bob"}], source="email")

        entries = logger.get_session()
        assert len(entries) == 1
        assert entries[0].data["count"] == 2
        assert entries[0].data["source"] == "email"

    def test_log_research(self, tmp_path):
        """Test log_research convenience method."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        logger.log_research(
            entity="Alice Smith",
            query="alice smith",
            matches=[{"path": "people/alice", "score": 0.95}],
            decision="update",
        )

        entries = logger.get_session()
        assert len(entries) == 1
        assert entries[0].phase == "research"
        assert entries[0].data["entity"] == "Alice Smith"
        assert entries[0].data["match_count"] == 1

    def test_log_decide(self, tmp_path):
        """Test log_decide convenience method."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        logger.log_decide(
            entity="Alice Smith",
            action="create",
            reasoning="No existing match found",
            confidence=0.95,
        )

        entries = logger.get_session()
        assert len(entries) == 1
        assert entries[0].phase == "decide"
        assert entries[0].data["action"] == "create"
        assert entries[0].data["confidence"] == 0.95

    def test_log_write(self, tmp_path):
        """Test log_write convenience method."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        logger.log_write(
            path="people/alice",
            change_type="create",
            diff_summary="Created new entity",
        )

        entries = logger.get_session()
        assert len(entries) == 1
        assert entries[0].phase == "write"
        assert entries[0].data["path"] == "people/alice"

    def test_log_propagate(self, tmp_path):
        """Test log_propagate convenience method."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        logger.log_propagate(
            from_path="people/collaborators/alice",
            updated_paths=["people/collaborators", "people"],
            reasoning="Updated parent summaries",
        )

        entries = logger.get_session()
        assert len(entries) == 1
        assert entries[0].phase == "propagate"
        assert entries[0].data["propagation_depth"] == 2

    def test_log_error(self, tmp_path):
        """Test log_error convenience method."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        logger.log_error(
            error_type="duplicate_entity",
            entity="Alice Smith",
            details={"existing_path": "people/alice"},
            resolution="merged with existing",
        )

        entries = logger.get_session()
        assert len(entries) == 1
        assert entries[0].phase == "error"
        assert entries[0].data["error_type"] == "duplicate_entity"

    def test_get_session_specific(self, tmp_path):
        """Test getting logs for specific session."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        session1 = logger.session_id
        logger.log("input", {"session": 1})

        logger.new_session()
        logger.log("input", {"session": 2})

        session1_entries = logger.get_session(session1)
        assert len(session1_entries) == 1
        assert session1_entries[0].data["session"] == 1

    def test_get_errors(self, tmp_path):
        """Test getting error logs."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        logger.log_error("error1", entity="e1")
        logger.log_decide("entity", "create", "reason")
        logger.log_error("error2", entity="e2")

        errors = logger.get_errors()
        assert len(errors) == 2

    def test_get_decisions(self, tmp_path):
        """Test getting decision logs."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        logger.log_decide("e1", "create", "reason")
        logger.log_decide("e2", "update", "reason")
        logger.log_decide("e3", "create", "reason")

        # All decisions
        decisions = logger.get_decisions()
        assert len(decisions) == 3

        # Filter by action
        creates = logger.get_decisions(action="create")
        assert len(creates) == 2

    def test_get_low_confidence(self, tmp_path):
        """Test getting low confidence decisions."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        logger.log_decide("e1", "create", "reason", confidence=0.95)
        logger.log_decide("e2", "review", "reason", confidence=0.65)
        logger.log_decide("e3", "review", "reason", confidence=0.50)

        low_conf = logger.get_low_confidence(threshold=0.7)
        assert len(low_conf) == 2

    def test_get_session_summary(self, tmp_path):
        """Test getting session summary."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        logger.log_input([{"name": "a"}])
        logger.log_research("a", "a", [], "create")
        logger.log_decide("a", "create", "reason")
        logger.log_write("path/a", "create", "created")
        logger.log_error("minor_error")

        summary = logger.get_session_summary()

        assert summary["total_logs"] == 5
        assert summary["phase_counts"]["input"] == 1
        assert summary["phase_counts"]["decide"] == 1
        assert summary["error_count"] == 1
        assert summary["action_counts"]["create"] == 1

    def test_multiple_sessions_isolation(self, tmp_path):
        """Test that sessions are properly isolated."""
        logger = ObservabilityLogger(tmp_path / "logs.db")

        logger.log("input", {"data": "session1"})
        logger.log("input", {"data": "session1-2"})

        session1_id = logger.session_id
        logger.new_session()

        logger.log("input", {"data": "session2"})

        session1_entries = logger.get_session(session1_id)
        session2_entries = logger.get_session()

        assert len(session1_entries) == 2
        assert len(session2_entries) == 1
