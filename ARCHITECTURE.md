# kvault Architecture

> **Canonical Reference** - This document is the single source of truth for kvault's design.
> Last updated: 2026-01-05

## Overview

kvault is a config-driven knowledge graph framework that transforms unstructured data (emails, documents) into structured knowledge using LLM-powered entity extraction with fuzzy deduplication.

### Goals

1. **Autonomous Processing** - High-confidence decisions made automatically
2. **Human-in-the-Loop** - Ambiguous cases surfaced for review
3. **Resumable** - Checkpoint-based processing that survives interruption
4. **Config-Driven** - All behavior controlled via YAML configuration
5. **Auditable** - Complete trail of all decisions

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USER INTERFACE                                  │
│                                                                              │
│  $ kvault process    $ kvault resume    $ kvault review    $ kvault tree    │
└─────────────────────────────────────────┬───────────────────────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              ORCHESTRATOR                                    │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │  SessionManager │  │  BatchScheduler │  │  CheckpointManager          │  │
│  │  (state.json)   │  │  (data source)  │  │  (resume/recovery)          │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────┬───────────────────────────────────┘
                                          │
            ┌─────────────────────────────┼─────────────────────────────┐
            │                             │                             │
            ▼                             ▼                             ▼
┌───────────────────────┐   ┌───────────────────────┐   ┌───────────────────────┐
│   EXTRACTION PHASE    │   │    RESEARCH PHASE     │   │   RECONCILE PHASE     │
│                       │   │                       │   │                       │
│   ExtractionAgent     │──▶│   ResearchAgent       │──▶│   DecisionAgent       │
│   - Claude CLI        │   │   - Alias matching    │   │   - Auto-decide rules │
│   - Structured output │   │   - Fuzzy matching    │   │   - LLM fallback      │
│   - Entity parsing    │   │   - Domain matching   │   │   - Confidence scores │
└───────────────────────┘   └───────────────────────┘   └───────────┬───────────┘
                                                                    │
                                                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           STAGING LAYER (SQLite)                             │
│                                                                              │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌────────────────────┐ │
│  │  staged_operations   │  │   question_queue     │  │    audit_log       │ │
│  │  - entity_data       │  │   - question_text    │  │    - timestamp     │ │
│  │  - action (M/U/C)    │  │   - priority         │  │    - category      │ │
│  │  - confidence        │  │   - suggested_action │  │    - action        │ │
│  │  - status            │  │   - status           │  │    - details       │ │
│  └──────────────────────┘  └──────────────────────┘  └────────────────────┘ │
└─────────────────────────────────────────┬───────────────────────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              APPLY PHASE                                     │
│                                                                              │
│   OperationExecutor                                                          │
│   - Priority ordering: MERGE (1) → UPDATE (2) → CREATE (3)                  │
│   - Atomic writes with rollback                                              │
│   - Index invalidation                                                       │
└─────────────────────────────────────────┬───────────────────────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         KNOWLEDGE GRAPH (Filesystem)                         │
│                                                                              │
│   FilesystemStorage                                                          │
│   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐ │
│   │  Directory Tier │  │  Directory Tier │  │     JSONL Registry          │ │
│   │  (strategic)    │  │  (key)          │  │     (prospects)             │ │
│   │  _meta.json     │  │  _meta.json     │  │     _registry.jsonl         │ │
│   │  _summary.md    │  │  _summary.md    │  │                             │ │
│   └─────────────────┘  └─────────────────┘  └─────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Descriptions

### Core Layer (`kvault/core/`)

| Component | File | Purpose |
|-----------|------|---------|
| **KGraphConfig** | `config.py` | Pydantic v2 configuration with validation |
| **ConfidenceConfig** | `config.py` | Thresholds for auto-decisions |
| **FilesystemStorage** | `storage.py` | Tiered entity storage (directory + JSONL) |

### Matching Layer (`kvault/matching/`)

| Component | File | Score Range | Purpose |
|-----------|------|-------------|---------|
| **AliasMatchStrategy** | `alias.py` | 1.0 | Exact match against known aliases |
| **FuzzyNameMatchStrategy** | `fuzzy.py` | 0.85-0.99 | SequenceMatcher string similarity |
| **EmailDomainMatchStrategy** | `domain.py` | 0.85-0.95 | Shared corporate email domains |

### Pipeline Layer (`kvault/pipeline/`)

