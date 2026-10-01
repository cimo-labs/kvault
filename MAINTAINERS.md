# kvault - Maintainer Notes

## Overview

`kvault` is a CLI-first knowledge base library. The canonical runtime interface is:

- CLI commands via `kvault` entry point (`kvault/cli/`)
- stateless operations layer (`kvault/core/operations.py`)
- structured lexical search (`kvault/core/search.py`)
- filesystem storage with frontmatter summaries (`kvault/core/`)
- thin root-bound MCP compatibility server (`kvault/mcp/server.py`)

## Repository Layout

```
kvault/
├── kvault/
│   ├── cli/         # CLI commands (primary interface)
│   ├── core/        # Operations, search, storage, validation, frontmatter
│   ├── mcp/         # MCP compatibility server
│   ├── templates/   # KB init templates (AGENTS.md)
│   └── py.typed     # PEP 561 marker
└── tests/
```

## Critical Invariants

1. `operations.py` is the shared business logic layer — CLI and MCP use it.
2. A node is any non-hidden directory with `_summary.md`, including root, branches, and leaves.
3. Nodes are written as `_summary.md` with frontmatter (legacy `_meta.json` read fallback only).
4. Path handling must never allow escape outside configured KB root.
5. If `KVAULT_ALLOWED_ROOTS` is configured, CLI and MCP boundaries must reject non-allowed roots.
6. CLI uses `default_source="auto:cli"`.
7. MCP uses `default_source="auto:mcp"` and is bound to one KB root per process.
8. Parent summaries should be comprehensive rollups of all descendants that stay the size of
   an index page; `kvault check` emits warn-only `SUMMARY:` findings for rollups that are too
   short, too long, missing child coverage, placeholder-shaped, or changelog-shaped
   (`stale_history`). A parent whose only children are background dirs (`deep_context/`,
   see `core/conventions.py`) is budgeted as a leaf.
9. MCP parent-summary writes should use strict prepare/write tools so direct child summaries are
   read before a parent rollup is rewritten.
10. **J1**: in `--json` mode kvault emits exactly one JSON document and writes nothing to
    stderr, at every verbosity tier. Pinned by `tests/test_output_channels.py` (a matrix
    over every JSON-capable command × every tier).
11. **M1**: no stream writers under `kvault/core/` or `kvault/mcp/` — over MCP, stdout is
    the JSON-RPC transport and one stray print corrupts the framing. Rendering happens
    only in `kvault/cli/render.py`. Pinned by `tests/test_output_channels.py`.
12. **No WAL on `.kvault/logs.db`**, ever. WAL creates `-wal`/`-shm` sidecar files next to
    the DB, and unexpected untracked sidecars break KB git-sync automation. Logging sits
    off the write path, so the contention WAL solves does not exist here. Pinned by
    `tests/test_oplog.py` (`test_no_wal_anywhere`).
13. **MCP tools refuse unknown arguments.** FastMCP drops unknown keys by default, so a
    misspelled budget or path argument silently ran with defaults;
    `_forbid_unknown_arguments` in `kvault/mcp/server.py` rebuilds every tool's argument
    model with `extra="forbid"`. Accept a common alternative name as an explicit alias
    parameter rather than loosening this. Pinned by `tests/test_mcp_surface.py`.
