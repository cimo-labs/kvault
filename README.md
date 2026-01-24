# kgraph

Agent-first knowledge graph framework. Build knowledge graphs from unstructured data using intelligent agents.

## Philosophy

**The agent IS the pipeline.** Claude (or another LLM) does extraction, research, decisions, and propagation. kgraph provides tools, not workflows.

```
┌─────────────────────────────────────────────────────────────┐
│  EntityIndex    MatchStrategies    ObservabilityLogger      │
│  (fast lookup)  (fuzzy, alias)     (debug & improve)        │
│                                                             │
│  SimpleStorage  (YAML frontmatter in _summary.md preferred) │
└─────────────────────────────────────────────────────────────┘

Agent (Claude) does:
  - Read input
  - Research (using EntityIndex + MatchStrategies)
  - Decide (using its reasoning)
  - Write (using SimpleStorage)
  - Propagate (update parent summaries)
  - Log (using ObservabilityLogger)
```

## Installation

```bash
pip install kgraph
```

Or install from source:

```bash
git clone https://github.com/eddiel/kgraph
cd kgraph
pip install -e .
```

## Quick Start

```python
from pathlib import Path
from kgraph import (
    EntityIndex,
    SimpleStorage,
    ObservabilityLogger,
    EntityResearcher
)

# Initialize
kg_root = Path("my_knowledge_base")
index = EntityIndex(kg_root / ".kgraph" / "index.db")
storage = SimpleStorage(kg_root)
logger = ObservabilityLogger(kg_root / ".kgraph" / "logs.db")
researcher = EntityResearcher(index)

# 1. Research - find existing entities
matches = researcher.research("Alice Smith", email="alice@anthropic.com")
action, target, confidence = researcher.suggest_action("Alice Smith")
logger.log_research("Alice Smith", "alice smith",
                    [m.__dict__ for m in matches], action)

# 2. Decide - agent determines what to do
if action == "create":
    entity_path = "people/collaborators/alice_smith"
    logger.log_decide("Alice Smith", "create",
                      "No existing match found", confidence)

# 3. Write - create/update the entity
storage.create_entity(entity_path, {
    "created": "2026-01-05",
    "last_updated": "2026-01-05",
    "sources": ["email:123"],
    "aliases": ["Alice", "alice@anthropic.com"]
}, summary="# Alice Smith\n\nResearch scientist at Anthropic.")
logger.log_write(entity_path, "create", "Created new entity")

# 4. Update index
index.add(entity_path, "Alice Smith",
          ["Alice", "alice@anthropic.com"], "people")

# 5. Propagate - update parent summaries
ancestors = storage.get_ancestors(entity_path)
logger.log_propagate(entity_path, ancestors)
```

## Core Components

### EntityIndex

SQLite-backed entity index with full-text search for fast lookups.

```python
from kgraph import EntityIndex

index = EntityIndex(Path("index.db"))

# Add entity
index.add("people/alice", "Alice Smith",
          aliases=["Alice", "alice@example.com"],
          category="people")

# Search
results = index.search("Alice")

# Find by alias
entry = index.find_by_alias("alice@example.com")

# Find by email domain
entries = index.find_by_email_domain("example.com")

# Rebuild from filesystem
count = index.rebuild(Path("knowledge_graph"))
```

### SimpleStorage

Filesystem storage with minimal 4-field schema.

```python
from kgraph import SimpleStorage

storage = SimpleStorage(Path("knowledge_graph"))

# Create entity
storage.create_entity("people/alice", {
    "created": "2026-01-05",
    "last_updated": "2026-01-05",
    "sources": ["manual"],
    "aliases": ["Alice"]
}, summary="# Alice\n\nDescription here.")

# Update entity
storage.update_entity("people/alice",
                      meta={"sources": ["manual", "email:123"]},
                      summary="# Alice\n\nUpdated description.")

# Read
meta = storage.read_meta("people/alice")
summary = storage.read_summary("people/alice")

# Navigate hierarchy
ancestors = storage.get_ancestors("people/collaborators/alice")
# Returns: ["people/collaborators", "people"]
```

### ObservabilityLogger

Phase-based logging for debugging and system improvement.

