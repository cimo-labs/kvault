"""Tests for the hook system."""

import pytest
from datetime import datetime

from kgraph.pipeline.hooks import (
    HookRegistry,
    PipelineEvent,
    HookError,
    create_logging_hook,
    create_counter_hook,
)


class TestPipelineEvent:
    """Tests for PipelineEvent dataclass."""

    def test_create_event(self):
        """Test creating a basic event."""
        event = PipelineEvent(
            event_type="entity_created",
            data={"entity_name": "Acme Corp"},
            timestamp="2024-01-15T10:30:00",
        )

        assert event.event_type == "entity_created"
        assert event.data["entity_name"] == "Acme Corp"
        assert event.timestamp == "2024-01-15T10:30:00"
        assert event.batch_id is None
        assert event.session_id is None

    def test_event_with_ids(self):
        """Test event with batch and session IDs."""
        event = PipelineEvent(
            event_type="batch_complete",
            data={"count": 10},
            timestamp="2024-01-15T10:30:00",
            batch_id="batch_123",
            session_id="session_456",
        )

        assert event.batch_id == "batch_123"
        assert event.session_id == "session_456"

    def test_event_to_dict(self):
        """Test event serialization."""
        event = PipelineEvent(
            event_type="entity_merged",
            data={"source": "a", "target": "b"},
            timestamp="2024-01-15T10:30:00",
            batch_id="batch_123",
        )

        d = event.to_dict()
        assert d["event_type"] == "entity_merged"
        assert d["data"] == {"source": "a", "target": "b"}
        assert d["batch_id"] == "batch_123"
        assert d["session_id"] is None


class TestHookRegistry:
    """Tests for HookRegistry."""

    def test_register_valid_event(self):
        """Test registering a hook for a valid event."""
        registry = HookRegistry()
        called = []

        def my_hook(event: PipelineEvent):
            called.append(event)

        registry.register("entity_created", my_hook)

        assert len(registry.get_hooks("entity_created")) == 1

    def test_register_invalid_event(self):
        """Test registering for an invalid event type raises error."""
        registry = HookRegistry()

        with pytest.raises(ValueError, match="Unknown event type"):
            registry.register("invalid_event", lambda e: None)

    def test_emit_calls_hooks(self):
        """Test that emit calls all registered hooks."""
        registry = HookRegistry()
        results = []

        def hook1(event: PipelineEvent):
            results.append(("hook1", event.data))

        def hook2(event: PipelineEvent):
            results.append(("hook2", event.data))

        registry.register("entity_created", hook1)
        registry.register("entity_created", hook2)

        event = PipelineEvent(
            event_type="entity_created",
            data={"name": "Test"},
            timestamp="2024-01-15T10:30:00",
        )
        registry.emit(event)

        assert len(results) == 2
        assert ("hook1", {"name": "Test"}) in results
        assert ("hook2", {"name": "Test"}) in results

    def test_emit_only_matching_event_type(self):
        """Test that emit only calls hooks for matching event type."""
        registry = HookRegistry()
        results = []

        def created_hook(event: PipelineEvent):
            results.append("created")

        def merged_hook(event: PipelineEvent):
            results.append("merged")

        registry.register("entity_created", created_hook)
        registry.register("entity_merged", merged_hook)

        event = PipelineEvent(
            event_type="entity_created",
            data={},
            timestamp="2024-01-15T10:30:00",
        )
        registry.emit(event)

        assert results == ["created"]

    def test_emit_simple(self):
        """Test convenience emit_simple method."""
        registry = HookRegistry()
        captured = []

        def my_hook(event: PipelineEvent):
            captured.append(event)

        registry.register("batch_complete", my_hook)

        registry.emit_simple(
            "batch_complete",
            {"items": 10},
            batch_id="batch_123",
        )

        assert len(captured) == 1
        assert captured[0].event_type == "batch_complete"
        assert captured[0].data == {"items": 10}
        assert captured[0].batch_id == "batch_123"
        assert captured[0].timestamp  # Should be set automatically

    def test_hook_error_handling(self):
        """Test that hook errors don't stop execution."""
        registry = HookRegistry()
        results = []

        def failing_hook(event: PipelineEvent):
            raise ValueError("Hook failed!")

        def working_hook(event: PipelineEvent):
            results.append("working")

        registry.register("entity_created", failing_hook)
        registry.register("entity_created", working_hook)

        event = PipelineEvent(
            event_type="entity_created",
            data={},
            timestamp="2024-01-15T10:30:00",
        )

        # Should not raise
        registry.emit(event)

        # Working hook should still be called
        assert results == ["working"]

        # Error should be recorded
        errors = registry.get_errors()
        assert len(errors) == 1
        assert errors[0].event_type == "entity_created"
        assert "Hook failed!" in errors[0].error

    def test_error_handler_callback(self):
        """Test custom error handler is called."""
        handled_errors = []

        def error_handler(error: HookError):
            handled_errors.append(error)

        registry = HookRegistry(error_handler=error_handler)

        def failing_hook(event: PipelineEvent):
            raise RuntimeError("Oops!")

        registry.register("entity_created", failing_hook)

        event = PipelineEvent(
            event_type="entity_created",
            data={},
            timestamp="2024-01-15T10:30:00",
        )
        registry.emit(event)

        assert len(handled_errors) == 1
        assert "Oops!" in handled_errors[0].error

    def test_unregister_hook(self):
        """Test unregistering a hook."""
        registry = HookRegistry()
        results = []

        def my_hook(event: PipelineEvent):
            results.append(1)

        registry.register("entity_created", my_hook)
        assert registry.unregister("entity_created", my_hook) is True

        event = PipelineEvent(
            event_type="entity_created",
            data={},
            timestamp="2024-01-15T10:30:00",
        )
        registry.emit(event)

        assert results == []

    def test_unregister_nonexistent_hook(self):
        """Test unregistering a hook that wasn't registered."""
        registry = HookRegistry()

        result = registry.unregister("entity_created", lambda e: None)
        assert result is False

    def test_clear_specific_event(self):
        """Test clearing hooks for a specific event."""
        registry = HookRegistry()

        registry.register("entity_created", lambda e: None)
        registry.register("entity_merged", lambda e: None)

        registry.clear("entity_created")

        assert len(registry.get_hooks("entity_created")) == 0
        assert len(registry.get_hooks("entity_merged")) == 1

    def test_clear_all(self):
        """Test clearing all hooks."""
        registry = HookRegistry()

        registry.register("entity_created", lambda e: None)
        registry.register("entity_merged", lambda e: None)

        registry.clear()

        assert registry.hook_count == 0

    def test_hook_count(self):
        """Test hook_count property."""
        registry = HookRegistry()

        assert registry.hook_count == 0

        registry.register("entity_created", lambda e: None)
        assert registry.hook_count == 1

        registry.register("entity_merged", lambda e: None)
        assert registry.hook_count == 2

        registry.register("entity_created", lambda e: None)
        assert registry.hook_count == 3

    def test_valid_events_list(self):
        """Test that all expected events are valid."""
        expected_events = {
            "entity_created",
            "entity_merged",
            "entity_updated",
            "operation_applied",
            "operation_failed",
            "operation_skipped",
            "batch_start",
            "batch_complete",
            "session_start",
            "session_complete",
            "session_failed",
            "question_created",
            "question_answered",
        }

        assert HookRegistry.VALID_EVENTS == expected_events


