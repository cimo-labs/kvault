# kvault - Personal Knowledge Base for Claude Code

## Overview

kvault is a personal knowledge base that runs inside Claude Code (or OpenAI Codex). It provides entity storage, indexing, matching strategies, and 20 MCP tools for structured, searchable agent memory.

## Quick Start

```bash
pip install -e ".[dev]"  # Install with dev dependencies
pytest                    # Run tests
```

## Architecture

```
kvault/
├── core/           # Storage, indexing, observability
├── matching/       # Entity matching strategies
├── orchestrator/   # Headless workflow runner
└── cli/            # Command-line interface
```

## Key Components

### EntityIndex (core/index.py)
SQLite-backed full-text search index for entity lookup.

```python
from kvault.core.index import EntityIndex
index = EntityIndex(Path(".kvault/index.db"))
index.rebuild(kg_root)  # Scans for entities
results = index.search("query")
```

### SimpleStorage (core/storage.py)
File-based entity storage with read/write operations.

### HeadlessOrchestrator (orchestrator/runner.py)
Spawns Claude subprocess to execute the 6-step workflow autonomously.

```python
from kvault.orchestrator import HeadlessOrchestrator, OrchestratorConfig
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
kvault index rebuild --kg-root .
kvault index search --db .kvault/index.db --query "term"

# Observability
kvault log summary --db .kvault/logs.db

# MCP Server
kvault-mcp  # Start MCP server for Claude Code
```

## MCP Server (Preferred)

The MCP server provides direct tool access for any MCP-compatible AI tool (Claude Code, Codex, Cursor, VS Code + Copilot, etc.).

### Installation

```bash
pip install knowledgevault[mcp]
```

### Configuration (.claude/settings.json)

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

### Tools (20 total)

**Initialization:** `kvault_init`, `kvault_status`
**Index:** `kvault_search`, `kvault_find_by_alias`, `kvault_find_by_email_domain`, `kvault_rebuild_index`
**Entity:** `kvault_read_entity`, `kvault_write_entity`, `kvault_list_entities`, `kvault_delete_entity`, `kvault_move_entity`
**Summary:** `kvault_read_summary`, `kvault_write_summary`, `kvault_get_parent_summaries`, `kvault_propagate_all`
**Research:** `kvault_research`
**Workflow:** `kvault_log_phase`, `kvault_write_journal`, `kvault_validate_transition`
**Validation:** `kvault_validate_kb`

### Key Differences from CLI Orchestrator

| CLI Orchestrator | MCP Server |
|------------------|------------|
| Single subprocess, parses output | Individual tool calls |
| 10-15 min timeout | No timeout concerns |
| Regex-based path extraction | Structured JSON responses |
| Single session | Session state management |

## Important Patterns

### Entity Matching
Always verify identifiers (phone, email) EXACTLY before claiming entity match. Never merge entities based on name similarity alone.

### Frontmatter Parsing
```python
from kvault.core.frontmatter import parse_frontmatter, build_frontmatter

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
