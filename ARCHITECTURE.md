# kvault Architecture

Canonical architecture for the `knowledgevault` package.
Last updated: 2026-02-25

## Overview

`kvault` is a CLI-first knowledge base for AI agents. It stores entities in Markdown
with YAML frontmatter and exposes a stateless operations layer that both the CLI and
any future integrations use directly.

## Design Goals

1. CLI-first: agents call `kvault` commands via shell.
2. Deterministic, Git-friendly file storage.
3. Explicit workflow and validation boundaries.
4. Zero external service dependencies.
5. Portable across agent runtimes (Claude Code, Codex, Cursor, VS Code, Windsurf).

## System Layers

```
AI Tool Runtime
  -> shell exec
  -> kvault CLI (Click commands)
  -> kvault.core.operations (stateless business logic)
  -> core modules (storage/frontmatter/research/observability/artifacts)
  -> filesystem knowledge base
```

### CLI Layer (`kvault/cli/`)

Primary interface. All commands take `--kb-root` (auto-detected from cwd) and `--json`.

- `entity.py`: read, write, list, delete, move
- `summary.py`: read-summary, write-summary, update-summaries, ancestors
- `journal.py`: journal
- `validate.py`: validate
- `check.py`: check (propagation staleness)
- `main.py`: init, status, tree, artifact daily, log summary

### Operations Layer (`kvault/core/operations.py`)

Stateless functions — all take `kg_root: Path` as first arg. Shared by CLI and tests.

### Core Layer (`kvault/core/`)

- `storage.py`: filesystem CRUD + `scan_entities` / entity records.
- `frontmatter.py`: parse/build/merge YAML frontmatter.
- `validation.py`: path validation, error codes, input normalization.
- `research.py`: reusable entity matching and reconciliation suggestions.
- `observability.py`: structured logs to `.kvault/logs.db`.
- `daily_artifacts.py`: deterministic daily artifact generation.

## Storage Model

### Entity Format

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
New writes use frontmatter in `_summary.md`.

## Write Workflow

The canonical 2-call agent write flow:

1. Navigate via `kvault list`, `kvault read`, or agent file tools
2. `kvault write <path> --create --reasoning "..." --json` (returns ancestors)
3. `kvault update-summaries --json` (batch-update ancestor summaries from stdin)
4. Optional: `kvault journal` for additional logging (auto-journal happens on write with `--reasoning`)
5. Optional: `kvault validate` to check integrity

## Daily Artifact Flow

`kvault artifact daily` composes from:

- root summary
- people summary
- projects summary
- recent journal sections

Artifact output path: `.kvault/artifacts/daily/YYYY-MM-DD.md`

## Runtime Boundaries

What belongs in `kvault`:

- canonical data model and on-disk invariants
- CLI commands and operations layer
- reusable research/reconciliation logic
- validation and artifact generation

What belongs in host runtimes (e.g. Moss/OpenClaw):

- workspace-specific permissions/tool routing
- cron scheduling/inbox queue orchestration
- persona prompts and delivery policy

## Testing

Primary test suites live under `tests/` and cover:

- core modules (storage, frontmatter, research)
- operations layer (read, write, delete, move, validate, journal)
- CLI commands (CliRunner integration tests)
- end-to-end write/propagation workflows
- CLI checks and artifacts

Run:

```bash
pytest -q
```

## Version Notes

- 0.7.0: CLI-first. MCP server removed. Operations layer extracted to `core/operations.py`.
- 0.6.2: shared research primitives, architecture cleanup.
- 0.6.1: MCP path hardening, manifest/status reliability.
- 0.6.0: 2-call write workflow, batch summary updates, auto-journaling.
