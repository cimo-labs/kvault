"""
kvault - Agent-first knowledge graph framework

Build knowledge graphs from unstructured data using intelligent agents.
The agent does extraction, research, decisions, and propagation.
kvault provides tools, not workflows.

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

__version__ = "0.3.0"

# Core modules
from kvault.core.index import EntityIndex, IndexEntry
from kvault.core.storage import SimpleStorage, normalize_entity_id
from kvault.core.observability import ObservabilityLogger, LogEntry
from kvault.core.research import EntityResearcher

# Matching strategies
from kvault.matching import (
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
