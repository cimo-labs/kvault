# kvault

Agent-first knowledge graph framework. Build knowledge graphs from unstructured data using intelligent agents.

## Philosophy

**The agent IS the pipeline.** Claude (or another LLM) does extraction, research, decisions, and propagation. kvault provides tools, not workflows.

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

## Getting Started with Claude Code

The fastest way to get a personal knowledge base running with Claude Code:

```bash
# 1. Install kvault with MCP support
pip install kvault[mcp]

# 2. Initialize a new knowledge base
kvault init my_kb --name "Your Name"

# 3. Verify it's clean
kvault check --kb-root my_kb
```

Then add the MCP server to `.claude/settings.json`:

```json
{
  "mcpServers": {
    "kvault": {
      "command": "kvault-mcp",
      "env": {}
    }
  }
}
```

And add the integrity hook (catches stale summaries before each prompt):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "type": "command",
        "command": "kvault check --kb-root /absolute/path/to/my_kb"
      }
    ]
  }
}
```

Customize the generated `CLAUDE.md` with your personal details, then start adding entities.

## Installation

```bash
pip install kvault
```

Or install from source:

```bash
git clone https://github.com/cimo-labs/kvault
cd kvault
pip install -e .
```

## Quick Start

```python
from pathlib import Path
from kvault import (
    EntityIndex,
    SimpleStorage,
    ObservabilityLogger,
    EntityResearcher
)

# Initialize
kg_root = Path("my_knowledge_base")
index = EntityIndex(kg_root / ".kvault" / "index.db")
storage = SimpleStorage(kg_root)
logger = ObservabilityLogger(kg_root / ".kvault" / "logs.db")
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
    "updated": "2026-01-05",
    "source": "email:123",
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
from kvault import EntityIndex

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
from kvault import SimpleStorage

storage = SimpleStorage(Path("knowledge_graph"))

# Create entity
storage.create_entity("people/alice", {
    "created": "2026-01-05",
    "updated": "2026-01-05",
    "source": "manual",
    "aliases": ["Alice"]
}, summary="# Alice\n\nDescription here.")

# Update entity
storage.update_entity("people/alice",
                      meta={"source": "email:123"},
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
from kvault import ObservabilityLogger

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
from kvault import EntityResearcher, EntityIndex

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
from kvault import (
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
black kvault/

# Type check
mypy kvault/
```

## MCP Server (Claude Code Integration)

The kvault MCP server provides direct tool access for Claude Code, enabling the 6-step workflow without subprocess parsing.

### Installation

```bash
pip install kvault[mcp]  # Install with MCP support
```

### Configuration

Add to `.claude/settings.json`:

```json
{
  "mcpServers": {
    "kvault": {
      "command": "kvault-mcp",
      "env": {}
    }
  }
}
```

### Available Tools

| Category | Tools |
|----------|-------|
| **Init** | `kvault_init`, `kvault_status` |
| **Index** | `kvault_search`, `kvault_find_by_alias`, `kvault_find_by_email_domain`, `kvault_rebuild_index` |
| **Entity** | `kvault_read_entity`, `kvault_write_entity`, `kvault_list_entities`, `kvault_delete_entity`, `kvault_move_entity` |
| **Summary** | `kvault_read_summary`, `kvault_write_summary`, `kvault_get_parent_summaries` |
| **Research** | `kvault_research` |
| **Workflow** | `kvault_log_phase`, `kvault_write_journal`, `kvault_validate_transition` |

### Example Workflow

```
1. kvault_init(kg_root="/path/to/kb")
2. kvault_research(name="John Doe", phone="+14155551234")
3. kvault_write_entity(path="people/contacts/john_doe", meta={...}, content="...", create=true)
4. kvault_get_parent_summaries(path="people/contacts/john_doe")
5. kvault_write_summary(path="people/contacts", content="...")
6. kvault_write_journal(actions=[...], source="manual")
7. kvault_rebuild_index()
```

### Benefits

- **Structured JSON responses** - No regex parsing of CLI output
- **Direct control** - Each tool call is explicit and debuggable
- **Session state** - Track workflow progress across calls
- **No timeouts** - Individual tools complete quickly

---

## CLI Usage

```bash
pip install -e ".[dev]"

# Initialize a new KB
kvault init my_kb --name "Alice"

# Check KB integrity (propagation, journal, index, frontmatter, branching)
kvault check --kb-root my_kb
kvault check                      # Auto-detects KB root from cwd

# Process a corpus
kvault process --corpus /path/to/corpus --kg-root /path/to/kg --dry-run
kvault process --corpus /path/to/corpus --kg-root /path/to/kg --apply

# Rebuild and search the index
kvault index rebuild --kg-root /path/to/kg
kvault index search --db /path/to/kg/.kvault/index.db --query "Acme"

# Session summary (observability)
kvault log summary --db /path/to/kg/.kvault/logs.db
```

## License

MIT
