"""Core abstractions for kgraph - agent-first design."""

from kgraph.core.index import EntityIndex, IndexEntry
from kgraph.core.storage import SimpleStorage, normalize_entity_id
from kgraph.core.observability import ObservabilityLogger, LogEntry
from kgraph.core.research import EntityResearcher

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
