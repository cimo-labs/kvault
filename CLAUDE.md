# kgraph - Agent-First Knowledge Graph Framework

## Overview

kgraph is a Python framework for building and maintaining knowledge graphs with AI agent integration. It provides entity storage, indexing, matching strategies, and an orchestrator for automated knowledge curation.

## Quick Start

```bash
pip install -e ".[dev]"  # Install with dev dependencies
pytest                    # Run tests
```

## Architecture

```
kgraph/
├── core/           # Storage, indexing, observability
├── matching/       # Entity matching strategies
├── orchestrator/   # Headless workflow runner
└── cli/            # Command-line interface
```

## Key Components

### EntityIndex (core/index.py)
SQLite-backed full-text search index for entity lookup.

```python
from kgraph.core.index import EntityIndex
index = EntityIndex(Path(".kgraph/index.db"))
index.rebuild(kg_root)  # Scans for entities
results = index.search("query")
```

### SimpleStorage (core/storage.py)
File-based entity storage with read/write operations.

### HeadlessOrchestrator (orchestrator/runner.py)
Spawns Claude subprocess to execute the 6-step workflow autonomously.

```python
from kgraph.orchestrator import HeadlessOrchestrator, OrchestratorConfig
config = OrchestratorConfig(kg_root=Path("."))
orchestrator = HeadlessOrchestrator(config)
result = orchestrator.ingest(content="...", source="manual")
```

## Entity Format (YAML Frontmatter)

**Preferred format**: Single `_summary.md` file with YAML frontmatter.

```markdown
---
created: 2026-01-23
updated: 2026-01-23
source: imessage:abc123
aliases: [John, john@example.com, +14155551234]
phone: +14155551234
email: john@example.com
relationship_type: colleague
context: ex-Stitch Fix
---

# John Doe

**Relationship:** Colleague

## Background
[content]
```

**Required fields**: `created`, `updated`, `source`, `aliases`
**Optional fields**: `phone`, `email`, `relationship_type`, `context`, `related_to`, `last_interaction`, `status`

**Legacy format**: Separate `_meta.json` files are still supported for backward compatibility but should not be used for new entities.

## The 6-Step Workflow

The orchestrator enforces this workflow for all knowledge graph updates:

1. **RESEARCH** - Search index for existing entities, extract identifiers
2. **DECIDE** - Output ActionPlan with create/update/delete/skip actions
3. **EXECUTE** - Write entity files with YAML frontmatter
4. **PROPAGATE** - Update ancestor `_summary.md` files
5. **LOG** - Add entry to `journal/YYYY-MM/log.md`
6. **REBUILD** - Rebuild index if new entities created

## CLI Commands

```bash
# Index operations
kgraph index rebuild --kg-root .
kgraph index search --db .kgraph/index.db --query "term"

# Orchestrator
kgraph orchestrate ingest --kg-root . --content "..." --source "manual"
kgraph orchestrate process --kg-root . --name "Entity" --type "person"

# Observability
kgraph log summary --db .kgraph/logs.db
```

## Important Patterns

### Entity Matching
Always verify identifiers (phone, email) EXACTLY before claiming entity match. Never merge entities based on name similarity alone.

### Frontmatter Parsing
```python
from kgraph.core.frontmatter import parse_frontmatter, build_frontmatter

content = open("_summary.md").read()
meta, body = parse_frontmatter(content)  # Returns (dict, str)
```

### Index Rebuild
The index parses YAML frontmatter first, falls back to `_meta.json`. Phone and email fields are automatically added to aliases for matching.

## Development

```bash
ruff check . && black . && mypy .  # Lint, format, type-check
pytest -v                           # Run tests with verbose output
```

## Do Not

- Create separate `_meta.json` files (use frontmatter instead)
- Merge entities without exact identifier match
- Skip the PROPAGATE step (summaries must stay in sync)
- Modify entity files without going through the workflow
