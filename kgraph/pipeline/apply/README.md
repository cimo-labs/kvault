# Apply Module

Execution layer for applying staged operations to the knowledge graph.

## Overview

The apply module takes staged operations and writes them to the filesystem storage. It handles:

- **Priority ordering** - Merges first, then updates, then creates
- **Atomic writes** - Each operation is atomic
- **Index invalidation** - Updates research cache
- **Audit logging** - Records all changes

## Components

| Component | File | Purpose |
|-----------|------|---------|
| **OperationExecutor** | `executor.py` | Execute staged operations |

## Execution Order

```
┌─────────────────────────────────────────────────────────┐
│                  Execute Batch                          │
│                                                         │
│  1. MERGE Operations (priority=1)                       │
│     └── Merge contacts, add aliases                     │
│                                                         │
│  2. UPDATE Operations (priority=2)                      │
│     └── Update existing entity fields                   │
│                                                         │
│  3. CREATE Operations (priority=3)                      │
│     └── Create new entity directories                   │
└─────────────────────────────────────────────────────────┘
```

## Usage

```python
from kgraph.pipeline.apply import OperationExecutor

executor = OperationExecutor(config, storage, staging_db, audit_logger)

# Execute all ready operations
result = executor.execute_batch(batch_id="batch_001")

print(f"Successful: {result.successful}")
print(f"Failed: {result.failed}")
print(f"Skipped: {result.skipped}")

# Execute single operation
success = executor.execute_operation(op_id=123)
```

## ExecutionResult

```python
@dataclass
class ExecutionResult:
    batch_id: str
    successful: int
    failed: int
    skipped: int
    errors: list[dict]
```

## Operation Types

### MERGE

Combines a new entity with an existing one:

```python
# Before: existing entity at target_path
# After: contacts merged, alias added, sources updated
```

- Merges contacts (deduplicated by email)
- Adds source name as alias
- Updates sources list
- Sets last_updated timestamp

### UPDATE

Updates fields on an existing entity:

```python
# Before: existing entity at target_path
# After: specific fields updated
```

- Updates non-empty fields
- Preserves existing data
- Sets last_updated timestamp

### CREATE

Creates a new entity:

```python
# Before: no entity
# After: new directory with _meta.json and _summary.md
```

- Creates entity directory
- Writes _meta.json
- Generates _summary.md

## Error Handling

```python
try:
    executor.execute_operation(op_id)
except ExecutionError as e:
    # Operation failed - status updated to 'failed'
    # error_message stored in database
    print(f"Failed: {e.message}")
```

## Audit Trail

Each execution is logged:

```json
{
  "timestamp": "2026-01-05T10:30:00",
  "category": "apply",
  "action": "merge",
  "details": {
    "operation_id": 123,
    "entity": "Acme Corp",
    "target": "customers/strategic/acme_corporation"
  }
}
```
