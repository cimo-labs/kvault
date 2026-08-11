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
8. Parent summaries should be comprehensive rollups of all descendants; `kvault check`
   emits warn-only `SUMMARY:` findings for weak rollups.
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

## Core APIs

```python
from kvault.core import operations as ops

# Stateless — all functions take kg_root: Path as first arg
ops.read_node(kg_root, path, parents="immediate")
ops.write_node(kg_root, path, content, meta=..., create=...)
ops.list_nodes(kg_root, path=".", recursive=False)
ops.search_nodes(kg_root, query, limit=10)
ops.prepare_summary_update(kg_root, path)
ops.write_parent_summary(kg_root, path, content, children_digest, meta=...)
ops.read_entity(kg_root, path)
ops.write_entity(kg_root, path, content, meta=..., create=..., reasoning=...)
ops.update_summaries(kg_root, updates)
ops.list_entities(kg_root, category=...)
ops.delete_entity(kg_root, path)
ops.move_entity(kg_root, source, target)
ops.get_ancestors(kg_root, path)
ops.write_journal(kg_root, actions, source)
ops.validate_kb(kg_root)
ops.get_kb_info(kg_root)
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
kvault search <query> [--json]
kvault read <path> [--parents none|immediate|all] [--json]
kvault write <path> [--create] [--reasoning TEXT] [--json] < content.md
kvault list [path] [--recursive] [--json]

# Compatibility operations
kvault delete <path> [--force] [--json]
kvault move <source> <target> [--json]
kvault read-summary <path> [--json]
kvault write-summary <path> [--json] < content.md
kvault update-summaries [--json] < updates.json
kvault ancestors <path> [--json]

# Journal
kvault journal --source TEXT [--date YYYY-MM-DD] [--json] < actions.json

# Status & validation
kvault status [--json]
kvault tree [--depth N]
kvault validate [--json]
kvault check [--kb-root PATH] [--json] [--no-summary-quality] [--summary-max-warnings N]

# Init & artifacts
kvault init <path> [--name NAME]
kvault artifact daily [--kb-root PATH] [--date YYYY-MM-DD] [--force] [--stdout] [--json]

# Ops log
kvault log tail [--limit N] [--session ID] [--kb-root PATH] [--json]
kvault log summary [--db PATH] [--session-id ID] [--kb-root PATH] [--json]

# Output tiers & strict mode (accepted on the group and after subcommands)
kvault [-q|--quiet] [--explain] [--trace] [--strict] <command> ...

# MCP compatibility
kvault-mcp --kb-root PATH
# Preferred MCP summary flow:
# kvault_prepare_summary_update -> kvault_write_parent_summary

# Version
kvault status --json
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
