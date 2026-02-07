# kvault Architecture

> **Canonical Reference** — This document is the single source of truth for kvault's design.
> Last updated: 2026-02-06

## Overview

kvault is a personal knowledge base that runs inside any MCP-compatible AI tool (Claude Code, OpenAI Codex, Cursor, VS Code + Copilot, etc.). It stores entities as YAML-frontmatter Markdown files with fuzzy deduplication, hierarchical summary propagation, and 20 MCP tools for agent interaction.

### Goals

1. **Agent-Native** — 20 MCP tools; agents operate the KB directly
2. **Structured Memory** — Hierarchical entities with deduplication, not flat notes
3. **Zero Extra Cost** — Uses existing AI tool subscription; no API keys
4. **Auditable** — Complete trail of all decisions in `.kvault/logs.db`
5. **Portable** — Plain Markdown + SQLite; works offline, version-controllable

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
│  20 tools: init, search, read/write entity, propagate, journal, validate   │
│  Session state management, workflow enforcement                             │
│  kvault/mcp/server.py (52KB)                                               │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
┌───────────────────┐ ┌───────────────────────┐ ┌───────────────────────┐
│   EntityIndex     │ │    SimpleStorage      │ │   EntityResearcher    │
│                   │ │                       │ │                       │
│   SQLite FTS5     │ │   Filesystem CRUD     │ │   Multi-strategy     │
│   .kvault/index.db│ │   YAML frontmatter    │ │   matching engine    │
│                   │ │   _summary.md files   │ │                       │
│   kvault/core/    │ │   kvault/core/        │ │   kvault/core/        │
│   index.py        │ │   storage.py          │ │   research.py         │
└───────────────────┘ └───────────────────────┘ └───────────┬───────────┘
                                                            │
                                                            ▼
                                               ┌───────────────────────┐
                                               │   Matching Strategies │
                                               │                       │
                                               │   AliasMatch (1.0)    │
                                               │   FuzzyName (0.85-0.99)│
                                               │   EmailDomain (0.85-0.95)│
                                               │                       │
                                               │   kvault/matching/    │
                                               └───────────────────────┘

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
│       ├── index.db             ← SQLite FTS5 search index                   │
│       └── logs.db              ← Observability/audit log                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Descriptions

### Core Layer (`kvault/core/`)

| Component | File | Purpose |
|-----------|------|---------|
| **SimpleStorage** | `storage.py` | Filesystem CRUD for entities and summaries |
| **EntityIndex** | `index.py` | SQLite FTS5 full-text search with alias indexing |
| **EntityResearcher** | `research.py` | Multi-strategy entity matching and dedup detection |
| **ObservabilityLogger** | `observability.py` | Structured logging to `.kvault/logs.db` |
| **parse_frontmatter** | `frontmatter.py` | YAML frontmatter ↔ Markdown parsing |
| **build_frontmatter** | `frontmatter.py` | Markdown ↔ YAML frontmatter serialization |

### Matching Layer (`kvault/matching/`)

| Component | File | Score Range | Purpose |
|-----------|------|-------------|---------|
| **AliasMatchStrategy** | `alias.py` | 1.0 | Exact match against known aliases |
| **FuzzyNameMatchStrategy** | `fuzzy.py` | 0.85–0.99 | SequenceMatcher string similarity |
| **EmailDomainMatchStrategy** | `domain.py` | 0.85–0.95 | Shared corporate email domains |

### MCP Server (`kvault/mcp/`)

| Component | File | Purpose |
|-----------|------|---------|
| **server.py** | `server.py` | 20 MCP tool handlers, session management |
| **SessionState** | `state.py` | Per-session workflow state tracking |
| **validation.py** | `validation.py` | 10 pure validation functions for tool inputs |

### Orchestrator (`kvault/orchestrator/`)

| Component | File | Purpose |
|-----------|------|---------|
| **HeadlessOrchestrator** | `runner.py` | Spawns Claude subprocess for autonomous 6-step workflow |
| **OrchestratorConfig** | `context.py` | Configuration for orchestrator runs |
| **WorkflowStateMachine** | `state_machine.py` | State transitions for the 6-step workflow |
| **WorkflowEnforcer** | `enforcer.py` | Validates workflow compliance |

### CLI (`kvault/cli/`)

| Component | File | Purpose |
|-----------|------|---------|
| **main.py** | `main.py` | CLI entry point (`kvault` command) |
| **check.py** | `check.py` | `kvault check` integrity validation |

---

## Data Flow (MCP-based, primary path)

The agent drives the 6-step workflow through individual MCP tool calls:

```
Agent (Claude Code, Codex, etc.)
    │
    ├── 1. kvault_init(kg_root=".")
    │       → Loads hierarchy, root summary, entity count
    │
    ├── 2. kvault_research(name="...", email="...")
    │       → Runs all matching strategies against index
    │       → Returns matches with confidence scores
    │
    ├── 3. kvault_write_entity(path="...", meta={...}, content="...")
    │       → Creates/updates entity with YAML frontmatter
    │       → Validates required fields (source, aliases)
    │       → Sets created/updated timestamps
    │
    ├── 4. kvault_propagate_all(path="...")
    │       → Returns list of ancestor paths
    │       → Agent reads, updates, and writes each summary
    │
    ├── 5. kvault_write_journal(actions=[...], source="...")
    │       → Appends to journal/YYYY-MM/log.md
    │
    └── 6. kvault_rebuild_index()
            → Scans all entities, rebuilds FTS5 index
```

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

---

## Decision Log

### Why MCP Server over CLI Orchestrator?

**Decision**: MCP server is the primary interface; orchestrator is available for autonomous batch processing.

**Rationale**:
- Individual tool calls give the agent fine-grained control
- No subprocess timeout issues
- Structured JSON responses (no regex parsing)
- Works with any MCP-compatible tool, not just Claude

### Why SQLite for Index and Logs?

**Decision**: Use SQLite FTS5 for entity search and structured logging.

**Rationale**:
- Zero configuration
- FTS5 provides fast full-text search
- File-based = portable with the KB
- Easy to inspect (any SQLite browser)

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
├── conftest.py                  # Shared fixtures
├── fixtures/                    # Sample data
├── test_check.py                # kvault check CLI tests
├── test_e2e_cli.py              # End-to-end CLI tests
├── test_frontmatter.py          # YAML frontmatter parsing
├── test_index.py                # EntityIndex FTS5 tests
├── test_init.py                 # kvault init tests
├── test_matching.py             # Matching strategy tests
├── test_observability.py        # ObservabilityLogger tests
├── test_orchestrator.py         # Orchestrator workflow tests
├── test_research.py             # EntityResearcher tests
└── test_storage.py              # SimpleStorage tests
```

**185 tests, runs in <10s.**

```bash
pytest                                  # Run all
pytest --cov=kvault --cov-report=term   # With coverage
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1.0 | 2026-01-05 | Initial architecture |
| 0.2.0 | 2026-01-23 | MCP server, YAML frontmatter, 20 tools |
| 0.3.0 | 2026-02-06 | Multi-tool support (Codex, Cursor, VS Code), integrity hook |
