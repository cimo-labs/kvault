# kvault - Maintainer Notes

## Overview

`kvault` is an MCP-first knowledge base library. The canonical runtime contract is:

- manifest-driven MCP server (`kvault/mcp/manifest.py`)
- filesystem storage with frontmatter summaries
- reusable core modules in `kvault/core`
- thin CLI wrappers in `kvault/cli`

Current MCP tool count: **16**.

## Repository Layout

```
kvault/
├── kvault/
│   ├── cli/
│   ├── core/
│   ├── mcp/
│   └── templates/
└── tests/
```

## Critical Invariants

1. `manifest.py` is the single source of truth for tool schemas/names.
2. `kvault_status` must report manifest version/count consistently.
3. Entities are written as `_summary.md` with frontmatter (legacy `_meta.json` read fallback only).
4. Path handling must never allow escape outside configured KB root.
5. If `KVAULT_ALLOWED_ROOTS` is configured, `kvault_init` must reject non-allowed roots.

## Core APIs

```python
from kvault.core import (
    parse_frontmatter,
    build_frontmatter,
    merge_frontmatter,
    EntityResearcher,
    ResearchCandidate,
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
kvault init <path>
kvault check --kb-root <path>
kvault artifact daily --kb-root <path> [--date YYYY-MM-DD] [--force]
kvault log summary --db <path/to/.kvault/logs.db> [--session-id <id>] [--json]
kvault-mcp
```

## Testing

```bash
pytest -q
```

Prefer adding tests in `tests/` whenever changing:

- MCP handler behavior
- validation/path logic
- research/matching heuristics
- CLI output/flags

## Release Hygiene

Before publishing:

1. Update docs for any API/manifest changes.
2. Run full tests.
3. Ensure `CHANGELOG.md` and package version stay in sync.
4. Verify MCP manifest count + docs references are consistent.
