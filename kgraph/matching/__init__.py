"""Pluggable matching strategies for entity deduplication."""

from kgraph.matching.base import (
    MatchStrategy,
    MatchCandidate,
    EntityIndexEntry,
    register_strategy,
    get_strategy,
    list_strategies,
    load_strategies,
)

# Import strategies to register them
from kgraph.matching.alias import AliasMatchStrategy
from kgraph.matching.fuzzy import FuzzyNameMatchStrategy
from kgraph.matching.domain import EmailDomainMatchStrategy

__all__ = [
    "MatchStrategy",
    "MatchCandidate",
    "EntityIndexEntry",
    "register_strategy",
    "get_strategy",
    "list_strategies",
    "load_strategies",
    "AliasMatchStrategy",
    "FuzzyNameMatchStrategy",
    "EmailDomainMatchStrategy",
]