| Component | File | Purpose |
|-----------|------|---------|
| **Orchestrator** | `orchestrator.py` | Main coordinator for processing pipeline |
| **SessionManager** | `session.py` | Tracks active session state |
| **CheckpointManager** | `checkpoint.py` | Resume/recovery from interruption |
| **ExtractionAgent** | `agents/extraction.py` | LLM entity extraction via Claude CLI |
| **ResearchAgent** | `agents/research.py` | Find existing entity matches |
| **DecisionAgent** | `agents/decision.py` | Reconciliation decisions |
| **StagingDatabase** | `staging/database.py` | SQLite for staged operations |
| **QuestionQueue** | `staging/question_queue.py` | Human review queue |
| **OperationExecutor** | `apply/executor.py` | Apply changes to knowledge graph |
| **AuditLogger** | `audit/logger.py` | JSONL audit trail |

---

## Data Flow

### 1. Extraction Phase

```
Raw Data (emails, documents)
    │
    ▼
┌─────────────────────────────────────────┐
│ ExtractionAgent                         │
│                                         │
│ INPUT:  List of raw items               │
│ PROMPT: extraction.md template          │
│ LLM:    Claude CLI (headless)           │
│ OUTPUT: List[ExtractedEntity]           │
│         - name, entity_type, tier       │
│         - contacts, confidence          │
└─────────────────────────────────────────┘
    │
    ▼
List[ExtractedEntity]
```

### 2. Research Phase

```
List[ExtractedEntity]
    │
    ▼
┌─────────────────────────────────────────┐
│ ResearchAgent                           │
│                                         │
│ For each entity:                        │
│   1. Load entity index (cached)         │
│   2. Run each matching strategy         │
│   3. Deduplicate, sort by score         │
│                                         │
│ OUTPUT: List[(entity, candidates)]      │
│         candidates: List[MatchCandidate]│
│         - path, name, score, type       │
└─────────────────────────────────────────┘
    │
    ▼
List[(ExtractedEntity, List[MatchCandidate])]
```

### 3. Reconciliation Phase

```
List[(ExtractedEntity, List[MatchCandidate])]
    │
    ▼
┌─────────────────────────────────────────┐
│ DecisionAgent                           │
│                                         │
│ Auto-decide rules (ConfidenceConfig):   │
│   - Alias match (1.0) → MERGE           │
│   - Score >= 0.95 → MERGE               │
│   - Email domain >= 0.90 → UPDATE       │
│   - Score < 0.50 → CREATE               │
│   - Otherwise → LLM decides             │
│                                         │
│ OUTPUT: List[ReconcileDecision]         │
│         - action, target, confidence    │
│         - needs_review flag             │
└─────────────────────────────────────────┘
    │
    ▼
List[ReconcileDecision]
```

### 4. Staging Phase

```
List[ReconcileDecision]
    │
    ▼
┌─────────────────────────────────────────┐
│ StagingDatabase                         │
│                                         │
│ High confidence (>=0.95):               │
│   → Stage as "ready"                    │
│                                         │
│ Needs review (0.50-0.95):               │
│   → Add to question_queue               │
│   → Stage as "pending_review"           │
│                                         │
│ Low confidence (<0.50):                 │
│   → Stage as "ready" (auto-create)      │
└─────────────────────────────────────────┘
    │
    ▼
SQLite: staged_operations + question_queue
```

### 5. Apply Phase

```
staged_operations WHERE status='ready'
    │
    ▼
┌─────────────────────────────────────────┐
│ OperationExecutor                       │
│                                         │
│ Order by priority:                      │
│   1. MERGE (priority=1)                 │
│   2. UPDATE (priority=2)                │
│   3. CREATE (priority=3)                │
│                                         │
│ For each operation:                     │
│   1. Execute against FilesystemStorage  │
│   2. Update status to "applied"         │
│   3. Invalidate research cache          │
│   4. Log to audit trail                 │
└─────────────────────────────────────────┘
    │
    ▼
Knowledge Graph (filesystem)
```

---

## Configuration Reference

### kvault.yaml Structure

