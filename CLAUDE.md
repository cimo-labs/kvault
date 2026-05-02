# kvault - Maintainer Notes

## Overview

`kvault` is a CLI-first knowledge base library. The canonical runtime interface is:

- CLI commands via `kvault` entry point (`kvault/cli/`)
- stateless operations layer (`kvault/core/operations.py`)
- filesystem storage with frontmatter summaries (`kvault/core/`)
- thin root-bound MCP compatibility server (`kvault/mcp/server.py`)
- optional read-only web UI (`kvault/ui/`)

## Repository Layout

```
kvault/
├── kvault/
│   ├── cli/         # CLI commands (primary interface)
│   ├── core/        # Operations, storage, validation, frontmatter
│   ├── mcp/         # MCP compatibility server
│   ├── templates/   # KB init templates (AGENTS.md)
│   ├── ui/          # Read-only Starlette UI
│   └── py.typed     # PEP 561 marker
└── tests/
```

## Critical Invariants

1. `operations.py` is the shared business logic layer — both CLI and MCP use it.
2. Entities are written as `_summary.md` with frontmatter (legacy `_meta.json` read fallback only).
3. Path handling must never allow escape outside configured KB root.
4. If `KVAULT_ALLOWED_ROOTS` is configured, operations must reject non-allowed roots.
5. CLI uses `default_source="auto:cli"`.
6. MCP uses `default_source="auto:mcp"` and is bound to one KB root per process.
7. Parent summaries should be comprehensive rollups of all descendants; `kvault check`
   emits warn-only `SUMMARY:` findings for weak rollups.

## Core APIs

```python
from kvault.core import operations as ops

# Stateless — all functions take kg_root: Path as first arg
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
    EntityResearcher,
    ResearchCandidate,
    ObservabilityLogger,
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
# Entity operations
kvault read <path> [--json]
kvault write <path> [--create] [--reasoning TEXT] [--json] < content.md
kvault list [category] [--json]
kvault delete <path> [--force] [--json]
kvault move <source> <target> [--json]

# Summary operations
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
kvault check [--kb-root PATH] [--json] [--no-summary-quality]

# Init & artifacts
kvault init <path> [--name NAME]
kvault artifact daily [--kb-root PATH] [--date YYYY-MM-DD] [--force] [--json]
kvault log summary [--db PATH] [--session-id ID] [--json]
kvault ui [--kb-root PATH] [--port PORT] [--host HOST]

# MCP compatibility
kvault-mcp --kb-root PATH

# Version
kvault status --json
```

## Testing

```bash
pytest -q
```

Prefer adding tests in `tests/` whenever changing:

- operations layer behavior
- validation/path logic
- CLI commands/output
- research/matching heuristics

## Release Hygiene

Before publishing:

1. Update docs for any API/CLI changes.
2. Run full tests.
3. Ensure `CHANGELOG.md` and package version stay in sync.
4. Update templates/AGENTS.md for agent-facing changes.
