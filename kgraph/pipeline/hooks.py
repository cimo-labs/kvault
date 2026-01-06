"""
Pipeline hook system for kgraph.

Provides callback/event mechanisms so applications can react to pipeline events
such as entity creation, merging, batch completion, etc.

Usage:
    from kgraph.pipeline import HookRegistry, PipelineEvent

    def on_entity_created(event: PipelineEvent):
        print(f"Created: {event.data['entity_name']}")

    hooks = HookRegistry()
    hooks.register("entity_created", on_entity_created)

    orchestrator = Orchestrator(config, kg_path, hooks=hooks)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Protocol


@dataclass
class PipelineEvent:
    """Event emitted during pipeline execution."""

    event_type: str
    """Event type identifier (e.g., 'entity_created', 'entity_merged')"""

    data: Dict[str, Any]
    """Event-specific data payload"""

    timestamp: str
    """ISO format timestamp when event was emitted"""

    batch_id: Optional[str] = None
    """Associated batch ID, if any"""

    session_id: Optional[str] = None
    """Associated session ID, if any"""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
            "batch_id": self.batch_id,
            "session_id": self.session_id,
        }


class PipelineHook(Protocol):
    """Protocol for pipeline hooks.

    Hooks are callables that receive a PipelineEvent and return None.
    Hooks should handle their own errors - exceptions are caught and logged
    but do not stop pipeline execution.
    """

    def __call__(self, event: PipelineEvent) -> None:
        """Handle a pipeline event."""
        ...


@dataclass
class HookError:
    """Record of a hook execution error."""

    hook_name: str
    event_type: str
    error: str
    timestamp: str


class HookRegistry:
    """Registry for pipeline hooks.

    Manages hook registration and event emission. Hooks are executed
    synchronously in registration order. Errors in hooks are logged
    but do not stop pipeline execution.

    Example:
        hooks = HookRegistry()

        # Register with function
        hooks.register("entity_created", my_handler)

        # Register with lambda
        hooks.register("batch_complete", lambda e: print(f"Batch done: {e.data}"))

        # Emit events
        hooks.emit(PipelineEvent(
            event_type="entity_created",
            data={"entity_name": "Acme Corp"},
            timestamp=datetime.now().isoformat(),
        ))
    """

    VALID_EVENTS = {
        # Entity lifecycle events
        "entity_created",
        "entity_merged",
        "entity_updated",
        # Operation events
        "operation_applied",
        "operation_failed",
        "operation_skipped",
        # Batch events
        "batch_start",
        "batch_complete",
        # Session events
        "session_start",
        "session_complete",
        "session_failed",
        # Review events
        "question_created",
        "question_answered",
    }

    def __init__(self, error_handler: Optional[Callable[[HookError], None]] = None):
        """
        Initialize hook registry.

        Args:
            error_handler: Optional callback for hook errors.
                          If not provided, errors are silently ignored.
        """
        self._hooks: Dict[str, List[PipelineHook]] = {e: [] for e in self.VALID_EVENTS}
        self._error_handler = error_handler
        self._errors: List[HookError] = []

    def register(self, event_type: str, hook: PipelineHook) -> None:
        """
        Register a hook for an event type.

        Args:
            event_type: Event type to listen for (must be in VALID_EVENTS)
            hook: Callable that receives PipelineEvent

        Raises:
            ValueError: If event_type is not valid
        """
        if event_type not in self.VALID_EVENTS:
            raise ValueError(
                f"Unknown event type: {event_type}. "
                f"Valid types: {sorted(self.VALID_EVENTS)}"
            )
        self._hooks[event_type].append(hook)

    def unregister(self, event_type: str, hook: PipelineHook) -> bool:
        """
        Unregister a hook.

        Args:
            event_type: Event type
            hook: Hook to remove

        Returns:
            True if hook was found and removed
        """
        if event_type not in self._hooks:
            return False

        try:
            self._hooks[event_type].remove(hook)
            return True
        except ValueError:
            return False

    def emit(self, event: PipelineEvent) -> None:
        """
        Emit an event to all registered hooks.

        Hooks are executed synchronously in registration order.
        Errors in hooks are caught and logged but do not stop execution.

        Args:
            event: Event to emit
        """
        hooks = self._hooks.get(event.event_type, [])

        for hook in hooks:
            try:
                hook(event)
            except Exception as e:
                error = HookError(
                    hook_name=_get_hook_name(hook),
                    event_type=event.event_type,
                    error=str(e),
                    timestamp=datetime.now().isoformat(),
                )
                self._errors.append(error)

                if self._error_handler:
                    try:
                        self._error_handler(error)
                    except Exception:
                        pass  # Don't let error handler errors propagate

    def emit_simple(
        self,
        event_type: str,
        data: Dict[str, Any],
        batch_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """
        Convenience method to emit an event without constructing PipelineEvent.

        Args:
            event_type: Event type
            data: Event data
            batch_id: Optional batch ID
            session_id: Optional session ID
        """
        event = PipelineEvent(
            event_type=event_type,
            data=data,
            timestamp=datetime.now().isoformat(),
            batch_id=batch_id,
            session_id=session_id,
        )
        self.emit(event)

    def get_hooks(self, event_type: str) -> List[PipelineHook]:
        """
        Get all hooks registered for an event type.

        Args:
            event_type: Event type

        Returns:
            List of registered hooks
        """
        return list(self._hooks.get(event_type, []))

    def get_errors(self) -> List[HookError]:
        """
        Get all hook execution errors.

        Returns:
            List of errors (oldest first)
        """
        return list(self._errors)

    def clear_errors(self) -> None:
        """Clear error history."""
        self._errors.clear()

    def clear(self, event_type: Optional[str] = None) -> None:
        """
        Clear registered hooks.

        Args:
            event_type: If provided, only clear hooks for this event.
                       Otherwise clear all hooks.
        """
        if event_type:
            if event_type in self._hooks:
                self._hooks[event_type] = []
        else:
            self._hooks = {e: [] for e in self.VALID_EVENTS}

    @property
    def hook_count(self) -> int:
        """Total number of registered hooks."""
        return sum(len(hooks) for hooks in self._hooks.values())


def _get_hook_name(hook: PipelineHook) -> str:
    """Get a descriptive name for a hook."""
    if hasattr(hook, "__name__"):
        return hook.__name__
    if hasattr(hook, "__class__"):
        return hook.__class__.__name__
    return str(hook)


# Convenience factory for common hooks


def create_logging_hook(
    logger: Optional[Callable[[str], None]] = None,
) -> PipelineHook:
    """
    Create a hook that logs events.

    Args:
        logger: Logging function (defaults to print)

    Returns:
        Hook function
    """
    log_fn = logger or print

    def logging_hook(event: PipelineEvent) -> None:
        log_fn(f"[{event.event_type}] {event.data}")

    return logging_hook


def create_counter_hook() -> tuple[PipelineHook, Callable[[], Dict[str, int]]]:
    """
    Create a hook that counts events by type.

    Returns:
        Tuple of (hook function, get_counts function)
    """
    counts: Dict[str, int] = {}

    def counter_hook(event: PipelineEvent) -> None:
        counts[event.event_type] = counts.get(event.event_type, 0) + 1

    def get_counts() -> Dict[str, int]:
        return dict(counts)

    return counter_hook, get_counts