```yaml
project:
  name: "My Knowledge Graph"
  data_path: "./data"
  kg_path: "./knowledge_graph"
  prompts_path: "./prompts"

entity_types:
  customer:
    directory: "customers"
    tier_field: "tier"
    required_fields: [name]

tiers:
  strategic:
    storage_type: directory
    criteria:
      revenue_min: 200000
  prospect:
    storage_type: jsonl
    criteria:
      revenue: 0

confidence:
  auto_merge: 0.95    # Score >= this: auto-merge
  auto_update: 0.90   # Score >= this: auto-update
  auto_create: 0.50   # Score < this: auto-create new
  llm_min: 0.50       # Min score for LLM review range
  llm_max: 0.95       # Max score for LLM review range

matching:
  strategies:
    - alias
    - fuzzy_name
    - email_domain
  fuzzy_threshold: 0.85

processing:
  batch_size: 500
  objective_interval: 5

agent:
  provider: claude
  timeout: 120
```

### Confidence Thresholds

| Threshold | Default | Meaning |
|-----------|---------|---------|
| `auto_merge` | 0.95 | Score >= this triggers automatic merge |
| `auto_update` | 0.90 | Score >= this triggers automatic update |
| `auto_create` | 0.50 | Score < this triggers automatic create |
| `llm_min` | 0.50 | Lower bound of LLM decision range |
| `llm_max` | 0.95 | Upper bound of LLM decision range |

---

## Key Data Models

### ExtractedEntity

```python
@dataclass
class ExtractedEntity:
    """Entity extracted from raw data by LLM."""
    name: str                           # Normalized entity name
    entity_type: str                    # customer, supplier, person, etc.
    tier: Optional[str] = None          # strategic, key, standard, prospect
    industry: Optional[str] = None      # robotics, automotive, medical, etc.
    contacts: List[Dict] = field(default_factory=list)  # [{name, email, role}]
    confidence: float = 0.5             # LLM's extraction confidence
    source_id: Optional[str] = None     # ID from source data
    raw_data: Dict[str, Any] = field(default_factory=dict)
```

### MatchCandidate

```python
@dataclass
class MatchCandidate:
    """Potential match from research phase."""
    candidate_path: str                 # e.g., "customers/strategic/acme_corp"
    candidate_name: str                 # Display name
    match_type: str                     # alias, fuzzy_name, email_domain
    match_score: float                  # 0.0-1.0
    match_details: Dict[str, Any]       # Strategy-specific details
```

### ReconcileDecision

```python
@dataclass
class ReconcileDecision:
    """Decision about how to handle an extracted entity."""
    entity_name: str                    # Name of extracted entity
    action: str                         # "merge" | "update" | "create"
    target_path: Optional[str] = None   # Target for merge/update
    confidence: float = 0.5             # Decision confidence
    reasoning: str = ""                 # Why this decision
    needs_review: bool = False          # Queue for human review?
    source_entity: Optional[ExtractedEntity] = None
    candidates: List[MatchCandidate] = field(default_factory=list)
```

---

## Database Schema

### staged_operations

Holds all pending and completed operations.

```sql
CREATE TABLE staged_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    action TEXT NOT NULL,           -- merge, update, create
    target_path TEXT,               -- NULL for create
    confidence REAL NOT NULL,
    reasoning TEXT,
    entity_data TEXT NOT NULL,      -- JSON blob
    candidates_data TEXT,           -- JSON blob
    status TEXT DEFAULT 'staged',   -- staged, ready, applied, failed, rejected
    priority INTEGER DEFAULT 3,     -- 1=merge, 2=update, 3=create
    created_at TEXT NOT NULL,
    applied_at TEXT,
    error_message TEXT
);
```

### question_queue

Human review queue for ambiguous decisions.

```sql
CREATE TABLE question_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    staged_op_id INTEGER REFERENCES staged_operations(id),
    question_type TEXT NOT NULL,    -- entity_dedup, tier_classification, etc.
    question_text TEXT NOT NULL,
    context TEXT,
    suggested_action TEXT,
    confidence REAL,
    priority INTEGER DEFAULT 50,    -- Higher = more urgent
    status TEXT DEFAULT 'pending',  -- pending, answered, skipped, deferred
    user_answer TEXT,
    answered_at TEXT,
    created_at TEXT NOT NULL
);
```

---

## Extension Points

### Custom Matching Strategy

```python
from kvault.matching import MatchStrategy, register_strategy, MatchCandidate

@register_strategy("semantic")
class SemanticMatchStrategy(MatchStrategy):
    @property
    def name(self) -> str:
        return "semantic"

    @property
    def score_range(self) -> tuple[float, float]:
        return (0.7, 0.95)

    def find_matches(self, entity, index, threshold=0.0) -> list[MatchCandidate]:
        # Your embedding-based matching logic
        ...
```

### Custom Storage Backend

