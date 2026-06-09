# kvault Architecture

Canonical architecture for the `knowledgevault` package.
Last updated: 2026-06-09

## Overview

`kvault` is a CLI-first knowledge base for AI agents. It stores nodes in Markdown
with YAML frontmatter and exposes a stateless operations layer used by the CLI, the
thin MCP compatibility server, and tests.

## Design Goals

1. CLI-first: agents call `kvault` commands via shell.
2. Deterministic, Git-friendly file storage.
3. Explicit workflow and validation boundaries.
4. Zero external service dependencies.
5. Portable across agent runtimes that can run shell commands or MCP tools.

## System Layers

```
AI Tool Runtime
  -> shell exec
  -> kvault CLI (Click commands) or kvault-mcp tools
  -> kvault.core.operations (stateless business logic)
  -> core modules (search/storage/frontmatter/research/observability/artifacts)
  -> filesystem knowledge base
```

### CLI Layer (`kvault/cli/`)

Primary interface. Agent-facing KB commands support `--kb-root` (auto-detected from cwd)
and `--json`; command groups and server-launching commands may have command-specific flags.

- `entity.py`: node-first read, write, list; compatibility delete/move
- `search.py`: structured lexical node search
- `summary.py`: read-summary, write-summary, update-summaries, ancestors
- `journal.py`: journal
- `validate.py`: validate
- `check.py`: check (propagation staleness and summary-quality warnings)
- `main.py`: init, status, tree, artifact daily, log summary

### MCP Layer (`kvault/mcp/`)

Thin compatibility server exposed through `kvault-mcp`. Each process is bound to one KB
root from `--kb-root` or `KVAULT_KB_ROOT`, enforces `KVAULT_ALLOWED_ROOTS`, and delegates
tool behavior to `kvault.core.operations`. MCP clients should prefer
`kvault_prepare_summary_update` and `kvault_write_parent_summary` for parent rollups so direct
children are read before a parent summary is rewritten.

### Operations Layer (`kvault/core/operations.py`)

Stateless functions — all take `kg_root: Path` as first arg. Shared by CLI, MCP, and tests.
Includes node read/write/list/search, compatibility entity/summary operations, and strict
parent-summary helpers backed by direct-child digests.

### Core Layer (`kvault/core/`)

- `storage.py`: filesystem CRUD + `scan_entities` / entity records.
- `search.py`: stateless structured lexical search over visible `_summary.md` nodes.
- `frontmatter.py`: parse/build/merge YAML frontmatter.
- `validation.py`: path validation, error codes, input normalization.
- `research.py`: reusable entity matching and reconciliation suggestions.
- `observability.py`: structured logs to `.kvault/logs.db`.
- `daily_artifacts.py`: deterministic daily artifact generation.
- `summary_quality.py`: warn-only parent-summary quality audit used by `kvault check`.

## Storage Model

### Node Format

Each node is a directory containing a `_summary.md` with YAML frontmatter:

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

The canonical CLI write flow:

1. Navigate via `kvault list`, `kvault read`, or agent file tools
2. `kvault write <path> --create --reasoning "..." --json` (returns ancestors)
3. `kvault update-summaries --json` (batch-update ancestor summaries from stdin)
4. Optional: `kvault journal` for additional logging (auto-journal happens on write with `--reasoning`)
5. Optional: `kvault validate` to check integrity

The canonical MCP parent-summary flow is stricter:

1. `kvault_write_node(...)`
2. For each returned ancestor, closest-first:
   - `kvault_prepare_summary_update(path)`
   - compose the parent from the returned parent and immediate child summaries
   - `kvault_write_parent_summary(path, content, children_digest)`
3. `kvault_validate_kb(...)` after larger edits

Strict parent writes use a stateless digest over direct child summaries. The digest excludes mtime
and changes when a direct child summary body, frontmatter, path, or existence changes.

Parent summaries are expected to be comprehensive rollups of descendants. `kvault check`
emits warn-only `SUMMARY:` findings when a parent omits immediate child coverage, is too
short for its subtree, or contains placeholder/redirect language.

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

What belongs in host runtimes:

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
- MCP compatibility tools

Run:

```bash
pytest -q
```

## Version Notes

- 0.11.0: annotated tree outline (`build_outline`/`render_outline_text`) with counts, recency,
  and explicit truncation markers; MCP `kvault_tree`; no-op writes preserve `updated`.
- 0.10.0: strict MCP parent-summary updates with stateless child digests and hierarchy hints.
- 0.9.0: node-first interface, structured lexical search, optional UI removed.
- 0.8.0: UI, summary-quality audit, MCP compatibility restored, arbitrary-depth entity paths.
- 0.7.0: CLI-first. MCP server removed. Operations layer extracted to `core/operations.py`.
- 0.6.2: shared research primitives, architecture cleanup.
- 0.6.1: MCP path hardening, manifest/status reliability.
- 0.6.0: 2-call write workflow, batch summary updates, auto-journaling.
