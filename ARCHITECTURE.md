# kvault Architecture

> **Canonical Reference** — This document is the single source of truth for kvault's design.
> Last updated: 2026-02-07

## Overview

kvault is a personal knowledge base that runs inside any MCP-compatible AI tool (Claude Code, OpenAI Codex, Cursor, VS Code + Copilot, etc.). It stores entities as YAML-frontmatter Markdown files with fuzzy deduplication, hierarchical summary propagation, and 17 MCP tools for agent interaction.

### Goals

1. **Agent-Native** — 17 MCP tools; agents operate the KB directly
2. **Structured Memory** — Hierarchical entities with deduplication, not flat notes
3. **Zero Extra Cost** — Uses existing AI tool subscription; no API keys
4. **Auditable** — Complete trail of all decisions in `.kvault/logs.db`
5. **Portable** — Plain Markdown files; works offline, version-controllable

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AI TOOL INTERFACE                                   │
│                                                                              │
│  Claude Code    OpenAI Codex    Cursor    VS Code + Copilot    Windsurf    │
│  (.claude/)     (.codex/)       (.cursor/) (.vscode/)                       │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │ MCP (stdio)
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MCP SERVER (kvault-mcp)                              │
│                                                                              │
│  17 tools: init, search, read/write entity, propagate, journal, validate   │
│  Session state management, workflow enforcement                             │
│  kvault/mcp/server.py                                                       │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
┌───────────────────┐ ┌───────────────────────┐ ┌───────────────────────┐
│ Filesystem Search │ │    SimpleStorage      │ │ ObservabilityLogger   │
│                   │ │                       │ │                       │
│ Scans _summary.md │ │   Filesystem CRUD     │ │ Structured logging    │
│ files directly    │ │   YAML frontmatter    │ │ .kvault/logs.db       │
│ No index needed   │ │   _summary.md files   │ │                       │
│                   │ │                       │ │ kvault/core/           │
│ kvault/core/      │ │   kvault/core/        │ │ observability.py      │
│ search.py         │ │   storage.py          │ │                       │
└───────────────────┘ └───────────────────────┘ └───────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                      KNOWLEDGE BASE (Filesystem)                             │
│                                                                              │
│   my_kb/                                                                     │
│   ├── _summary.md              ← Root: executive overview                   │
│   ├── people/                                                                │
│   │   ├── _summary.md          ← Semantic summary of all people             │
│   │   └── sarah_chen/                                                        │
│   │       └── _summary.md      ← Entity: YAML frontmatter + Markdown       │
│   ├── journal/YYYY-MM/log.md                                                │
│   └── .kvault/                                                               │
│       └── logs.db              ← Observability/audit log                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Descriptions

### Core Layer (`kvault/core/`)

| Component | File | Purpose |
|-----------|------|---------|
| **SimpleStorage** | `storage.py` | Filesystem CRUD for entities and summaries |
| **search** | `search.py` | Filesystem-based search — scans `_summary.md` files directly, no index |
| **ObservabilityLogger** | `observability.py` | Structured logging to `.kvault/logs.db` |
| **parse_frontmatter** | `frontmatter.py` | YAML frontmatter ↔ Markdown parsing |
| **build_frontmatter** | `frontmatter.py` | Markdown ↔ YAML frontmatter serialization |

### MCP Server (`kvault/mcp/`)

| Component | File | Purpose |
|-----------|------|---------|
| **server.py** | `server.py` | 17 MCP tool handlers, session management |
| **SessionState** | `state.py` | Per-session workflow state tracking |
| **validation.py** | `validation.py` | 10 pure validation functions for tool inputs |

### Orchestrator (`kvault/orchestrator/`)

| Component | File | Purpose |
|-----------|------|---------|
| **HeadlessOrchestrator** | `runner.py` | Spawns Claude subprocess for autonomous 5-step workflow |
| **OrchestratorConfig** | `context.py` | Configuration for orchestrator runs |
| **WorkflowStateMachine** | `state_machine.py` | State transitions for the 5-step workflow |
| **WorkflowEnforcer** | `enforcer.py` | Validates workflow compliance |

### CLI (`kvault/cli/`)

| Component | File | Purpose |
|-----------|------|---------|
| **main.py** | `main.py` | CLI entry point (`kvault` command) |
| **check.py** | `check.py` | `kvault check` integrity validation |

