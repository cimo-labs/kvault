# kvault Architecture

Canonical architecture for the `knowledgevault` package.
Last updated: 2026-02-17

## Overview

`kvault` is a filesystem-native knowledge base runtime for MCP-compatible agents.
It stores entities in Markdown with YAML frontmatter, exposes a canonical MCP tool
manifest, and keeps write workflows auditable via `.kvault/logs.db`.

## Design Goals

1. Agent-native interface through MCP.
2. Deterministic, Git-friendly file storage.
3. Explicit workflow and validation boundaries.
4. Zero external service dependencies.
5. Portable across agent runtimes (Claude Code, Codex, Cursor, VS Code, Windsurf).

## System Layers

```
AI Tool Runtime
  -> MCP (stdio)
  -> kvault.mcp.server (manifest-driven)
  -> core modules (storage/frontmatter/research/observability/artifacts)
  -> filesystem knowledge base
```

### MCP Layer (`kvault/mcp/`)

- `manifest.py` is the canonical source of tool names/schemas.
- `server.py` wires manifest entries to handlers and session state.
- `state.py` tracks workflow sessions.
- `validation.py` centralizes input/path/frontmatter validation.

Current canonical tool count: **16**
(`kvault_init`, `kvault_status`, `kvault_log_phase`, entity CRUD, summary tools,
`kvault_write_journal`, `kvault_generate_daily_artifact`, `kvault_validate_kb`).

### Core Layer (`kvault/core/`)

- `storage.py`: filesystem CRUD + `scan_entities` / entity records.
- `frontmatter.py`: parse/build/merge YAML frontmatter.
- `research.py`: reusable entity matching and reconciliation suggestions.
- `observability.py`: structured logs to `.kvault/logs.db`.
- `daily_artifacts.py`: deterministic daily artifact generation.

### CLI Layer (`kvault/cli/`)

- `kvault init`
- `kvault check`
- `kvault artifact daily`
- `kvault log summary`

CLI commands are thin wrappers around core modules.

## Storage Model

### Preferred Entity Format

Each entity is a directory containing a `_summary.md` with YAML frontmatter:

```markdown
---
created: 2026-02-17
updated: 2026-02-17
source: manual
aliases: [Alice Smith, alice@example.com]
---
# Alice Smith

Entity body...
```

### Legacy Compatibility

`scan_entities` supports legacy `_meta.json` fallback for read compatibility.
New writes should use frontmatter in `_summary.md`.

## Write Workflow

The canonical agent write flow:

1. `kvault_init(kg_root=...)`
2. research/navigation via agent file tools or `kvault_list_entities` / read tools
3. `kvault_write_entity(..., reasoning=...)`
4. `kvault_update_summaries(updates=[...])`
5. optional explicit `kvault_write_journal(...)` (auto-journal can happen on write)
6. optional `kvault_validate_kb`

`kvault_write_entity` returns ancestor context for propagation.

## Daily Artifact Flow

`kvault_generate_daily_artifact` (or `kvault artifact daily`) composes from:

- root summary
- people summary
- projects summary
- recent journal sections

Artifact output path:
`.kvault/artifacts/daily/YYYY-MM-DD.md`

## Runtime Boundaries

What belongs in `kvault`:

- canonical data model and on-disk invariants
- tool manifest + MCP behavior
- reusable research/reconciliation logic
- validation and artifact generation

What belongs in host runtimes (e.g. moss/OpenClaw):

- workspace-specific permissions/tool routing
- cron scheduling/inbox queue orchestration
- persona prompts and delivery policy

## Testing

Primary test suites live under `tests/` and cover:

- core modules
- MCP handlers and regressions
- end-to-end write/propagation paths
- CLI checks and artifacts

Run:

```bash
pytest -q
```

## Version Notes

- 0.6.1 introduced MCP path hardening and manifest/status reliability.
- 0.6.2 (current workspace changes) adds shared research primitives and removes
  stale architecture/docs references to non-existent orchestrator components.
