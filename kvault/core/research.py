"""Reusable entity research and reconciliation helpers.

This module provides lightweight candidate matching against entities already
stored in a kvault knowledge base. It is intentionally filesystem-backed and
dependency-light so downstream agents/adapters can share one matching policy.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kvault.core.storage import normalize_entity_id, scan_entities


@dataclass(frozen=True)
class ResearchCandidate:
    """A ranked candidate returned by entity research."""

    candidate_path: str
    candidate_name: str
    match_type: str
    match_score: float
    match_details: Dict[str, Any]


class EntityResearcher:
    """Filesystem-backed entity researcher for dedup/reconciliation."""

    FUZZY_MATCH_THRESHOLD = 0.78
    UPDATE_THRESHOLD = 0.95
    REVIEW_THRESHOLD = 0.80
    DEFAULT_CREATE_CONFIDENCE = 0.95
    MIN_CREATE_CONFIDENCE = 0.55

    def __init__(self, kg_root: Path):
        self.kg_root = Path(kg_root)
        self._entity_cache = None

    def invalidate(self) -> None:
        """Invalidate in-memory entity cache after writes."""
        self._entity_cache = None

    def _entities(self):
        if self._entity_cache is None:
            self._entity_cache = scan_entities(self.kg_root)
        return self._entity_cache

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    def research(
        self,
        entity_name: str,
        aliases: Optional[List[str]] = None,
        email: Optional[str] = None,
        max_results: int = 5,
    ) -> List[ResearchCandidate]:
        """Find likely existing entities for a proposed entity."""
        aliases = aliases or []
        target_norm = normalize_entity_id(entity_name)
        alias_norms = [normalize_entity_id(str(a)) for a in aliases if a]
        email_norm = email.lower().strip() if email else None
        email_domain = email_norm.split("@", 1)[1] if email_norm and "@" in email_norm else None

        candidates: List[ResearchCandidate] = []

        for entity in self._entities():
            entity_name_norm = normalize_entity_id(entity.name)
            path_leaf_norm = normalize_entity_id(Path(entity.path).name)
            entity_alias_norms = {normalize_entity_id(str(a)) for a in entity.aliases if a}
            entity_aliases_lower = {str(a).lower() for a in entity.aliases if a}

            best_type = ""
            best_score = 0.0
            best_details: Dict[str, Any] = {}

            if target_norm and (target_norm == entity_name_norm or target_norm == path_leaf_norm):
                best_type = "exact_name"
                best_score = 1.0
                best_details = {"matched": target_norm}
            elif target_norm and target_norm in entity_alias_norms:
                best_type = "exact_alias"
                best_score = 0.98
                best_details = {"matched": target_norm}
            elif email_norm and email_norm in entity_aliases_lower:
                best_type = "exact_email"
                best_score = 0.99
                best_details = {"matched": email_norm}
            elif email_domain and email_domain in entity.email_domains:
                best_type = "email_domain"
                best_score = 0.90
                best_details = {"domain": email_domain}
            else:
                comparison_pool = [entity_name_norm, path_leaf_norm] + list(entity_alias_norms)
                query_terms = [target_norm] + [a for a in alias_norms if a]

                fuzzy_score = 0.0
                fuzzy_term = ""
                fuzzy_target = ""
                for query in query_terms:
                    if not query:
                        continue
                    for target in comparison_pool:
                        if not target:
                            continue
                        score = self._similarity(query, target)
                        if score > fuzzy_score:
                            fuzzy_score = score
                            fuzzy_term = query
                            fuzzy_target = target

                if fuzzy_score >= self.FUZZY_MATCH_THRESHOLD:
                    best_type = "fuzzy_name"
                    best_score = fuzzy_score
                    best_details = {"matched": fuzzy_term, "target": fuzzy_target}

            if best_type:
                candidates.append(
                    ResearchCandidate(
                        candidate_path=entity.path,
                        candidate_name=entity.name,
                        match_type=best_type,
                        match_score=best_score,
                        match_details=best_details,
                    )
                )

        candidates.sort(key=lambda c: c.match_score, reverse=True)
        return candidates[:max_results]

    def suggest_action(
        self,
        entity_name: str,
        aliases: Optional[List[str]] = None,
        email: Optional[str] = None,
    ) -> Tuple[str, Optional[str], float]:
        """Suggest update/review/create action for reconciliation."""
        candidates = self.research(entity_name, aliases=aliases, email=email, max_results=1)
        if not candidates:
            return "create", None, self.DEFAULT_CREATE_CONFIDENCE

        best = candidates[0]
        if best.match_score >= self.UPDATE_THRESHOLD:
            return "update", best.candidate_path, best.match_score
        if best.match_score >= self.REVIEW_THRESHOLD:
            return "review", best.candidate_path, best.match_score
        return "create", None, max(self.MIN_CREATE_CONFIDENCE, 1.0 - best.match_score)


__all__ = ["ResearchCandidate", "EntityResearcher"]