```python
from kgraph import ObservabilityLogger

logger = ObservabilityLogger(Path("logs.db"))

# Log phases
logger.log_input([{"name": "Alice"}], source="email")
logger.log_research("Alice", "alice", matches, "create")
logger.log_decide("Alice", "create", "No match found", confidence=0.95)
logger.log_write("people/alice", "create", "Created entity")
logger.log_propagate("people/alice", ["people"])
logger.log_error("validation_failed", entity="Alice",
                 details={"field": "email"})

# Query logs
errors = logger.get_errors()
decisions = logger.get_decisions(action="create")
low_conf = logger.get_low_confidence(threshold=0.7)
summary = logger.get_session_summary()
```

### EntityResearcher

Research existing entities before creating new ones.

```python
from kgraph import EntityResearcher, EntityIndex

index = EntityIndex(Path("index.db"))
researcher = EntityResearcher(index)

# Find matches
matches = researcher.research("Alice Smith", email="alice@example.com")

# Get suggestion
action, path, confidence = researcher.suggest_action("Alice Smith")
# Returns: ("create", None, 0.95)  or  ("update", "people/alice", 0.90)

# Quick checks
exists = researcher.exists("Alice Smith", threshold=0.9)
best = researcher.best_match("Alice Smith")
```

### Matching Strategies

Pluggable strategies for entity deduplication.

```python
from kgraph import (
    AliasMatchStrategy,
    FuzzyNameMatchStrategy,
    EmailDomainMatchStrategy
)

# Alias matching - exact match (score: 1.0)
alias_strategy = AliasMatchStrategy()

# Fuzzy name matching (score: 0.85-0.99)
fuzzy_strategy = FuzzyNameMatchStrategy(threshold=0.85)

# Email domain matching (score: 0.85-0.95)
domain_strategy = EmailDomainMatchStrategy()
```

## Storage Format

### YAML Frontmatter (Preferred)

Entities are stored as a single `_summary.md` file with YAML frontmatter:

```markdown
---
created: 2026-01-05
updated: 2026-01-05
source: email:123
aliases: [Alice, alice@anthropic.com, +14155551234]
phone: +14155551234
email: alice@anthropic.com
relationship_type: colleague
context: Met at NeurIPS 2024
---

# Alice Smith

Research scientist at Anthropic working on causal discovery.

## Background
Collaborator on interpretability project.

## Interactions
- 2026-01-05: Initial contact logged

## Notes
- Interested in causal representation learning
```

**Required fields:** `created`, `updated`, `source`, `aliases`
**Optional fields:** `phone`, `email`, `relationship_type`, `context`, `related_to`, `last_interaction`, `status`

### Legacy Format (_meta.json)

Separate `_meta.json` files are still supported for backward compatibility:

```json
{
  "created": "2026-01-05",
  "last_updated": "2026-01-05",
  "sources": ["email:123"],
  "aliases": ["Alice", "alice@anthropic.com"]
}
```

**Note:** New entities should use YAML frontmatter. The index rebuilder supports both formats.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black kgraph/

# Type check
mypy kgraph/
```

## CLI Usage

Install in development mode and use the `kgraph` CLI to process a corpus, manage the index, and view logs.

```
pip install -e .[dev]

# 1) Dry-run a corpus (no writes)
kgraph process --corpus /path/to/corpus --kg-root /path/to/kg --dry-run

# 2) Apply changes (create/update entities, update index/logs)
kgraph process --corpus /path/to/corpus --kg-root /path/to/kg --apply

# Options
#   --include-ext ".txt,.md"     File extensions to include
#   --min-update-score 0.9        Threshold for auto-update vs review
#   --limit-files 100             Process at most N files

# 3) Rebuild and search the index
kgraph index rebuild --kg-root /path/to/kg
kgraph index search --db /path/to/kg/.kgraph/index.db --query "Acme"

# 4) Session summary (observability)
kgraph log summary --db /path/to/kg/.kgraph/logs.db
```

Notes:
- The corpus processor uses heuristics to discover people and orgs from emails in `.txt/.md` files. It researches/deduplicates against the local index, writes entities with YAML frontmatter, and logs actions.
- All writes require `--apply`. Without it, the command prints a JSON plan and does not modify the filesystem.
- Entities are stored under `people/<id>/` and `orgs/<id>/`. The index and logs live under `<kg-root>/.kgraph/`.

## License

MIT
