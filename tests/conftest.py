"""Pytest configuration for kgraph tests."""

import pytest
from pathlib import Path


@pytest.fixture
def tmp_kg_path(tmp_path):
    """Provide a temporary knowledge graph root directory."""
    kg_path = tmp_path / "knowledge_graph"
    kg_path.mkdir()
    return kg_path


@pytest.fixture
def tmp_db_path(tmp_path):
    """Provide a temporary database path."""
    return tmp_path / "test.db"


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"
