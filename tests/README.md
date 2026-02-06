# Tests

Pytest test suite for kvault.

## Structure

```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures
├── fixtures/
│   ├── __init__.py
│   ├── sample_config.yaml   # Test configuration
│   └── sample_emails.json   # 10 sample emails
├── test_e2e_pipeline.py     # End-to-end tests (6 tests)
├── test_agents.py           # Agent unit tests (19 tests)
└── test_staging.py          # Staging layer tests (19 tests)
```

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=kvault --cov-report=term-missing

# Run E2E tests only
pytest tests/test_e2e_pipeline.py -v

# Run unit tests only
pytest tests/ --ignore=tests/test_e2e_pipeline.py -v

# Stop on first failure
pytest tests/ -x
```

## Test Categories

### E2E Tests (`test_e2e_pipeline.py`)

Full pipeline integration tests:

| Test | Scenario |
|------|----------|
| `test_create_new_entity_full_pipeline` | Process → CREATE → verify in KG |
| `test_merge_duplicate_entity` | Alias match → stage MERGE |
| `test_ambiguous_match_triggers_review` | Low confidence → question queue |
| `test_answer_question_then_apply` | Review → approve → apply |
| `test_session_state_tracking` | Session lifecycle |
| `test_get_status` | Status reporting |

### Agent Tests (`test_agents.py`)

Data model and agent unit tests:

- `TestExtractedEntity` - Serialization/deserialization
- `TestMatchCandidate` - Candidate creation
- `TestReconcileDecision` - Decision handling
- `TestMockExtractionAgent` - Mock agent behavior
- `TestAgentContext` - Context and prompts

### Staging Tests (`test_staging.py`)

Database and queue tests:

- `TestStagingDatabase` - CRUD operations, status transitions
- `TestQuestionQueue` - Add, answer, skip, priority ordering

## Key Fixtures

```python
@pytest.fixture
def temp_config() -> KGraphConfig:
    """Configuration pointing to temp directories."""

@pytest.fixture
def temp_storage() -> FilesystemStorage:
    """Temporary filesystem storage."""

@pytest.fixture
def staging_db(tmp_path) -> StagingDatabase:
    """Temporary SQLite database."""

@pytest.fixture
def sample_emails() -> list[dict]:
    """10 sample emails from fixtures."""

@pytest.fixture
def mock_entities_new_company() -> list[dict]:
    """Mock entities for new company scenario."""

@pytest.fixture
def existing_kg() -> FilesystemStorage:
    """KG with pre-existing entity for merge tests."""

@pytest.fixture
def orchestrator_with_mock() -> Orchestrator:
    """Orchestrator with MockExtractionAgent."""
```

## MockExtractionAgent

Tests use `MockExtractionAgent` to avoid Claude CLI dependency:

```python
from kvault.pipeline.agents.extraction import MockExtractionAgent

agent = MockExtractionAgent(config, mock_entities=[
    {"name": "Test Corp", "entity_type": "customer", "confidence": 0.9}
])

# Inject into orchestrator
orchestrator.extraction_agent = agent
```

## Test Data

### sample_emails.json

10 sample emails covering:
- New companies (Acme Corp, GlobalTech)
- Duplicates (ACME Corp vs Acme Corporation)
- Ambiguous matches
- Suppliers vs customers

### sample_config.yaml

Minimal test configuration with:
- Entity types: customer
- Tiers: strategic, key, standard, prospects
- Matching strategies: alias, fuzzy_name, email_domain
- Confidence thresholds: 0.95/0.90/0.50

## Coverage

Target: >70% coverage of pipeline module

```bash
pytest tests/ --cov=kvault --cov-report=html
open htmlcov/index.html
```