class TestHelperHooks:
    """Tests for helper hook factories."""

    def test_logging_hook(self):
        """Test create_logging_hook factory."""
        logs = []
        hook = create_logging_hook(lambda msg: logs.append(msg))

        registry = HookRegistry()
        registry.register("entity_created", hook)

        registry.emit_simple("entity_created", {"name": "Test"})

        assert len(logs) == 1
        assert "entity_created" in logs[0]
        assert "Test" in logs[0]

    def test_counter_hook(self):
        """Test create_counter_hook factory."""
        hook, get_counts = create_counter_hook()

        registry = HookRegistry()
        registry.register("entity_created", hook)
        registry.register("entity_merged", hook)

        registry.emit_simple("entity_created", {})
        registry.emit_simple("entity_created", {})
        registry.emit_simple("entity_merged", {})

        counts = get_counts()
        assert counts["entity_created"] == 2
        assert counts["entity_merged"] == 1


class TestHookIntegration:
    """Integration tests for hooks with pipeline components."""

    def test_multiple_hooks_execution_order(self):
        """Test that hooks execute in registration order."""
        registry = HookRegistry()
        order = []

        for i in range(5):
            def make_hook(n):
                return lambda e: order.append(n)
            registry.register("entity_created", make_hook(i))

        registry.emit_simple("entity_created", {})

        assert order == [0, 1, 2, 3, 4]

    def test_hook_receives_full_event_data(self):
        """Test that hooks receive complete event data."""
        registry = HookRegistry()
        captured = None

        def capture_hook(event: PipelineEvent):
            nonlocal captured
            captured = event

        registry.register("entity_created", capture_hook)

        registry.emit_simple(
            "entity_created",
            {
                "entity_name": "Acme Corp",
                "entity_path": "customers/strategic/acme_corp",
                "tier": "strategic",
            },
            batch_id="batch_001",
            session_id="session_xyz",
        )

        assert captured is not None
        assert captured.event_type == "entity_created"
        assert captured.data["entity_name"] == "Acme Corp"
        assert captured.data["entity_path"] == "customers/strategic/acme_corp"
        assert captured.data["tier"] == "strategic"
        assert captured.batch_id == "batch_001"
        assert captured.session_id == "session_xyz"
