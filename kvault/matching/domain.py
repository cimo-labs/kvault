"""
Email domain matching strategy.

Match entities by shared email domains in contacts.
"""

from typing import Dict, List, Optional, Set, Tuple

from kvault.matching.base import (
    EntityIndexEntry,
    MatchCandidate,
    MatchStrategy,
    register_strategy,
)


# Default generic domains to ignore
DEFAULT_GENERIC_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "aol.com",
    "icloud.com",
    "mail.com",
    "protonmail.com",
    "live.com",
    "msn.com",
    "ymail.com",
}


@register_strategy("email_domain")
class EmailDomainMatchStrategy(MatchStrategy):
    """Match entities by shared email domains.

    When an extracted entity has contacts with email addresses, this strategy
    finds existing entities whose contacts share the same email domain.

    Generic email domains (gmail, yahoo, etc.) are ignored.
    """

    def __init__(self, generic_domains: Optional[Set[str]] = None, **kwargs):
        """Initialize strategy.

        Args:
            generic_domains: Set of domains to ignore. If None, uses defaults.
            **kwargs: Ignored (allows shared kwargs across strategies)
        """
        self._generic_domains = generic_domains or DEFAULT_GENERIC_DOMAINS

    @property
    def name(self) -> str:
        return "email_domain"

    @property
    def score_range(self) -> Tuple[float, float]:
        return (0.85, 0.95)  # Domain matches are high confidence but not certain

    def _extract_domains(self, contacts: List[Dict]) -> Set[str]:
        """Extract non-generic domains from contacts."""
        domains = set()
        for contact in contacts:
            email = contact.get("email", "")
            if "@" in email:
                domain = email.split("@")[1].lower()
                if domain not in self._generic_domains:
                    domains.add(domain)
        return domains

    def find_matches(
        self,
        entity: dict,
        index: Dict[str, EntityIndexEntry],
        threshold: float = 0.0,
    ) -> List[MatchCandidate]:
        """Find matches by email domain overlap."""
        contacts = entity.get("contacts", [])
        entity_domains = self._extract_domains(contacts)

        if not entity_domains:
            return []

        candidates = []

        for entry_id, entry in index.items():
            entry_domains = set(entry.email_domains) - self._generic_domains

            if not entry_domains:
                continue

            # Find overlapping domains
            overlap = entity_domains & entry_domains

            if overlap:
                # Score based on overlap ratio
                # More overlap = higher confidence
                overlap_ratio = len(overlap) / max(len(entity_domains), len(entry_domains))
                score = 0.85 + (overlap_ratio * 0.10)  # 0.85 to 0.95

                candidates.append(
                    MatchCandidate(
                        candidate_id=entry_id,
                        candidate_name=entry.name,
                        candidate_path=entry.path,
                        match_type=self.name,
                        match_score=min(score, 0.95),
                        match_details={
                            "matching_domains": list(overlap),
                            "entity_domains": list(entity_domains),
                            "candidate_domains": list(entry_domains),
                        },
                    )
                )

        # Sort by score descending
        candidates.sort(key=lambda c: c.match_score, reverse=True)
        return candidates
