"""Core abstractions for kvault - agent-first design."""

from kvault.core.index import EntityIndex, IndexEntry
from kvault.core.storage import SimpleStorage, normalize_entity_id
from kvault.core.observability import ObservabilityLogger, LogEntry
from kvault.core.research import EntityResearcher

__all__ = [
    # Index
    "EntityIndex",
    "IndexEntry",
    # Storage
    "SimpleStorage",
    "normalize_entity_id",
    # Observability
    "ObservabilityLogger",
    "LogEntry",
    # Research
    "EntityResearcher",
]
