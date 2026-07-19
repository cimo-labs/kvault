# kvault Python package

`knowledgevault` 0.12 exposes a journal-first library as well as the primary `kvault` CLI.

## Supported mutation API

```python
from pathlib import Path

from kvault import (
    ReconciliationPlan,
    apply_reconciliation,
    capture_event,
    prepare_reconciliation,
)

root = Path("/absolute/path/to/knowledge-base")
captured = capture_event(
    root,
    "Alice owns the revised launch agenda.",
    source="conversation",
    source_ref="message:stable-id",
)
context = prepare_reconciliation(root, [captured.event_id], paths=["projects", "."])

# An external agent performs bounded reads and constructs a complete, revision-bound plan.
plan = ReconciliationPlan.model_validate(plan_payload)
result = apply_reconciliation(root, plan)
```

kvault is provider-neutral: it captures immutable evidence and validates policy, revisions,
provenance, summary coverage, transactions, and integrity. The caller reasons about semantic
placement and supplies the full proposed Markdown bodies.

Primary public types and services are exported from `kvault`:

- events: `capture_event`, `get_event`, `list_events`, `derive_event_states`
- reconciliation: `prepare_reconciliation`, `apply_reconciliation`,
  `approve_reconciliation`, `reconciliation_status`, `recover_reconciliations`
- policy and migration: `ReconciliationPolicy`, `load_policy`, `migrate`,
  `import_moss_capture`
- integrity: `audit_kb`
- read/search and derivative artifact helpers retained from earlier releases

## Interfaces

- `kvault`: primary CLI, including capture, bounded navigation, reconciliation, migration, and
  validation.
- `kvault-mcp`: optional Python 3.10+ root-bound MCP parity without direct mutation tools.
- `skills/kvault`: packaged agent workflow and read-only parallel-analysis reference.

`SimpleStorage` and direct write helpers in `kvault.core.operations` remain deprecated Python
compatibility code. They are not supported 0.12 mutation surfaces; new integrations must capture
and reconcile.

## Development

```bash
pip install -e ".[dev,mcp]"
pytest -q --cov=kvault --cov-fail-under=80
ruff check .
black --check kvault/ tests/
mypy kvault/ --ignore-missing-imports
```
