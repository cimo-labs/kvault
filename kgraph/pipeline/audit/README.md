# Audit Module

JSONL audit logging for complete traceability.

## Overview

Every significant action in the pipeline is logged to a JSONL audit file, providing:

- **Traceability** - Track all decisions
- **Debugging** - Diagnose issues
- **Compliance** - Audit trail for reviews

## Log Format

Each line is a JSON object:

```json
{
  "timestamp": "2026-01-05T10:30:00.123456",
  "category": "staging",
  "action": "stage",
  "details": {
    "operation_id": 123,
    "entity": "Acme Corp",
    "action": "merge",
    "confidence": 0.95,
    "status": "ready"
  }
}
```

## Categories

| Category | Actions | Description |
|----------|---------|-------------|
| `extraction` | `extract`, `parse` | Entity extraction events |
| `research` | `search`, `match` | Match finding events |
| `decision` | `auto_merge`, `auto_create`, `llm_decide` | Reconciliation decisions |
| `staging` | `stage`, `ready`, `reject` | Operation staging |
| `question` | `queued`, `answered`, `skipped` | Human review queue |
| `apply` | `merge`, `update`, `create`, `fail` | Execution events |
| `session` | `start`, `checkpoint`, `complete` | Session lifecycle |

## Usage

```python
from kgraph.pipeline.audit import log_audit, init_audit_logger

# Initialize logger (typically done by Orchestrator)
init_audit_logger(Path(".kgraph/audit.jsonl"))

# Log an event
log_audit("staging", "stage", {
    "operation_id": 123,
    "entity": "Acme Corp",
    "action": "merge",
    "confidence": 0.95,
})
```

## Files

```
audit/
├── __init__.py    # Exports: log_audit, init_audit_logger
└── logger.py      # AuditLogger implementation
```

## Log File Location

```
.kgraph/
└── audit.jsonl    # Append-only audit log
```

## Querying Logs

```bash
# Find all merge operations
grep '"action": "merge"' .kgraph/audit.jsonl

# Find all failed operations
grep '"action": "fail"' .kgraph/audit.jsonl

# Parse with jq
cat .kgraph/audit.jsonl | jq 'select(.category == "apply")'
```

## Log Rotation

Logs are append-only. For large deployments, consider:

```bash
# Rotate logs (example)
mv .kgraph/audit.jsonl .kgraph/audit.$(date +%Y%m%d).jsonl
```

## Privacy Considerations

Audit logs may contain entity names and email addresses. Handle according to your data retention policies.