---

## Data Flow (MCP-based, primary path)

The agent drives the 5-step workflow through individual MCP tool calls:

```
Agent (Claude Code, Codex, etc.)
    │
    ├── 1. kvault_init(kg_root=".")
    │       → Loads hierarchy, root summary, entity count
    │
    ├── 2. kvault_research(name="...", email="...")
    │       → Scans filesystem, runs matching strategies
    │       → Returns matches with confidence scores
    │
    ├── 3. kvault_write_entity(path="...", meta={...}, content="...")
    │       → Creates/updates entity with YAML frontmatter
    │       → Validates required fields (source, aliases)
    │       → Sets created/updated timestamps
    │       → Returns propagation_needed ancestor list
    │
    ├── 4. kvault_propagate_all(path="...")
    │       → Returns list of ancestor paths with current content
    │       → Agent reads, updates, and writes each summary
    │
    └── 5. kvault_write_journal(actions=[...], source="...")
            → Appends to journal/YYYY-MM/log.md
```

No index rebuild needed — search reads `_summary.md` files directly from disk.

---

## Entity Format

Single `_summary.md` file with YAML frontmatter:

```markdown
---
created: 2026-02-06
updated: 2026-02-06
source: manual
aliases: [Sarah Chen, sarah@anthropic.com]
email: sarah@anthropic.com
relationship_type: colleague
---
# Sarah Chen

Research scientist at Anthropic.
```

**Required fields:** `source`, `aliases`
**Auto-set fields:** `created`, `updated`
**Optional fields:** `phone`, `email`, `relationship_type`, `context`, `related_to`, `last_interaction`, `status`

---

## Decision Log

### Why MCP Server over CLI Orchestrator?

**Decision**: MCP server is the primary interface; orchestrator is available for autonomous batch processing.

**Rationale**:
- Individual tool calls give the agent fine-grained control
- No subprocess timeout issues
- Structured JSON responses (no regex parsing)
- Works with any MCP-compatible tool, not just Claude

### Why Filesystem Search (not SQLite)?

**Decision**: Search scans `_summary.md` files directly. SQLite is used only for observability logs.

**Rationale**:
- At typical KB sizes (< 1,000 entities), filesystem scan is fast (< 200ms)
- Eliminates stale-index bugs entirely — no rebuild step needed
- One fewer moving part; search results are always consistent with disk
- SQLite `.kvault/logs.db` is still used for structured audit logging

### Why Filesystem over Database for Entities?

**Decision**: Entities are plain Markdown files, not database rows.

**Rationale**:
- Git-friendly (version control, diffs, PRs)
- Human-readable without tooling
- Agent can read/write with standard file tools as fallback
- Hierarchical directory structure mirrors the semantic hierarchy

---

## Testing

### Test Structure

```
tests/
├── conftest.py                  # Rich shared fixtures (sample_kb, initialized_kb, empty_kb)
├── fixtures/
│   └── sample_kb/               # 5-entity representative KB for E2E tests
│
├── E2E Tests
│   └── test_e2e_workflows.py    # Complete 5-step workflow pipelines
│
├── Feature Tests
│   ├── test_check.py            # kvault check CLI + propagation staleness detection
│   ├── test_frontmatter.py      # YAML frontmatter parsing
│   ├── test_search.py           # Filesystem search, alias, domain matching
│   └── test_storage.py          # SimpleStorage filesystem
│
└── Regression Tests
    └── test_pressure_fixes.py   # Pressure test regression coverage
```

**133 tests, runs in < 1s.**

```bash
pytest tests/                                     # Run all
pytest tests/test_e2e*.py -q                      # Quick E2E validation
pytest tests/test_mcp*.py -v                      # MCP layer tests
pytest tests/ --ignore=tests/test_orchestrator.py  # Skip flaky orchestrator tests
pytest --cov=kvault --cov-report=term tests/       # With coverage
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1.0 | 2026-01-05 | Initial architecture |
| 0.2.0 | 2026-01-23 | MCP server, YAML frontmatter, 20 tools |
| 0.3.0 | 2026-02-06 | Multi-tool support (Codex, Cursor, VS Code), integrity hook |
| 0.4.0 | 2026-02-07 | Removed SQLite index, filesystem-based search, 17 tools, 5-step workflow, propagation staleness detection |
