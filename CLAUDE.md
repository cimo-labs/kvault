# kvault - Personal Knowledge Base for Claude Code

## Overview

kvault is a personal knowledge base that runs inside Claude Code (or OpenAI Codex). It provides entity storage, hierarchical navigation, and 15 MCP tools for structured agent memory.

## Quick Start

```bash
pip install -e ".[dev]"  # Install with dev dependencies
pytest                    # Run tests
```

## Architecture

```
kvault/
├── core/           # Storage, frontmatter, observability
├── orchestrator/   # Headless workflow runner
└── cli/            # Command-line interface
```

## Key Components

### SimpleStorage + scan_entities (core/storage.py)
File-based entity storage with read/write operations and entity scanning.

```python
from kvault.core.storage import SimpleStorage, scan_entities

storage = SimpleStorage(kg_root)
entities = scan_entities(kg_root)         # Full entity scan
```

### HeadlessOrchestrator (orchestrator/runner.py)
Spawns Claude subprocess to execute the 5-step workflow autonomously.

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

## The 4-Step Workflow

The orchestrator enforces this workflow for all knowledge graph updates:

1. **NAVIGATE** - Browse the tree, read parent summaries to find existing entities
2. **WRITE** - Create/update entity files with YAML frontmatter
3. **PROPAGATE** - Update ancestor `_summary.md` files
4. **LOG** - Add entry to `journal/YYYY-MM/log.md`

Agents use their own Grep/Glob/Read tools for searching. `kvault_read_entity` includes the parent summary for sibling context.

## CLI Commands

```bash
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

### Tools (15 total)

**Entity:** `kvault_read_entity` (includes parent summary), `kvault_write_entity`, `kvault_list_entities`, `kvault_delete_entity`, `kvault_move_entity`
**Summary:** `kvault_read_summary`, `kvault_write_summary`, `kvault_get_parent_summaries`, `kvault_propagate_all`
**Workflow:** `kvault_log_phase`, `kvault_write_journal`, `kvault_validate_transition`
**Validation:** `kvault_validate_kb`, `kvault_status`
**Init:** `kvault_init`

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

## Development

```bash
ruff check . && black . && mypy .  # Lint, format, type-check
pytest -v                           # Run tests with verbose output
```

**Before committing:** Always run `black kvault/ tests/` to ensure CI passes. CI runs `black --check` and will reject unformatted code.

## Do Not

- Create separate `_meta.json` files (use frontmatter instead)
- Merge entities without exact identifier match
- Skip the PROPAGATE step (summaries must stay in sync)
- Modify entity files without going through the workflow
- Commit without running `black` first
