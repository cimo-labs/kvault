"""
Alias matching strategy.

Exact match against known aliases from an aliases file.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from kvault.matching.base import (
    EntityIndexEntry,
    MatchCandidate,
    MatchStrategy,
    register_strategy,
)


@register_strategy("alias")
class AliasMatchStrategy(MatchStrategy):
    """Match entities against known aliases.

    Uses an aliases file (JSON) that maps canonical IDs to lists of aliases.
    Returns score of 1.0 for exact alias matches.
    """

    def __init__(self, aliases_path: Optional[Path] = None, **kwargs):
        """Initialize strategy.

        Args:
            aliases_path: Path to aliases JSON file. If None, relies on
                          aliases stored in EntityIndexEntry.
            **kwargs: Ignored (allows shared kwargs across strategies)
        """
        self._aliases_path = aliases_path
        self._aliases_cache: Optional[Dict] = None

    @property
    def name(self) -> str:
        return "alias"

    @property
    def score_range(self) -> Tuple[float, float]:
        return (1.0, 1.0)  # Alias matches are always exact

    def _load_aliases(self) -> Dict:
        """Load aliases from file."""
        if self._aliases_cache is not None:
            return self._aliases_cache

        if self._aliases_path and self._aliases_path.exists():
            with open(self._aliases_path) as f:
                self._aliases_cache = json.load(f)
        else:
            self._aliases_cache = {}

        return self._aliases_cache

    def _normalize(self, name: str) -> str:
        """Normalize name for comparison."""
        return name.lower().strip()

    def find_matches(
        self,
        entity: dict,
        index: Dict[str, EntityIndexEntry],
        threshold: float = 0.0,
    ) -> List[MatchCandidate]:
        """Find matches by alias lookup.

        Checks both:
        1. External aliases file (if configured)
        2. Aliases stored in each EntityIndexEntry
        """
        entity_name = entity.get("name", "")
        if not entity_name:
            return []

        normalized_name = self._normalize(entity_name)
        candidates = []

        # Check external aliases file
        aliases_data = self._load_aliases()
        for canonical_id, alias_info in aliases_data.items():
            # alias_info can be a list of aliases or a dict with 'aliases' key
            if isinstance(alias_info, list):
                aliases = alias_info
            elif isinstance(alias_info, dict):
                aliases = alias_info.get("aliases", [])
            else:
                continue

            # Check if entity name matches any alias
            for alias in aliases:
                if self._normalize(alias) == normalized_name:
                    # Found a match - look up in index
                    if canonical_id in index:
                        entry = index[canonical_id]
                        candidates.append(
                            MatchCandidate(
                                candidate_id=canonical_id,
                                candidate_name=entry.name,
                                candidate_path=entry.path,
                                match_type=self.name,
                                match_score=1.0,
                                match_details={
                                    "matched_alias": alias,
                                    "source": "aliases_file",
                                },
                            )
                        )
                        break

        # Check aliases in index entries
        for entry_id, entry in index.items():
            if entry_id in [c.candidate_id for c in candidates]:
                continue  # Already found via aliases file

            for alias in entry.aliases:
                if self._normalize(alias) == normalized_name:
                    candidates.append(
                        MatchCandidate(
                            candidate_id=entry_id,
                            candidate_name=entry.name,
                            candidate_path=entry.path,
                            match_type=self.name,
                            match_score=1.0,
                            match_details={
                                "matched_alias": alias,
                                "source": "entity_aliases",
                            },
                        )
                    )
                    break

            # Also check if name exactly matches
            if self._normalize(entry.name) == normalized_name:
                if entry_id not in [c.candidate_id for c in candidates]:
                    candidates.append(
                        MatchCandidate(
                            candidate_id=entry_id,
                            candidate_name=entry.name,
                            candidate_path=entry.path,
                            match_type=self.name,
                            match_score=1.0,
                            match_details={
                                "matched_alias": entry.name,
                                "source": "exact_name",
                            },
                        )
                    )

        return candidates
