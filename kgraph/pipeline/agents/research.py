"""
Research agent for finding existing entity matches.

Uses configured matching strategies to find potential matches
in the existing knowledge graph.
"""

from typing import Dict, List, Optional, Tuple

from kgraph.core.config import KGraphConfig
from kgraph.core.storage import FilesystemStorage
from kgraph.matching import load_strategies, EntityIndexEntry
from kgraph.matching.base import MatchCandidate as BaseMatchCandidate
from kgraph.pipeline.agents.base import ExtractedEntity, MatchCandidate
from kgraph.pipeline.audit import log_audit


class ResearchAgent:
    """
    Agent that researches existing entities for potential matches.

    Uses configured matching strategies (alias, fuzzy, email domain)
    to find entities that might be duplicates or related.
    """

    def __init__(self, config: KGraphConfig, storage: FilesystemStorage):
        """
        Initialize research agent.

        Args:
            config: KGraph configuration
            storage: Storage backend for reading entities
        """
        self.config = config
        self.storage = storage

        # Load matching strategies from config
        self.strategies = load_strategies(
            config.matching.strategies,
            threshold=config.matching.fuzzy_threshold,
            generic_domains=list(config.matching.generic_domains),
        )

        # Entity index cache
        self._index: Dict[str, EntityIndexEntry] = {}
        self._index_dirty = True

    @property
    def name(self) -> str:
        """Agent name for logging."""
        return "research"

    def research_batch(
        self,
        entities: List[ExtractedEntity],
    ) -> List[Tuple[ExtractedEntity, List[MatchCandidate]]]:
        """
        Research all entities against existing knowledge graph.

        Args:
            entities: List of extracted entities to research

        Returns:
            List of (entity, candidates) tuples
        """
        # Rebuild index if needed
        if self._index_dirty:
            self._build_index()

        results = []

        for entity in entities:
            candidates = self._research_entity(entity)
            results.append((entity, candidates))

            log_audit(
                "research",
                "entity_researched",
                {
                    "entity": entity.name,
                    "candidates_found": len(candidates),
                    "top_score": candidates[0].match_score if candidates else None,
                    "top_type": candidates[0].match_type if candidates else None,
                },
            )

        return results

    def research_single(
        self,
        entity: ExtractedEntity,
    ) -> List[MatchCandidate]:
        """
        Research a single entity.

        Args:
            entity: Entity to research

        Returns:
            List of match candidates sorted by score (highest first)
        """
        if self._index_dirty:
            self._build_index()

        return self._research_entity(entity)

    def _research_entity(self, entity: ExtractedEntity) -> List[MatchCandidate]:
        """Research a single entity against the index."""
        # Build entity dict for matching strategies
        entity_dict = {
            "name": entity.name,
            "contacts": entity.contacts,
            "industry": entity.industry,
            "entity_type": entity.entity_type,
        }

        all_candidates: List[MatchCandidate] = []

        # Run each strategy
        for strategy in self.strategies:
            try:
                base_candidates = strategy.find_matches(
                    entity_dict,
                    self._index,
                    threshold=self.config.matching.fuzzy_threshold,
                )

                # Convert to our MatchCandidate format
                for bc in base_candidates:
                    all_candidates.append(
                        MatchCandidate(
                            candidate_path=bc.candidate_path,
                            candidate_name=bc.candidate_name,
                            match_type=bc.match_type,
                            match_score=bc.match_score,
                            match_details=bc.match_details,
                        )
                    )

            except Exception as e:
                log_audit(
                    "research",
                    "strategy_error",
                    {
                        "strategy": strategy.name,
                        "entity": entity.name,
                        "error": str(e),
                    },
                )

        # Deduplicate by path, keep highest score
        seen: Dict[str, MatchCandidate] = {}
        for c in all_candidates:
            if c.candidate_path not in seen or c.match_score > seen[c.candidate_path].match_score:
                seen[c.candidate_path] = c

        # Sort by score (highest first)
        return sorted(seen.values(), key=lambda x: x.match_score, reverse=True)

    def _build_index(self) -> None:
        """Build entity index from storage."""
        self._index = {}

        for et_name, et_config in self.config.entity_types.items():
            # Get all entities for this type
            entities = self.storage.list_entities(et_name)

            for entity_info in entities:
                entity_id = entity_info["id"]
                tier = entity_info.get("tier")

                # Read full entity data
                full_data = self.storage.read_entity(et_name, entity_id, tier)
                if not full_data:
                    continue

                # Combine list info with full data
                combined = {**entity_info, **full_data}

                # Build index entry
                try:
                    index_entry = self._build_index_entry(
                        entity_id,
                        combined,
                        et_name,
                        tier,
                    )
                    self._index[entity_id] = index_entry

                except Exception as e:
                    log_audit(
                        "research",
                        "index_error",
                        {
                            "entity_id": entity_id,
                            "error": str(e),
                        },
                    )

        self._index_dirty = False

        log_audit(
            "research",
            "index_built",
            {"total_entities": len(self._index)},
        )

    def _build_index_entry(
        self,
        entity_id: str,
        data: Dict,
        entity_type: str,
        tier: Optional[str],
    ) -> EntityIndexEntry:
        """Build an EntityIndexEntry from entity data."""
        # Extract aliases (various possible locations)
        aliases = set()
        if data.get("aliases"):
            aliases.update(data["aliases"])
        if data.get("known_aliases"):
            aliases.update(data["known_aliases"])

        # Extract email domains from contacts
        email_domains = set()
        for contact in data.get("contacts", []):
            email = contact.get("email", "")
            if "@" in email:
                domain = email.split("@")[1].lower()
                # Skip generic domains
                if domain not in self.config.matching.generic_domains:
                    email_domains.add(domain)

        # Build path
        if tier:
            path = f"{self.config.entity_types[entity_type].directory}/{tier}/{entity_id}"
        else:
            path = f"{self.config.entity_types[entity_type].directory}/{entity_id}"

        return EntityIndexEntry(
            id=entity_id,
            entity_type=entity_type,
            tier=tier,
            name=data.get("name", entity_id),
            aliases=list(aliases),
            email_domains=list(email_domains),
            contacts=data.get("contacts", []),
            industry=data.get("industry"),
            path=path,
            extra=data,
        )

    def invalidate_cache(self) -> None:
        """Mark index as needing rebuild."""
        self._index_dirty = True

    def get_index_size(self) -> int:
        """Get number of entities in index."""
        return len(self._index)
