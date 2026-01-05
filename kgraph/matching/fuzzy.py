"""
Fuzzy name matching strategy.

Uses difflib.SequenceMatcher for string similarity.
"""

import re
from difflib import SequenceMatcher
from typing import Dict, List, Tuple

from kgraph.matching.base import (
    EntityIndexEntry,
    MatchCandidate,
    MatchStrategy,
    register_strategy,
)


@register_strategy("fuzzy_name")
class FuzzyNameMatchStrategy(MatchStrategy):
    """Match entities by fuzzy string similarity on names.

    Uses SequenceMatcher to compute similarity between normalized names.
    Also checks against aliases.
    """

    def __init__(self, threshold: float = 0.85):
        """Initialize strategy.

        Args:
            threshold: Minimum similarity score to consider a match
        """
        self._threshold = threshold

    @property
    def name(self) -> str:
        return "fuzzy_name"

    @property
    def score_range(self) -> Tuple[float, float]:
        return (self._threshold, 0.99)  # Never returns 1.0, that's for exact/alias

    def _normalize_name(self, name: str) -> str:
        """Normalize company name for comparison.

        Ensures symmetric normalization so that:
        - "Port Group USA" and "port_group_usa" both normalize the same
        """
        name = name.lower()
        # Convert underscores to spaces first (makes normalization symmetric)
        name = name.replace("_", " ")
        # Remove common suffixes
        for suffix in [
            " inc", " inc.", " corp", " corp.", " llc", " ltd", " ltd.",
            " gmbh", " a/s", " co", " co.", " company", " corporation",
            " sa", " s.a.", " sas", " s.a.s."
        ]:
            name = name.replace(suffix, "")
        # Remove special chars, keep alphanumeric and spaces
        name = re.sub(r"[^a-z0-9\s]", "", name)
        # Normalize whitespace
        name = " ".join(name.split())
        return name.strip()

    def _similarity(self, name1: str, name2: str) -> float:
        """Compute similarity between two names."""
        norm1 = self._normalize_name(name1)
        norm2 = self._normalize_name(name2)
        return SequenceMatcher(None, norm1, norm2).ratio()

    def find_matches(
        self,
        entity: dict,
        index: Dict[str, EntityIndexEntry],
        threshold: float = 0.0,
    ) -> List[MatchCandidate]:
        """Find matches by fuzzy name similarity."""
        threshold = threshold or self._threshold
        entity_name = entity.get("name", "")
        if not entity_name:
            return []

        candidates = []

        for entry_id, entry in index.items():
            best_score = 0.0
            matched_name = entry.name

            # Check main name
            score = self._similarity(entity_name, entry.name)
            if score > best_score:
                best_score = score
                matched_name = entry.name

            # Check aliases
            for alias in entry.aliases:
                alias_score = self._similarity(entity_name, alias)
                if alias_score > best_score:
                    best_score = alias_score
                    matched_name = alias

            if best_score >= threshold:
                candidates.append(
                    MatchCandidate(
                        candidate_id=entry_id,
                        candidate_name=entry.name,
                        candidate_path=entry.path,
                        match_type=self.name,
                        match_score=best_score,
                        match_details={
                            "matched_against": matched_name,
                            "query_name": entity_name,
                            "normalized_query": self._normalize_name(entity_name),
                            "normalized_match": self._normalize_name(matched_name),
                        },
                    )
                )

        # Sort by score descending
        candidates.sort(key=lambda c: c.match_score, reverse=True)
        return candidates
