"""Pytest configuration for kvault tests.

Fixture Architecture (following CJE patterns):
- sample_kb_path: Read-only path to the sample KB fixture
- sample_kb: Writable copy of sample KB in tmp dir
- initialized_kb: Sample KB with MCP server initialized + index built
- empty_kb: Fresh KB with category structure but no entities
- tmp_kg_path: Bare temporary directory (legacy)
- tmp_db_path: Temporary database path (legacy)
"""

import shutil
import pytest
from pathlib import Path


# ============================================================================
# Sample KB Fixtures (NEW — real data for E2E tests)
# ============================================================================

SAMPLE_KB_ENTITY_COUNT = 5  # alice_smith, jose_garcia, sarah_chen, bob_jones, kvault


@pytest.fixture
def sample_kb_path():
    """Path to the sample KB fixture (read-only source).

    Contains 5 entities across 2 categories:
    - people/friends/alice_smith (aliases: Alice Smith, alice@acme.com, Ali)
    - people/friends/jose_garcia (aliases: José García, Jose Garcia, jose@startup.io)
    - people/work/sarah_chen (aliases: Sarah Chen, sarah@anthropic.com)
    - people/work/bob_jones (aliases: Bob Jones, bob@bigcorp.com, Bobby)
    - projects/kvault (aliases: kvault, knowledgevault, knowledge vault)
    """
    return Path(__file__).parent / "fixtures" / "sample_kb"


@pytest.fixture
def sample_kb(tmp_path, sample_kb_path):
    """Writable copy of the sample KB in a tmp directory.

    Safe to modify — each test gets its own copy.
    """
    dst = tmp_path / "kb"
    shutil.copytree(sample_kb_path, dst)
    return dst


@pytest.fixture
def initialized_kb(sample_kb):
    """Sample KB with MCP server initialized and index built.

    Ready for search, CRUD, and workflow tests.
    Note: This sets global MCP state — tests using this fixture
    should not also call handle_kvault_init with a different path.
    """
    from kvault.mcp.server import handle_kvault_init

    result = handle_kvault_init(str(sample_kb))
    return sample_kb


@pytest.fixture
def empty_kb(tmp_path):
    """Fresh KB with category structure but no entities.

    Has people/ and projects/ categories ready for entity creation.
    MCP server is initialized.
    """
    from kvault.mcp.server import handle_kvault_init

    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "_summary.md").write_text("# Test KB\n\nEmpty knowledge base for testing.\n")
    (kb / "people").mkdir()
    (kb / "people" / "_summary.md").write_text("# People\n\nAll contacts.\n")
    (kb / "projects").mkdir()
    (kb / "projects" / "_summary.md").write_text("# Projects\n\nAll projects.\n")

    handle_kvault_init(str(kb))
    return kb


# ============================================================================
# Legacy Fixtures (kept for backward compatibility with existing tests)
# ============================================================================


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
