# Tests

Pytest test suite for kvault.

## Structure

```
tests/
├── conftest.py              # Shared fixtures (sample_kb, initialized_kb, empty_kb)
├── fixtures/
│   └── sample_kb/           # 5-entity representative KB for E2E tests
├── test_check.py            # kvault check CLI + propagation staleness detection
├── test_e2e_workflows.py    # Complete 4-step workflow pipelines
├── test_frontmatter.py      # YAML frontmatter parsing
├── test_pressure_fixes.py   # Pressure test regression coverage
└── test_storage.py          # SimpleStorage filesystem + scan_entities
```

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Quick summary
pytest tests/ -q

# Run with coverage
pytest tests/ --cov=kvault --cov-report=term-missing

# Single test file
pytest tests/test_check.py -v

# Stop on first failure
pytest tests/ -x
```

## Key Fixtures

```python
@pytest.fixture
def sample_kb(tmp_path):
    """Writable copy of the sample KB — safe to modify per test."""

@pytest.fixture
def initialized_kb(sample_kb):
    """Sample KB with MCP server initialized."""

@pytest.fixture
def empty_kb(tmp_path):
    """Fresh KB with category structure but no entities. MCP initialized."""
```

## Test Data

### sample_kb (5 entities)

- `people/friends/alice_smith` — aliases: Alice Smith, alice@acme.com, Ali
- `people/friends/jose_garcia` — aliases: José García, Jose Garcia, jose@startup.io
- `people/work/sarah_chen` — aliases: Sarah Chen, sarah@anthropic.com
- `people/work/bob_jones` — aliases: Bob Jones, bob@bigcorp.com, Bobby
- `projects/kvault` — aliases: kvault, knowledgevault, knowledge vault

## Stats

**~80 tests, runs in < 1s.**
