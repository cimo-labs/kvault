"""Pluggable matching strategies for entity deduplication."""

from kvault.matching.base import (
    MatchStrategy,
    MatchCandidate,
    EntityIndexEntry,
    register_strategy,
    get_strategy,
    list_strategies,
    load_strategies,
)

# Import strategies to register them
from kvault.matching.alias import AliasMatchStrategy
from kvault.matching.fuzzy import FuzzyNameMatchStrategy
from kvault.matching.domain import EmailDomainMatchStrategy

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
