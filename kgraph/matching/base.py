"""
Base classes for matching strategies.

Matching strategies find candidate matches for entities during deduplication.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class EntityIndexEntry:
    """An entry in the entity index used for matching."""

    id: str  # Normalized entity ID
    name: str  # Display name
    entity_type: str  # e.g., "customer", "supplier"
    tier: Optional[str] = None
    path: str = ""
    aliases: List[str] = field(default_factory=list)
    email_domains: List[str] = field(default_factory=list)
    industry: Optional[str] = None
    contacts: List[Dict[str, str]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_entity_data(
        cls, entity_id: str, data: dict, entity_type: str, tier: Optional[str] = None
    ) -> "EntityIndexEntry":
        """Create index entry from entity data."""
        # Extract email domains from contacts
        email_domains = []
        contacts = data.get("contacts", [])
        for contact in contacts:
            email = contact.get("email", "")
            if "@" in email:
                domain = email.split("@")[1].lower()
                if domain not in email_domains:
                    email_domains.append(domain)

        return cls(
            id=entity_id,
            name=data.get("topic", data.get("name", entity_id)),
            entity_type=entity_type,
            tier=tier or data.get("tier"),
            path=data.get("path", ""),
            aliases=data.get("aliases", []),
            email_domains=email_domains,
            industry=data.get("industry"),
            contacts=contacts,
            extra={k: v for k, v in data.items() if k not in [
                "topic", "name", "tier", "path", "aliases", "contacts", "industry"
            ]},
        )


@dataclass
class MatchCandidate:
    """A potential match found by a matching strategy."""

    candidate_id: str  # ID of matching entity
    candidate_name: str  # Name of matching entity
    candidate_path: str  # Path to matching entity
    match_type: str  # Type of match (e.g., "alias", "fuzzy_name", "email_domain")
    match_score: float  # Confidence score (0.0 to 1.0)
    match_details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate match score."""
        if not 0.0 <= self.match_score <= 1.0:
            raise ValueError(f"match_score must be between 0 and 1, got {self.match_score}")


class MatchStrategy(ABC):
    """Abstract base class for entity matching strategies.

    Each strategy implements a different matching algorithm:
    - AliasMatchStrategy: Exact match against known aliases
    - FuzzyNameMatchStrategy: Fuzzy string matching on names
    - EmailDomainMatchStrategy: Match by email domain
    - SemanticMatchStrategy: Embedding-based semantic similarity (optional)

    Strategies return candidates with scores indicating match confidence.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name (e.g., 'alias', 'fuzzy_name')."""
        pass

    @property
    @abstractmethod
    def score_range(self) -> Tuple[float, float]:
        """Return (min_score, max_score) this strategy produces.

        Used to calibrate confidence thresholds across strategies.
        """
        pass

    @abstractmethod
    def find_matches(
        self,
        entity: dict,
        index: Dict[str, EntityIndexEntry],
        threshold: float = 0.0,
    ) -> List[MatchCandidate]:
        """Find matching candidates for an entity.

        Args:
            entity: Entity data to match (must have 'name' key)
            index: Dictionary mapping entity_id -> EntityIndexEntry
            threshold: Minimum score to include in results

        Returns:
            List of MatchCandidate objects sorted by score (highest first)
        """
        pass


# Strategy registry for loading by name
_STRATEGY_REGISTRY: Dict[str, type] = {}


def register_strategy(name: str):
    """Decorator to register a matching strategy."""
    def decorator(cls):
        _STRATEGY_REGISTRY[name] = cls
        return cls
    return decorator


def get_strategy(name: str) -> type:
    """Get a strategy class by name."""
    if name not in _STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy: {name}. Available: {list(_STRATEGY_REGISTRY.keys())}")
    return _STRATEGY_REGISTRY[name]


def list_strategies() -> List[str]:
    """List available strategy names."""
    return list(_STRATEGY_REGISTRY.keys())


def load_strategies(names: List[str], **kwargs) -> List[MatchStrategy]:
    """Load and instantiate strategies by name.

    Args:
        names: List of strategy names to load
        **kwargs: Arguments passed to strategy constructors

    Returns:
        List of instantiated strategies
    """
    strategies = []
    for name in names:
        strategy_cls = get_strategy(name)
        strategies.append(strategy_cls(**kwargs))
    return strategies