```python
from kvault.core.storage import StorageInterface

class PostgresStorage(StorageInterface):
    def write_entity(self, entity_type, entity_id, data, tier=None):
        # Write to PostgreSQL
        ...

    def read_entity(self, entity_type, entity_id, tier=None):
        # Read from PostgreSQL
        ...
```

### Custom Data Source

```python
from kvault.pipeline.data_sources import DataSource

class EmailDataSource(DataSource):
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(db_path)

    def get_batch(self, batch_size: int, offset: int) -> list[dict]:
        # Fetch emails from SQLite
        ...

    def count(self) -> int:
        # Total emails to process
        ...
```

---

## Decision Log

### Why Claude CLI over Agent SDK?

**Decision**: Use `claude -p` CLI invocation instead of Python Agent SDK.

**Rationale**:
- Simpler dependency (just subprocess)
- Works with existing Claude Code installation
- Structured output via `--output-format json`
- Easier debugging (can test prompts manually)

**Trade-off**: Less fine-grained control than SDK, but sufficient for our use case.

### Why SQLite for Staging?

**Decision**: Use SQLite for staged operations and question queue.

**Rationale**:
- Zero configuration
- ACID guarantees for concurrent access
- Easy to inspect (any SQLite browser)
- File-based = portable with project

**Trade-off**: Not horizontally scalable, but single-user processing is the target.

### Why Priority-Ordered Execution?

**Decision**: Execute merges before updates before creates.

**Rationale**:
- Merges reduce entity count (more efficient)
- Updates depend on existing entities
- Creates are independent (last is safest)

### Why Confidence-Based Auto-Decide?

**Decision**: Auto-decide at extremes, LLM in the middle.

**Rationale**:
- 95%+ matches are almost certainly correct → auto-merge
- <50% matches are almost certainly new → auto-create
- Middle range is genuinely ambiguous → worth LLM cost

---

## Testing

### Test Structure

```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures
├── fixtures/
│   ├── __init__.py
│   ├── sample_config.yaml   # Test configuration
│   └── sample_emails.json   # 10 sample emails
├── test_e2e_pipeline.py     # End-to-end tests
├── test_agents.py           # Agent unit tests
└── test_staging.py          # Staging layer tests
```

### Test Categories

| File | Tests | Purpose |
|------|-------|---------|
| `test_e2e_pipeline.py` | 6 | Full pipeline: extract → stage → apply |
| `test_agents.py` | 15+ | Data models, MockExtractionAgent |
| `test_staging.py` | 15+ | StagingDatabase, QuestionQueue |

### Key Testing Pattern: MockExtractionAgent

Tests avoid Claude CLI dependency by using `MockExtractionAgent`:

```python
class MockExtractionAgent(ExtractionAgent):
    """Mock agent for testing without Claude CLI."""

    def __init__(self, config, mock_entities=None):
        super().__init__(config)
        self.mock_entities = mock_entities or []

    def _call_llm(self, prompt: str) -> str:
        return json.dumps({"entities": self.mock_entities})
```

Usage in tests:

```python
@pytest.fixture
def orchestrator_with_mock(temp_config, mock_entities):
    orch = Orchestrator(config=temp_config, ...)
    orch.extraction_agent = MockExtractionAgent(
        temp_config, mock_entities=mock_entities
    )
    return orch
```

### E2E Test Scenarios

1. **New Entity Creation**: Unknown company → CREATE → verify in KG
2. **Merge Detection**: Fuzzy match (0.95) → MERGE → verify contacts merged
3. **Human Review Queue**: Ambiguous match → pending_review → question created
4. **Review and Apply**: Answer question → status transition → apply
5. **Session Resume**: State tracking and checkpoint restoration

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=kvault --cov-report=term-missing

# Run E2E only
pytest tests/test_e2e_pipeline.py -v

# Run unit tests only
pytest tests/ --ignore=tests/test_e2e_pipeline.py -v
```

### Pre-commit Hooks

`.pre-commit-config.yaml` runs on every commit:

1. **pytest** - All tests must pass
2. **mypy** - Type checking (kvault/ directory)
3. **ruff** - Linting and formatting
4. **pre-commit-hooks** - Trailing whitespace, YAML check, etc.

```bash
# Install hooks
pre-commit install

# Run manually
pre-commit run --all-files
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1.0 | 2026-01-05 | Initial architecture document |
| 0.1.1 | 2026-01-05 | Added testing section |
