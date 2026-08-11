# Tests

Pytest test suite for kvault.

## Structure

```
tests/
├── conftest.py              # Shared fixtures (sample_kb, initialized_kb, empty_kb)
├── fixtures/
│   └── sample_kb/           # 5-entity representative KB for E2E tests
├── test_api_exports.py      # Top-level public API exports
├── test_audit_2026_07_26.py # Regressions from the 2026-07-26 live-KB audit (path names, children digest, bad --kb-root)
├── test_check.py            # kvault check CLI + propagation staleness detection
├── test_cli_commands.py     # CLI command behavior and option ordering
├── test_cli_write_workflow.py # CLI write + propagation workflow
├── test_confirm_destructive.py # Destructive CLI operations require explicit confirmation
├── test_daily_artifacts.py  # Daily artifact generation
├── test_e2e_workflows.py    # End-to-end write/propagation workflow pipelines
├── test_events.py           # Capture journal: capture, resolve, write --event promotion, import
├── test_frontmatter.py      # YAML frontmatter parsing
├── test_hardening.py        # Path containment, write lock, strict frontmatter regressions
├── test_log_cli.py          # Observability log CLI
├── test_mcp_server.py       # MCP compatibility server
├── test_notes.py            # Work-reporting note vocabulary (closed set of 10 codes)
├── test_operations.py       # Node/entity operations and search
├── test_oplog.py            # Durable ops log: failure-proof appends, bounded store, UTC, no WAL
├── test_output_channels.py  # Channel contracts J1 (one JSON doc) and M1 (no stream writers in core/mcp)
├── test_pressure_fixes.py   # Pressure test regression coverage
├── test_research.py         # Entity matching/reconciliation helpers
├── test_storage.py          # SimpleStorage filesystem + scan_entities
├── test_summary_quality.py  # Parent-summary quality audit
└── test_work_reporting.py   # Each note code fires exactly when it should; no-op write leaves mtime alone
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

## Output Assertions (CliRunner)

**No test may use `result.stderr`.** On click 8.1.x (the Python 3.9 CI leg),
`Result.output` and `Result.stdout` are the *merged* stdout+stderr stream and
accessing `Result.stderr` raises `ValueError`; on click 8.2+ the streams are
separate but `output` stays merged. The portable guard is:

```python
data = json.loads(result.output)
```

Because `output` is merged on both legs, a successful parse proves nothing was
written to *either* channel besides the one JSON document — this is how the J1
invariant is pinned in `test_output_channels.py`.

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