14. **MCP results fit clients that inline about 4 KB** by default (tree outline, status
    hierarchy, search, read_nodes, write/move/delete ancestors, daily artifact, check without
    its legacy lists, validate with one message per issue type); anything larger is opt-in.
    A result cut to fit carries a `truncated` note; documents left out by default
    (ancestor documents, the artifact's markdown) are named in the tool's docstring. Register tools with `_tool(...)`
    (never `server.tool` directly): it sends the result as compact JSON text, and the
    closure keeps the dict-returning function for tools that call each other.

## Core APIs

```python
from kvault.core import operations as ops

# Stateless — all functions take kg_root: Path as first arg
ops.read_node(kg_root, path, parents="immediate")   # none | gist | immediate | all
ops.read_nodes(kg_root, paths, parents="none", total_max_chars=20000)   # budget counts whole nodes
ops.write_node(kg_root, path, content, meta=..., create=..., event_ids=...)
ops.write_node(kg_root, path, patches=[{"old_str": ..., "new_str": ...}])   # edit an existing body
ops.list_nodes(kg_root, path=".", recursive=False)
ops.search_nodes(kg_root, query, limit=10, compact=False, parents="none", include_background=False)
ops.prepare_summary_update(kg_root, path)
ops.write_parent_summary(kg_root, path, content, children_digest, meta=...)
ops.update_summaries(kg_root, updates)   # [{path, content | patches, meta}]; existing summaries only
ops.delete_entity(kg_root, path)
ops.move_entity(kg_root, source, target)
ops.move_entities(kg_root, moves, new_root=False, dry_run=False)   # [{from, to}] under one lock
ops.mark_node(kg_root, path, distinct_from=..., max_children=..., series_ok=..., verify_by=...)
ops.get_ancestors(kg_root, path)
ops.write_journal(kg_root, actions, source)
ops.validate_kb(kg_root)
ops.get_kb_info(kg_root)
# Entity-era names kept for compatibility: read_entity, write_entity, list_entities
```

```python
from kvault.core import (
    parse_frontmatter,
    build_frontmatter,
    merge_frontmatter,
    NOTE_CODES,
    note,
    collapse,
    EntityResearcher,
    ResearchCandidate,
    ObservabilityLogger,
    OpLog,
    SearchDocument,
    SearchResult,
    scan_search_documents,
    search_nodes,
    SummaryQualityIssue,
    audit_summary_quality,
    format_summary_quality_warnings,
    DailyArtifactResult,
    generate_daily_artifact,
)

from kvault.core.storage import (
    SimpleStorage,
    EntityRecord,
    normalize_entity_id,
    scan_entities,
    count_entities,
    list_entity_records,
)
```

## CLI Commands

```bash
# Node operations
kvault search <query> [--limit N] [--kind root|category|entity]... [--path PREFIX] [--no-collapse] \
              [--compact] [--snippet-chars N] [--parents none|gist|immediate|all] \
              [--include-background] [--json]
kvault read <path>... [--parents none|gist|immediate|all] [--max-total-chars N] [--json]   # several paths: one call
kvault write <path> [--create] [--reasoning TEXT] [--event ID]... [--new-root] [--allow-similar] \
             [--json] < content.md
kvault write <path> --patches [--json] < patches.json   # [{"old_str", "new_str"}], each matching once
kvault list [path] [--recursive] [--json]

# Structure
kvault delete <path> --confirm [--json]
kvault move <source> <target> --confirm [--new-root] [--json]
kvault move --batch --confirm [--dry-run] [--json] < moves.json   # [{"from", "to"}]
kvault plan [PATH] [--limit N|0] [--max-children N] [--json]       # never applies anything
kvault mark <path> [--distinct-from X]... [--max-children N] [--series-ok] [--verify-by DATE|+Nd|none] [--clear]

# Compatibility operations
kvault read-summary <path> [--json]
kvault write-summary <path> [--json] < content.md
kvault update-summaries [--json] < updates.json
kvault ancestors <path> [--paths-only] [--json]

# Journal
kvault journal --source TEXT [--date YYYY-MM-DD] [--json] < actions.json

# Capture journal
kvault capture --source S [--source-ref R] [--tag T] [--allow-suspicious] [--json] < text
kvault events list [--status pending|resolved|retracted] [--limit N|0] [--since YYYY-MM-DD] [--json]
kvault events show <id> [--json]
kvault events resolve <id> --outcome journal_only|duplicate|no_op|rejected [--note TEXT] [--json]
kvault events retract <id> --reason TEXT [--superseded-by ID] [--json]

# Status & validation
kvault status [--root-summary] [--json]
kvault tree [path] [--depth N] [--max-children N] [--gist] [--json]
kvault validate [--json]
kvault check [--kb-root PATH] [--json] [--code CODE]... [--max-findings N|0] [--max-lines N|0] \
             [--no-summary-quality] [--summary-max-words N|0] [--summary-max-dated-sections N|0] \
             [--pending-max-age D] [--max-children N]
kvault doctor [--kb-root PATH] [--json]   # runtime/env/KB-binding report; exit 0 always

# Init & artifacts
kvault init <path> [--name NAME]
kvault artifact daily [--kb-root PATH] [--date YYYY-MM-DD] [--force] [--stdout] [--json]   # --json carries content only with --stdout

# Ops log
kvault log tail [--limit N] [--session ID] [--kb-root PATH] [--json]
kvault log summary [--db PATH] [--session-id ID] [--kb-root PATH] [--json]

# Output tiers & strict mode (on the group, or after: write, write-summary, update-summaries, delete, move, mark, journal, search)
kvault [-q|--quiet] [--explain] [--trace] [--strict] <command> ...

# MCP server
kvault-mcp --kb-root PATH [--legacy-tools]   # or KVAULT_MCP_LEGACY_TOOLS=1 for the 8 superseded tools
# Preferred MCP summary flow:
# kvault_prepare_summary_update -> kvault_write_parent_summary

# Version (also reported as `version` in `kvault status --json` and `kvault doctor`)
kvault --version
```

## Testing

```bash
pytest -q
```

**No test may use `result.stderr`.** On click 8.1.x (the Python 3.9 CI leg),
`CliRunner`'s `Result.output` and `Result.stdout` are the merged stdout+stderr stream and
`Result.stderr` raises ValueError. `json.loads(result.output)` is the portable guard: the
merged stream catches pollution on both channels across the whole matrix.

Prefer adding tests in `tests/` whenever changing:

- operations layer behavior
- validation/path logic
- CLI commands/output
- MCP compatibility tools
- research/matching heuristics
- structured search ranking/output

## Release Hygiene

Before publishing:

1. Update docs for any API/CLI changes.
2. Run full tests.
3. Ensure `CHANGELOG.md` and package version stay in sync.
4. Update templates/AGENTS.md for agent-facing changes.
