"""
kgraph - Agent-first knowledge graph framework

Build knowledge graphs from unstructured data using intelligent agents.
The agent does extraction, research, decisions, and propagation.
kgraph provides tools, not workflows.

Core components:
- EntityIndex: SQLite-backed entity index with full-text search
- SimpleStorage: Filesystem storage with minimal 4-field schema
- ObservabilityLogger: Phase-based logging for debugging and improvement
- EntityResearcher: Research existing entities before creating new ones

Matching strategies:
- AliasMatchStrategy: Exact match against known aliases
- FuzzyNameMatchStrategy: Fuzzy string matching on names
- EmailDomainMatchStrategy: Match by email domain
"""

__version__ = "0.2.0"

# Core modules
from kgraph.core.index import EntityIndex, IndexEntry
from kgraph.core.storage import SimpleStorage, normalize_entity_id
from kgraph.core.observability import ObservabilityLogger, LogEntry
from kgraph.core.research import EntityResearcher

# Matching strategies
from kgraph.matching import (
    MatchStrategy,
    MatchCandidate,
    EntityIndexEntry,
    AliasMatchStrategy,
    FuzzyNameMatchStrategy,
    EmailDomainMatchStrategy,
    register_strategy,
    get_strategy,
    list_strategies,
    load_strategies,
)

__all__ = [
    # Core
    "EntityIndex",
    "IndexEntry",
    "SimpleStorage",
    "normalize_entity_id",
    "ObservabilityLogger",
    "LogEntry",
    "EntityResearcher",
    # Matching
    "MatchStrategy",
    "MatchCandidate",
    "EntityIndexEntry",
    "AliasMatchStrategy",
    "FuzzyNameMatchStrategy",
    "EmailDomainMatchStrategy",
    "register_strategy",
    "get_strategy",
    "list_strategies",
    "load_strategies",
]
