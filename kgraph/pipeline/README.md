# Pipeline Module

Orchestration layer for entity processing.

## Overview

The pipeline processes unstructured data through a series of phases:

```
Data Source → Extract → Research → Reconcile → Stage → Apply → Knowledge Graph
```

## Components

| Component | File | Purpose |
|-----------|------|---------|
| **Orchestrator** | `orchestrator.py` | Main coordinator |
| **SessionManager** | `session.py` | Session state tracking |
| **CheckpointManager** | `checkpoint.py` | Resume/recovery |

## Submodules

```
pipeline/
├── __init__.py         # Exports Orchestrator, agents, staging
├── orchestrator.py     # Main pipeline coordinator
├── session.py          # Session state (JSON)
├── checkpoint.py       # Checkpoint management
├── agents/             # LLM-powered agents
├── apply/              # Execution layer
├── audit/              # Audit logging
└── staging/            # Staging database
```

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                      Orchestrator                            │
│                                                              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐ │
│  │ Extract  │ → │ Research │ → │ Reconcile│ → │  Stage   │ │
│  │  Agent   │   │  Agent   │   │  Agent   │   │          │ │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘ │
│                                                    │        │
│                                              ┌─────▼──────┐ │
│                                              │   Apply    │ │
│                                              │  Executor  │ │
│                                              └────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## Usage

```python
from kgraph.pipeline import Orchestrator

# Initialize
orchestrator = Orchestrator(
    config=config,
    kg_path=config.kg_path,
    data_dir=Path(".kgraph"),
)

# Process items
result = orchestrator.process(
    items=[{"id": "email_001", "body": "..."}],
    source_name="emails",
    auto_apply=True,
    batch_size=100,
)

print(f"Extracted: {result.entities_extracted}")
print(f"Applied: {result.operations_applied}")

# Resume interrupted session
result = orchestrator.resume(session_id="session_001")

# Review pending questions
question = orchestrator.review_next(batch_id="batch_001")
if question:
    orchestrator.answer_question(question["question_id"], "approve")
```

## Session States

```
INITIALIZING → EXTRACTING → RESEARCHING → RECONCILING → STAGING → APPLYING → COMPLETED
                                                          ↓
                                                     REVIEWING
```

## Checkpoints

Checkpoints enable resume after interruption:

```json
{
  "session_id": "session_001",
  "batch_id": "batch_001",
  "phase": "reconciling",
  "items_processed": 150,
  "entities_extracted": 23,
  "last_checkpoint": "2026-01-05T10:30:00"
}
```

## Configuration Impact

```yaml
processing:
  batch_size: 500         # Items per batch
  objective_interval: 5   # Progress updates

confidence:
  auto_merge: 0.95        # Auto-merge threshold
  auto_update: 0.90       # Auto-update threshold
  auto_create: 0.50       # Auto-create threshold
```
