"""
Unit tests for pipeline agents.

Tests the data models and agent functionality:
- ExtractedEntity serialization
- ReconcileDecision creation
- MatchCandidate handling
- MockExtractionAgent
"""

from pathlib import Path
from typing import Any, Dict, List

import pytest

from kgraph.core.config import KGraphConfig
from kgraph.pipeline.agents.base import (
    ExtractedEntity,
    MatchCandidate,
    ReconcileDecision,
    AgentContext,
)
from kgraph.pipeline.agents.extraction import MockExtractionAgent


class TestExtractedEntity:
    """Tests for ExtractedEntity dataclass."""

    def test_create_minimal_entity(self) -> None:
        """Create entity with only required fields."""
        entity = ExtractedEntity(name="Test Corp", entity_type="customer")

        assert entity.name == "Test Corp"
        assert entity.entity_type == "customer"
        assert entity.tier is None
        assert entity.confidence == 0.5
        assert entity.contacts == []

    def test_create_full_entity(self) -> None:
        """Create entity with all fields."""
        entity = ExtractedEntity(
            name="Acme Corporation",
            entity_type="customer",
            tier="strategic",
            industry="robotics",
            contacts=[
                {"name": "John Doe", "email": "john@acme.com", "role": "CEO"}
            ],
            confidence=0.95,
            source_id="email_001",
            raw_data={"original": "data"},
        )

        assert entity.name == "Acme Corporation"
        assert entity.tier == "strategic"
        assert entity.industry == "robotics"
        assert len(entity.contacts) == 1
        assert entity.contacts[0]["name"] == "John Doe"
        assert entity.confidence == 0.95

    def test_to_dict(self) -> None:
        """Test serialization to dict."""
        entity = ExtractedEntity(
            name="Test Corp",
            entity_type="customer",
            tier="standard",
            confidence=0.8,
        )

        d = entity.to_dict()

        assert d["name"] == "Test Corp"
        assert d["entity_type"] == "customer"
        assert d["tier"] == "standard"
        assert d["confidence"] == 0.8

    def test_from_dict(self) -> None:
        """Test deserialization from dict."""
        data = {
            "name": "Restored Corp",
            "entity_type": "supplier",
            "tier": "key",
            "industry": "automotive",
            "contacts": [{"name": "Jane", "email": "jane@restored.com"}],
            "confidence": 0.75,
            "source_id": "email_123",
        }

        entity = ExtractedEntity.from_dict(data)

        assert entity.name == "Restored Corp"
        assert entity.entity_type == "supplier"
        assert entity.tier == "key"
        assert entity.industry == "automotive"
        assert len(entity.contacts) == 1
        assert entity.confidence == 0.75

    def test_roundtrip_serialization(self) -> None:
        """Test to_dict -> from_dict preserves data."""
        original = ExtractedEntity(
            name="Roundtrip Inc",
            entity_type="customer",
            tier="strategic",
            industry="medical",
            contacts=[
                {"name": "Dr. Smith", "email": "smith@roundtrip.com", "role": "Director"}
            ],
            confidence=0.92,
            source_id="source_001",
            raw_data={"key": "value"},
        )

        restored = ExtractedEntity.from_dict(original.to_dict())

        assert restored.name == original.name
        assert restored.entity_type == original.entity_type
        assert restored.tier == original.tier
        assert restored.industry == original.industry
        assert restored.confidence == original.confidence


class TestMatchCandidate:
    """Tests for MatchCandidate dataclass."""

    def test_create_match_candidate(self) -> None:
        """Create a match candidate."""
        candidate = MatchCandidate(
            candidate_path="customers/strategic/acme_corp",
            candidate_name="Acme Corporation",
            match_type="fuzzy_name",
            match_score=0.92,
            match_details={"ratio": 0.92},
        )

        assert candidate.candidate_path == "customers/strategic/acme_corp"
        assert candidate.candidate_name == "Acme Corporation"
        assert candidate.match_type == "fuzzy_name"
        assert candidate.match_score == 0.92

    def test_from_dict_with_aliases(self) -> None:
        """Test from_dict handles field aliases."""
        data = {
            "path": "customers/key/test",
            "name": "Test Company",
            "type": "alias",
            "score": 1.0,
            "details": {"matched_alias": "Test Co"},
        }

        candidate = MatchCandidate.from_dict(data)

        assert candidate.candidate_path == "customers/key/test"
        assert candidate.candidate_name == "Test Company"
        assert candidate.match_type == "alias"
        assert candidate.match_score == 1.0

    def test_to_dict(self) -> None:
        """Test serialization."""
        candidate = MatchCandidate(
            candidate_path="customers/standard/xyz",
            candidate_name="XYZ Corp",
            match_type="email_domain",
            match_score=0.85,
        )

        d = candidate.to_dict()

        assert d["candidate_path"] == "customers/standard/xyz"
        assert d["match_score"] == 0.85


