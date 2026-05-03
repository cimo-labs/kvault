# Tests

Pytest test suite for kvault.

## Structure

```
tests/
├── conftest.py              # Shared fixtures (sample_kb, initialized_kb, empty_kb)
├── fixtures/
│   └── sample_kb/           # 5-entity representative KB for E2E tests
├── test_api_exports.py      # Top-level public API exports
├── test_check.py            # kvault check CLI + propagation staleness detection
├── test_cli_commands.py     # CLI command behavior and option ordering
├── test_cli_write_workflow.py # CLI write + propagation workflow
├── test_daily_artifacts.py  # Daily artifact generation
├── test_e2e_workflows.py    # End-to-end write/propagation workflow pipelines
├── test_frontmatter.py      # YAML frontmatter parsing
├── test_log_cli.py          # Observability log CLI
├── test_mcp_server.py       # MCP compatibility server
├── test_operations.py       # Node/entity operations and search
├── test_pressure_fixes.py   # Pressure test regression coverage
├── test_research.py         # Entity matching/reconciliation helpers
├── test_storage.py          # SimpleStorage filesystem + scan_entities
└── test_summary_quality.py  # Parent-summary quality audit
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
    """Sample KB with .kvault/ initialized for operations."""

@pytest.fixture
def empty_kb(tmp_path):
    """Fresh KB with category structure but no entities."""
```

## Test Data

### sample_kb (5 leaf entities plus parent summaries)

- `people/friends/alice_smith` — aliases: Alice Smith, alice@acme.com, Ali
- `people/friends/jose_garcia` — aliases: José García, Jose Garcia, jose@startup.io
- `people/work/sarah_chen` — aliases: Sarah Chen, sarah@research.example
- `people/work/bob_jones` — aliases: Bob Jones, bob@bigcorp.com, Bobby
- `projects/kvault` — aliases: kvault, knowledgevault, knowledge vault

## Stats

**The full suite runs in a few seconds.**
