"""
EntityResearcher - Research existing entities before creating new ones.

Uses matching strategies to find potential duplicates and suggest actions.
"""

from typing import Dict, List, Optional, Tuple

from kgraph.core.index import EntityIndex, IndexEntry
from kgraph.matching.base import (
    EntityIndexEntry,
    MatchCandidate,
    MatchStrategy,
)
from kgraph.matching.alias import AliasMatchStrategy
from kgraph.matching.fuzzy import FuzzyNameMatchStrategy
from kgraph.matching.domain import EmailDomainMatchStrategy


class EntityResearcher:
    """Research existing entities before creating new ones.

    Uses multiple matching strategies to find potential duplicates:
    - Alias matching (exact match on known aliases)
    - Fuzzy name matching (string similarity)
    - Email domain matching (same company domain)

    Suggests actions based on match confidence:
    - 'create': No match found, safe to create new entity
    - 'update': High-confidence match, update existing entity
    - 'review': Ambiguous match, needs human review
    """

    def __init__(
        self,
        index: EntityIndex,
        strategies: List[MatchStrategy] = None,
    ):
        """Initialize researcher.

        Args:
            index: EntityIndex for lookups
            strategies: List of matching strategies (defaults to all three)
        """
        self.index = index
        self.strategies = strategies or [
            AliasMatchStrategy(),
            FuzzyNameMatchStrategy(threshold=0.85),
            EmailDomainMatchStrategy(),
        ]

    def _build_strategy_index(self) -> Dict[str, EntityIndexEntry]:
        """Build index format expected by matching strategies.

        Converts IndexEntry objects to EntityIndexEntry format.
        """
        entries = self.index.list_all()
        strategy_index = {}

        for entry in entries:
            # Use path as the key (unique identifier)
            strategy_index[entry.path] = EntityIndexEntry(
                id=entry.path,
                name=entry.name,
                entity_type=entry.category,
                tier=None,
                path=entry.path,
                aliases=entry.aliases,
                email_domains=entry.email_domains,
                industry=None,
                contacts=[],  # We extract domains at index time
                extra={},
            )

        return strategy_index

    def research(
        self,
        name: str,
        aliases: List[str] = None,
        email: str = None,
    ) -> List[MatchCandidate]:
        """Find potential matches using all strategies.

        Args:
            name: Name to search for
            aliases: Optional additional aliases to check
            email: Optional email address (used for domain matching)

        Returns:
            List of MatchCandidate objects sorted by score (highest first)
        """
        # Build entity dict for strategies
        entity = {"name": name}

        if aliases:
            entity["aliases"] = aliases

        if email:
            entity["contacts"] = [{"email": email}]

        # Build index for strategies
        strategy_index = self._build_strategy_index()

        # Collect all candidates
        all_candidates = []
        seen_paths = set()

        for strategy in self.strategies:
            candidates = strategy.find_matches(entity, strategy_index)
            for candidate in candidates:
                if candidate.candidate_path not in seen_paths:
                    all_candidates.append(candidate)
                    seen_paths.add(candidate.candidate_path)

        # Sort by score descending
        all_candidates.sort(key=lambda c: c.match_score, reverse=True)
        return all_candidates

    def best_match(
        self,
        name: str,
        threshold: float = 0.5,
        **kwargs,
    ) -> Optional[MatchCandidate]:
        """Return best match if above threshold, else None.

        Args:
            name: Name to search for
            threshold: Minimum score threshold
            **kwargs: Additional args passed to research()

        Returns:
            Best MatchCandidate if above threshold, None otherwise
        """
        candidates = self.research(name, **kwargs)

        if candidates and candidates[0].match_score >= threshold:
            return candidates[0]

        return None

    def exists(
        self,
        name: str,
        threshold: float = 0.9,
        **kwargs,
    ) -> bool:
        """Return True if high-confidence match exists.

        Args:
            name: Name to search for
            threshold: Minimum score to consider a match
            **kwargs: Additional args passed to research()

        Returns:
            True if a match exists above threshold
        """
        match = self.best_match(name, threshold=threshold, **kwargs)
        return match is not None

    def suggest_action(
        self,
        name: str,
        **kwargs,
    ) -> Tuple[str, Optional[str], float]:
        """Suggest action based on research.

        Args:
            name: Name to research
            **kwargs: Additional args passed to research()

        Returns:
            Tuple of (action, target_path, confidence):
            - ('create', None, 0.9): No match, create new entity
            - ('update', 'path/to/entity', 0.95): High match, update existing
            - ('review', 'path/to/entity', 0.7): Ambiguous, needs review
        """
        candidates = self.research(name, **kwargs)

        if not candidates:
            # No matches at all - safe to create
            return ("create", None, 0.95)

        best = candidates[0]

        if best.match_score >= 0.9:
            # High confidence match - update existing
            return ("update", best.candidate_path, best.match_score)
        elif best.match_score >= 0.7:
            # Medium confidence - needs review
            return ("review", best.candidate_path, best.match_score)
        else:
            # Low confidence matches - probably safe to create
            return ("create", None, 1.0 - best.match_score)

    def find_by_email(self, email: str) -> List[MatchCandidate]:
        """Find entities with matching email domain.

        Convenience method for email-based lookup.

        Args:
            email: Email address to search for

        Returns:
            List of matching candidates
        """
        if "@" not in email:
            return []

        domain = email.split("@")[1].lower()
        entries = self.index.find_by_email_domain(domain)

        return [
            MatchCandidate(
                candidate_id=entry.path,
                candidate_name=entry.name,
                candidate_path=entry.path,
                match_type="email_domain",
                match_score=0.9,
                match_details={
                    "matching_domain": domain,
                    "entity_domains": entry.email_domains,
                },
            )
            for entry in entries
        ]

    def find_exact(self, name: str) -> Optional[IndexEntry]:
        """Find entity by exact name or alias match.

        Convenience method for exact lookups.

        Args:
            name: Exact name or alias to find

        Returns:
            IndexEntry if found, None otherwise
        """
        # Try direct alias lookup first
        entry = self.index.find_by_alias(name)
        if entry:
            return entry

        # Fall back to FTS search and filter for exact
        results = self.index.search(name, limit=10)
        name_lower = name.lower()

        for result in results:
            if result.name.lower() == name_lower:
                return result
            if any(a.lower() == name_lower for a in result.aliases):
                return result

        return None