class TestReconcileDecision:
    """Tests for ReconcileDecision dataclass."""

    def test_create_merge_decision(self) -> None:
        """Create a merge decision."""
        entity = ExtractedEntity(
            name="ACME Corp",
            entity_type="customer",
            confidence=0.9,
        )
        candidate = MatchCandidate(
            candidate_path="customers/strategic/acme",
            candidate_name="Acme Corporation",
            match_type="fuzzy_name",
            match_score=0.95,
        )

        decision = ReconcileDecision(
            entity_name="ACME Corp",
            action="merge",
            target_path="customers/strategic/acme",
            confidence=0.95,
            reasoning="Fuzzy match score 0.95 exceeds threshold",
            needs_review=False,
            source_entity=entity,
            candidates=[candidate],
        )

        assert decision.action == "merge"
        assert decision.target_path == "customers/strategic/acme"
        assert not decision.needs_review
        assert len(decision.candidates) == 1

    def test_create_create_decision(self) -> None:
        """Create a create decision (no matches)."""
        entity = ExtractedEntity(
            name="NewCo LLC",
            entity_type="customer",
            confidence=0.8,
        )

        decision = ReconcileDecision(
            entity_name="NewCo LLC",
            action="create",
            target_path=None,
            confidence=0.8,
            reasoning="No existing matches found",
            needs_review=False,
            source_entity=entity,
            candidates=[],
        )

        assert decision.action == "create"
        assert decision.target_path is None
        assert decision.candidates == []

    def test_decision_needs_review(self) -> None:
        """Test decision that needs human review."""
        decision = ReconcileDecision(
            entity_name="Ambiguous Inc",
            action="merge",
            target_path="customers/standard/ambiguous",
            confidence=0.65,
            reasoning="Uncertain match - requires human verification",
            needs_review=True,
        )

        assert decision.needs_review
        assert decision.confidence == 0.65

    def test_to_dict_with_nested(self) -> None:
        """Test serialization with nested objects."""
        entity = ExtractedEntity(name="Test", entity_type="customer")
        candidate = MatchCandidate(
            candidate_path="path",
            candidate_name="Name",
            match_type="alias",
            match_score=1.0,
        )

        decision = ReconcileDecision(
            entity_name="Test",
            action="merge",
            target_path="path",
            confidence=1.0,
            source_entity=entity,
            candidates=[candidate],
        )

        d = decision.to_dict()

        assert d["entity_name"] == "Test"
        assert d["source_entity"]["name"] == "Test"
        assert len(d["candidates"]) == 1
        assert d["candidates"][0]["match_score"] == 1.0

    def test_from_dict_with_nested(self) -> None:
        """Test deserialization with nested objects."""
        data = {
            "entity_name": "Restored",
            "action": "update",
            "target_path": "customers/key/restored",
            "confidence": 0.88,
            "reasoning": "Email domain match",
            "needs_review": False,
            "source_entity": {
                "name": "Restored Inc",
                "entity_type": "customer",
                "confidence": 0.9,
            },
            "candidates": [
                {
                    "candidate_path": "customers/key/restored",
                    "candidate_name": "Restored Corporation",
                    "match_type": "email_domain",
                    "match_score": 0.88,
                }
            ],
        }

        decision = ReconcileDecision.from_dict(data)

        assert decision.entity_name == "Restored"
        assert decision.action == "update"
        assert decision.source_entity is not None
        assert decision.source_entity.name == "Restored Inc"
        assert len(decision.candidates) == 1
        assert decision.candidates[0].match_score == 0.88


class TestMockExtractionAgent:
    """Tests for MockExtractionAgent."""

    def test_mock_returns_predefined_entities(
        self,
        temp_config: KGraphConfig,
        mock_entities_new_company: List[Dict],
    ) -> None:
        """Mock agent returns predefined entities."""
        agent = MockExtractionAgent(
            temp_config,
            mock_entities=mock_entities_new_company,
        )

        # Process some items
        items = [{"id": "1", "content": "test"}]
        entities = agent.extract(items)

        assert len(entities) == 1
        assert entities[0].name == "Acme Corporation"
        assert entities[0].entity_type == "customer"

    def test_mock_with_empty_entities(
        self,
        temp_config: KGraphConfig,
    ) -> None:
        """Mock agent with no predefined entities returns empty list."""
        agent = MockExtractionAgent(temp_config, mock_entities=[])

        items = [{"id": "1", "content": "test"}]
        entities = agent.extract(items)

        assert entities == []

    def test_mock_multiple_entities(
        self,
        temp_config: KGraphConfig,
    ) -> None:
        """Mock agent can return multiple entities."""
        mock_data = [
            {"name": "Company A", "entity_type": "customer", "tier": "standard"},
            {"name": "Company B", "entity_type": "customer", "tier": "prospects"},
            {"name": "Supplier X", "entity_type": "supplier"},
        ]

        agent = MockExtractionAgent(temp_config, mock_entities=mock_data)

        items = [{"id": "1"}]
        entities = agent.extract(items)

        assert len(entities) == 3
        assert entities[0].name == "Company A"
        assert entities[1].name == "Company B"
        assert entities[2].name == "Supplier X"


class TestAgentContext:
    """Tests for AgentContext."""

    def test_create_context(
        self,
        temp_config: KGraphConfig,
    ) -> None:
        """Create agent context."""
        context = AgentContext(
            session_id="session_001",
            batch_id="batch_001",
            config=temp_config,
        )

        assert context.session_id == "session_001"
        assert context.batch_id == "batch_001"
        assert context.config is not None

    def test_get_prompt_template_no_path(
        self,
        temp_config: KGraphConfig,
    ) -> None:
        """Get prompt template returns None when no path set."""
        context = AgentContext(
            session_id="s1",
            batch_id="b1",
            config=temp_config,
            prompts_path=None,
        )

        template = context.get_prompt_template("extraction")

        assert template is None

    def test_get_prompt_template_from_path(
        self,
        temp_config: KGraphConfig,
        tmp_path: Path,
    ) -> None:
        """Get prompt template loads from file."""
        # Create a template file
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        template_file = prompts_dir / "extraction.md"
        template_file.write_text("# Extraction Prompt\nExtract entities from: {input}")

        context = AgentContext(
            session_id="s1",
            batch_id="b1",
            config=temp_config,
            prompts_path=prompts_dir,
        )

        template = context.get_prompt_template("extraction")

        assert template is not None
        assert "Extraction Prompt" in template
        assert "{input}" in template
