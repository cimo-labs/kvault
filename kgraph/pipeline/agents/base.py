"""
Base data models for pipeline agents.

These dataclasses flow through the pipeline:
- ExtractedEntity: Output of extraction phase
- ReconcileDecision: Output of reconciliation phase
- AgentContext: Shared context for agent invocations
"""

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ExtractedEntity:
    """
    Entity extracted from raw data by LLM.

    Created by ExtractionAgent, consumed by ResearchAgent.
    """

    name: str
    """Normalized entity name (e.g., 'Acme Corporation')"""

    entity_type: str
    """Type of entity: customer, supplier, person, etc."""

    tier: Optional[str] = None
    """Tier classification: strategic, key, standard, prospect"""

    industry: Optional[str] = None
    """Industry: robotics, automotive, medical, industrial, etc."""

    contacts: List[Dict[str, str]] = field(default_factory=list)
    """List of contacts: [{name, email, phone, role}]"""

    confidence: float = 0.5
    """LLM's confidence in this extraction (0.0-1.0)"""

    source_id: Optional[str] = None
    """ID from source data (e.g., email_id)"""

    raw_data: Dict[str, Any] = field(default_factory=dict)
    """Original data from extraction (for debugging/audit)"""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExtractedEntity":
        """Create from dictionary."""
        return cls(
            name=data.get("name", ""),
            entity_type=data.get("entity_type", "entity"),
            tier=data.get("tier"),
            industry=data.get("industry"),
            contacts=data.get("contacts", []),
            confidence=data.get("confidence", 0.5),
            source_id=data.get("source_id"),
            raw_data=data.get("raw_data", data),
        )


@dataclass
class MatchCandidate:
    """
    Potential match from research phase.

    Note: This shadows kgraph.matching.MatchCandidate but includes
    additional fields needed for reconciliation.
    """

    candidate_path: str
    """Path to candidate entity (e.g., 'customers/strategic/acme_corp')"""

    candidate_name: str
    """Display name of candidate"""

    match_type: str
    """How matched: alias, fuzzy_name, email_domain"""

    match_score: float
    """Match score (0.0-1.0)"""

    match_details: Dict[str, Any] = field(default_factory=dict)
    """Strategy-specific details"""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MatchCandidate":
        """Create from dictionary."""
        return cls(
            candidate_path=data.get("candidate_path", data.get("path", "")),
            candidate_name=data.get("candidate_name", data.get("name", "")),
            match_type=data.get("match_type", data.get("type", "")),
            match_score=data.get("match_score", data.get("score", 0.0)),
            match_details=data.get("match_details", data.get("details", {})),
        )


@dataclass
class ReconcileDecision:
    """
    Decision about how to handle an extracted entity.

    Created by DecisionAgent, consumed by staging layer.
    """

    entity_name: str
    """Name of the extracted entity"""

    action: str
    """Action to take: 'merge', 'update', or 'create'"""

    target_path: Optional[str] = None
    """Target entity path for merge/update (None for create)"""

    confidence: float = 0.5
    """Confidence in this decision (0.0-1.0)"""

    reasoning: str = ""
    """Explanation of why this decision was made"""

    needs_review: bool = False
    """Whether this should be queued for human review"""

    source_entity: Optional[ExtractedEntity] = None
    """The extracted entity this decision is about"""

    candidates: List[MatchCandidate] = field(default_factory=list)
    """Match candidates from research phase"""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "entity_name": self.entity_name,
            "action": self.action,
            "target_path": self.target_path,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "needs_review": self.needs_review,
            "source_entity": self.source_entity.to_dict() if self.source_entity else None,
            "candidates": [c.to_dict() for c in self.candidates],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReconcileDecision":
        """Create from dictionary."""
        source_entity = None
        if data.get("source_entity"):
            source_entity = ExtractedEntity.from_dict(data["source_entity"])

        candidates = [
            MatchCandidate.from_dict(c) for c in data.get("candidates", [])
        ]

        return cls(
            entity_name=data.get("entity_name", ""),
            action=data.get("action", "create"),
            target_path=data.get("target_path"),
            confidence=data.get("confidence", 0.5),
            reasoning=data.get("reasoning", ""),
            needs_review=data.get("needs_review", False),
            source_entity=source_entity,
            candidates=candidates,
        )


@dataclass
class AgentContext:
    """
    Shared context for agent invocations.

    Passed to agents to provide configuration and state.
    """

    session_id: str
    """Current session identifier"""

    batch_id: str
    """Current batch identifier"""

    config: Any  # KGraphConfig - avoiding circular import
    """KGraph configuration"""

    prompts_path: Optional[Path] = None
    """Path to prompt templates"""

    def get_prompt_template(self, name: str) -> Optional[str]:
        """
        Load a prompt template by name.

        Args:
            name: Template name (e.g., 'extraction', 'reconciliation')

        Returns:
            Template content or None if not found
        """
        if not self.prompts_path:
            return None

        template_path = self.prompts_path / f"{name}.md"
        if template_path.exists():
            return template_path.read_text()

        return None
