# Staging Module

SQLite-based staging layer for operations and human review.

## Overview

All entity changes are staged before being applied to the knowledge graph. This provides:

- **Atomic batching** - Group related operations
- **Human review** - Queue ambiguous decisions
- **Audit trail** - Track all decisions
- **Rollback** - Reject operations before apply

## Components

| Component | File | Purpose |
|-----------|------|---------|
| **StagingDatabase** | `database.py` | Operation staging |
| **QuestionQueue** | `question_queue.py` | Human review queue |

## Database Schema

### staged_operations

```sql
CREATE TABLE staged_operations (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    action TEXT NOT NULL,        -- merge, update, create
    target_path TEXT,            -- NULL for create
    confidence REAL NOT NULL,
    reasoning TEXT,
    entity_data TEXT NOT NULL,   -- JSON blob
    candidates_data TEXT,        -- JSON blob
    status TEXT DEFAULT 'staged',
    priority INTEGER DEFAULT 3,  -- 1=merge, 2=update, 3=create
    created_at TEXT,
    applied_at TEXT,
    error_message TEXT
);
```

### question_queue

```sql
CREATE TABLE question_queue (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL,
    staged_op_id INTEGER REFERENCES staged_operations(id),
    question_type TEXT NOT NULL,
    question_text TEXT NOT NULL,
    suggested_action TEXT,
    confidence REAL,
    priority INTEGER DEFAULT 50,
    status TEXT DEFAULT 'pending',
    user_answer TEXT,
    created_at TEXT
);
```

## Operation Statuses

| Status | Meaning |
|--------|---------|
| `staged` | Initial state |
| `ready` | Approved for execution |
| `pending_review` | Awaiting human review |
| `applied` | Successfully applied |
| `failed` | Error during application |
| `rejected` | Human rejected |

## Usage

### StagingDatabase

```python
from kgraph.pipeline import StagingDatabase

db = StagingDatabase(Path(".kgraph/staging.db"))

# Stage an operation
op_id = db.stage_operation(
    batch_id="batch_001",
    entity_name="Acme Corp",
    action="merge",
    entity_data={"name": "Acme Corp", "contacts": [...]},
    confidence=0.95,
    target_path="customers/strategic/acme_corporation",
)

# Get ready operations (ordered by priority)
ready = db.get_ready_operations(batch_id="batch_001")

# Update status
db.update_status(op_id, "applied")

# Get batch summary
counts = db.count_by_batch("batch_001")
# {"staged": 5, "ready": 3, "applied": 2}
```

### QuestionQueue

```python
from kgraph.pipeline import QuestionQueue

queue = QuestionQueue(Path(".kgraph/staging.db"))

# Add a question
q_id = queue.add_question(
    batch_id="batch_001",
    staged_op_id=op_id,
    question_type="confirm_merge",
    question_text="Merge 'Acme Corp' with 'Acme Corporation'?",
    suggested_action="merge",
    confidence=0.65,
)

# Get pending questions (ordered by priority)
pending = queue.get_pending(batch_id="batch_001")

# Answer a question
queue.answer(q_id, "approve")

# Skip a question
queue.skip(q_id)

# Count pending
count = queue.count_pending(batch_id="batch_001")
```

## Priority Ordering

Operations are executed in priority order:
1. **MERGE** (priority=1) - Reduces entity count
2. **UPDATE** (priority=2) - Modifies existing
3. **CREATE** (priority=3) - Adds new entities

## Question Priority

Lower confidence = higher urgency = lower priority number:

```python
confidence 0.3 → priority 30  # More urgent
confidence 0.7 → priority 70  # Less urgent
```
