# kvault Architecture

Canonical architecture for the `knowledgevault` package.
Last updated: 2026-08-10

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
  -> core modules (search/storage/frontmatter/research/notes/oplog/observability/artifacts)
  -> filesystem knowledge base
```

### CLI Layer (`kvault/cli/`)

Primary interface. Agent-facing KB commands support `--kb-root` (auto-detected from cwd)
and `--json`; command groups and server-launching commands may have command-specific flags.

- `entity.py`: node-first read, write (`--new-root`, `--allow-similar`), list; compatibility delete/move (`--batch`)
- `search.py`: structured lexical node search
- `summary.py`: read-summary, write-summary, update-summaries, ancestors
- `journal.py`: journal
- `events.py`: capture, events list/show/resolve/retract/import (capture journal)
- `validate.py`: validate
- `check.py`: check — thin renderer over `core/check.py` (hard `[KB]` line + bounded warn groups)
- `plan.py`: plan (ordered maintenance worklist from `core/plan.py`)
- `doctor.py`: doctor (version, python, install location, KB binding, env — never fails)
- `main.py`: init, status, tree, artifact daily, log tail / log summary
- `render.py`: human rendering of result notes; verbosity-tier resolution
- `_helpers.py`: shared option decorators, KB-root resolution, ops-log append

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
- `notes.py`: work-reporting vocabulary — the closed set of 11 note codes (`structure` since 0.15), verbosity
  tiers, and the `note`/`visible`/`collapse` helpers. Builds data, never prints.
- `oplog.py`: durable per-operation log — the `ops` table in `.kvault/logs.db`
  behind `kvault log tail`. Failure-proof appends; no WAL, ever.
- `observability.py`: legacy phase logs (`logs` table) in `.kvault/logs.db`.
- `daily_artifacts.py`: deterministic daily artifact generation.
- `summary_quality.py`: warn-only parent-summary quality audit used by `kvault check`.
- `structure.py`: lexical tree-shape rules shared by the write guards, `check`, and `plan` —
  name collisions (`same_words` / `prefix` / `overlap`), leading-token clustering, ghost
  directories, loose files, journal layout, and `.kvaultignore`. Names only, never bodies,
  so the guard and the audit cannot disagree.
- `check.py`: every `kvault check` finding as one stateless document (`run_checks`): hard
  codes (`PROPAGATE`, `LOG`, `WRITE`, `BRANCH`) and bounded warn codes (`SUMMARY`,
  `PENDING`, `RETRACTED`, `GHOST`, `SIBLINGS`, `LOOSE`, `JOURNAL`). Shared by the CLI
  and MCP `kvault_check`.
- `plan.py`: `build_plan` — findings → ordered worklist with exact commands and `moves`
  payloads; never applies anything.
- `conventions.py`: layout conventions shared by more than one rule (`BACKGROUND_CHILD_DIRS`,
  the `deep_context/` supporting-material child: budgeted as a leaf by the summary audit,
  never a reason for search to collapse its parent).

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

Since 0.13.0 a write result also reports what kvault decided: a one-line `did`
receipt, a `changed` flag, a `notes` array (see Work Reporting below), and
`ancestor_paths` alongside the full `ancestors` payload.

A write whose body and metadata match the node on disk takes a **no-op fast
path**: the file is not rewritten, mtime and the `updated` frontmatter date are
untouched, human output says `Unchanged:` (with the ancestors line suppressed),
and the result carries `changed: false` plus an `unchanged` note. The
byte-identical rewrite used to manufacture spurious `PROPAGATE` warnings in
`kvault check` and fake recency in search. Two deliberate exceptions: legacy
`_meta.json` cleanup still runs on the no-op path, and a legacy node without
frontmatter never takes it (the migrating write must run). `ancestors` /
`propagation_required` keep their meaning — "do ancestor summaries exist to
roll up" — so a stale chain from an earlier write stays visible on retry.

The canonical MCP parent-summary flow is stricter:

1. `kvault_write_node(...)`
2. For each returned ancestor, closest-first:
   - `kvault_prepare_summary_update(path)`
   - compose the parent from the returned parent and immediate child summaries
   - `kvault_write_parent_summary(path, content, children_digest)`
3. `kvault_validate_kb(...)` after larger edits

Strict parent writes use a stateless digest over direct child summaries. The digest excludes mtime
and changes when a direct child summary body, frontmatter, path, or existence changes.

Parent summaries are expected to be comprehensive rollups of descendants that stay the size of
an index page. `kvault check` emits warn-only `SUMMARY:` findings when a parent omits immediate
child coverage, is too short for its subtree, contains placeholder/redirect language, exceeds
the word ceiling (`too_long`), or accretes dated/delta sections (`stale_history`); and warn-only
`RETRACTED:` findings when a node's `source_refs` cite an event retracted with `kvault events
retract`.

## Work Reporting

kvault reports what it *decided* — invented values, deliberate no-ops,
half-failures, hidden results, fallbacks — not what it did step by step. The
pipeline has three stages with a hard boundary between them:

1. **Vocabulary** (`kvault/core/notes.py`): a closed set of 11 note codes
   (`autofilled`, `unchanged`, `partial`, `created`, `removed`, `truncated`,
   `skipped`, `waited`, `guessed`, `propagate`), each with a one-sentence
   contract. An unknown code raises at build time.
2. **Result dicts** (`kvault/core/operations.py` and friends): notes ride
   inside the result as `{code, text, level, detail?, why?, next?}`. Batch
   commands collapse per-item notes by code into `{code, count, examples,
   level}` via `notes.collapse`.
3. **Rendering** (`kvault/cli/render.py`): the ONLY place notes become printed
   text. Verbosity tiers (`-q`/normal/`--explain`/`--trace`, or
   `KVAULT_VERBOSITY`) filter by note level; `why`/`next` print at `--explain`.
   `partial` survives even `--quiet`. `--strict` exits 3 on any warning-class
   note. In `--json` mode notes ride in the document itself, `why`/`next`
   always included, and consumers filter by `level` themselves.

MCP never renders: results are returned as data, stdout is the JSON-RPC
transport, and nothing under `kvault/core/` or `kvault/mcp/` may write to a
stream (pinned by `tests/test_output_channels.py`).

Completed mutating operations are also appended to the durable `ops` table in
`.kvault/logs.db` (`kvault/core/oplog.py`; surfaces `cli` and `mcp`), read via
`kvault log tail` / `kvault_log_tail`. Appends run after the mutation commits
and can never fail it — a failed append surfaces as a `skipped` note on both
surfaces.
`KVAULT_SESSION` correlates commands into one session; `KVAULT_OPS_LOG=0`
disables the log.

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

- 0.14.0: bounded outputs — `--version`/`doctor` handshake, `check` ceilings (`too_long`,
  `stale_history`), capture tripwire for shell-mangled text, `events retract` + `RETRACTED:`,
  search ancestor collapse + `--kind`/`--path`, defaults flipped (`read --parents none`,
  `status` without `root_summary`, `events list --limit 50`, MCP `ancestors="paths"`).
- 0.13.0: work reporting — note vocabulary + verbosity tiers, `did`/`changed`/`notes`
  in results, durable ops log (`kvault log tail`), no-op writes skip the file rewrite;
  `check` human output/exit codes frozen.
- 0.12.1: child-digest fix (unreadable children could be silently erased from parent
  rollups), widened node-name pattern, `check` hard-errors on a bad `--kb-root`.
- 0.12.0: capture journal (`capture`/`events`, promotion via `write --event`), per-KB
  write lock with atomic writes, shared path-safety layer, delete/move confirmation.
- 0.11.0: annotated tree outline (`build_outline`/`render_outline_text`) with counts, recency,
  and explicit truncation markers; MCP `kvault_tree`; no-op writes preserve `updated`.
- 0.10.0: strict MCP parent-summary updates with stateless child digests and hierarchy hints.
- 0.9.0: node-first interface, structured lexical search, optional UI removed.
- 0.8.0: UI, summary-quality audit, MCP compatibility restored, arbitrary-depth entity paths.
- 0.7.0: CLI-first. MCP server removed. Operations layer extracted to `core/operations.py`.
- 0.6.2: shared research primitives, architecture cleanup.
- 0.6.1: MCP path hardening, manifest/status reliability.
- 0.6.0: 2-call write workflow, batch summary updates, auto-journaling.
