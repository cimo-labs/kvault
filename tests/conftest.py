"""
Shared pytest fixtures for kgraph tests.

Provides fixtures for:
- Configuration loading
- Temporary knowledge graph directories
- Mock extraction agents
- Staging databases
- Sample data
"""

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from kgraph.core.config import KGraphConfig
from kgraph.core.storage import FilesystemStorage
from kgraph.pipeline import Orchestrator, StagingDatabase, QuestionQueue
from kgraph.pipeline.agents.extraction import MockExtractionAgent


# Path to fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def sample_config_path(fixtures_dir: Path) -> Path:
    """Path to sample configuration file."""
    return fixtures_dir / "sample_config.yaml"


@pytest.fixture
def test_config(sample_config_path: Path) -> KGraphConfig:
    """Load test configuration from fixtures."""
    return KGraphConfig.from_yaml(sample_config_path)


@pytest.fixture
def test_config_dict() -> Dict[str, Any]:
    """Minimal configuration as dictionary (for custom configs)."""
    return {
        "project": {
            "name": "test-project",
            "data_path": "./data",
            "kg_path": "./knowledge_graph",
        },
        "entity_types": {
            "customer": {
                "directory": "customers",
                "tier_field": "tier",
                "required_fields": ["name"],
            },
        },
        "tiers": {
            "standard": {
                "storage_type": "directory",
                "criteria": {"revenue_min": 1},
            },
            "prospects": {
                "storage_type": "jsonl",
                "criteria": {"revenue": 0},
            },
        },
        "confidence": {
            "auto_merge": 0.95,
            "auto_update": 0.90,
            "auto_create": 0.50,
        },
        "matching": {
            "strategies": ["alias", "fuzzy_name", "email_domain"],
            "fuzzy_threshold": 0.85,
        },
        "agent": {
            "provider": "claude",
            "timeout": 120,
        },
    }


@pytest.fixture
def temp_kg_path(tmp_path: Path) -> Path:
    """Create temporary knowledge graph directory."""
    kg_path = tmp_path / "knowledge_graph"
    kg_path.mkdir(parents=True)

    # Create entity type directories
    (kg_path / "customers").mkdir()
    (kg_path / "customers" / "standard").mkdir()
    (kg_path / "customers" / "prospects").mkdir()

    return kg_path


@pytest.fixture
def temp_config(test_config_dict: Dict[str, Any], tmp_path: Path) -> KGraphConfig:
    """Create configuration pointing to temp directories."""
    config_dict = test_config_dict.copy()
    config_dict["project"]["data_path"] = str(tmp_path / "data")
    config_dict["project"]["kg_path"] = str(tmp_path / "knowledge_graph")

    # Create directories
    (tmp_path / "data").mkdir(parents=True)
    kg_path = tmp_path / "knowledge_graph"
    kg_path.mkdir(parents=True)
    (kg_path / "customers").mkdir()
    (kg_path / "customers" / "standard").mkdir()
    (kg_path / "customers" / "prospects").mkdir()

    return KGraphConfig.from_dict(config_dict)


@pytest.fixture
def temp_storage(temp_config: KGraphConfig) -> FilesystemStorage:
    """Filesystem storage with temp directory."""
    return FilesystemStorage(temp_config.kg_path, temp_config)


@pytest.fixture
def staging_db(tmp_path: Path) -> StagingDatabase:
    """Temporary staging database."""
    db_path = tmp_path / "staging.db"
    return StagingDatabase(db_path)


@pytest.fixture
def question_queue(tmp_path: Path) -> QuestionQueue:
    """Temporary question queue (shares db with staging)."""
    db_path = tmp_path / "staging.db"
    return QuestionQueue(db_path)


@pytest.fixture
def sample_emails(fixtures_dir: Path) -> List[Dict[str, Any]]:
    """Load sample emails from fixtures."""
    emails_path = fixtures_dir / "sample_emails.json"
    with open(emails_path) as f:
        return json.load(f)


@pytest.fixture
def mock_entities_new_company() -> List[Dict[str, Any]]:
    """Mock extracted entities for a new company (no matches)."""
    return [
        {
            "name": "Acme Corporation",
            "entity_type": "customer",
            "tier": "standard",
            "industry": "manufacturing",
            "contacts": [
                {"name": "John Smith", "email": "john.smith@acmecorp.com", "role": "Purchasing Manager"},
                {"name": "Jane Doe", "email": "jane.doe@acme-corporation.com", "role": "Operations Lead"},
            ],
            "confidence": 0.85,
            "source_id": "email_001",
        }
    ]


@pytest.fixture
def mock_entities_ambiguous() -> List[Dict[str, Any]]:
    """Mock extracted entities with ambiguous match (triggers review)."""
    return [
        {
            "name": "Ambiguous Match Co",
            "entity_type": "customer",
            "tier": "prospects",
            "industry": "unknown",
            "contacts": [
                {"name": "Contact Person", "email": "contact@ambiguous-match.com", "role": ""},
            ],
            "confidence": 0.60,
            "source_id": "email_006",
        }
    ]


@pytest.fixture
def mock_entities_duplicate() -> List[Dict[str, Any]]:
    """Mock extracted entities that should merge with existing."""
    return [
        {
            "name": "ACME Corp",  # Fuzzy match to "Acme Corporation"
            "entity_type": "customer",
            "tier": "standard",
            "industry": "manufacturing",
            "contacts": [
                {"name": "Tom Wilson", "email": "purchasing@acmecorp.com", "role": "Buyer"},
            ],
            "confidence": 0.90,
            "source_id": "email_007",
        }
    ]


@pytest.fixture
def mock_extraction_agent(temp_config: KGraphConfig, mock_entities_new_company: List[Dict]) -> MockExtractionAgent:
    """Mock extraction agent with predefined entities."""
    return MockExtractionAgent(temp_config, mock_entities=mock_entities_new_company)


@pytest.fixture
def existing_entity_data() -> Dict[str, Any]:
    """Data for an existing entity in the KG."""
    return {
        "name": "Acme Corporation",
        "industry": "manufacturing",
        "tier": "standard",
        "contacts": [
            {"name": "John Smith", "email": "john.smith@acmecorp.com", "role": "Purchasing Manager"},
        ],
        "aliases": ["Acme Corp", "ACME"],
        "sources": ["manual_entry"],
    }


@pytest.fixture
def existing_kg(temp_storage: FilesystemStorage, existing_entity_data: Dict[str, Any]) -> FilesystemStorage:
    """Knowledge graph with pre-existing entities for merge tests."""
    # Create existing entity
    temp_storage.write_entity(
        entity_type="customer",
        entity_id="acme_corporation",
        data=existing_entity_data,
        tier="standard",
    )
    return temp_storage


@pytest.fixture
def orchestrator(temp_config: KGraphConfig, tmp_path: Path) -> Orchestrator:
    """Orchestrator with temp directories."""
    return Orchestrator(
        config=temp_config,
        kg_path=temp_config.kg_path,
        data_dir=tmp_path / ".kgraph",
    )


@pytest.fixture
def orchestrator_with_mock(
    temp_config: KGraphConfig,
    tmp_path: Path,
    mock_entities_new_company: List[Dict],
) -> Orchestrator:
    """
    Orchestrator with MockExtractionAgent injected.

    This replaces the real extraction agent to avoid Claude CLI dependency.
    """
    orch = Orchestrator(
        config=temp_config,
        kg_path=temp_config.kg_path,
        data_dir=tmp_path / ".kgraph",
    )

    # Replace extraction agent with mock
    orch.extraction_agent = MockExtractionAgent(temp_config, mock_entities=mock_entities_new_company)

    return orch
