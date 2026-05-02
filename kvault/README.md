# kvault Package

Main Python package for `knowledgevault`.

## Module Structure

```
kvault/
├── __init__.py
├── cli/                 # CLI commands (primary interface)
├── core/                # operations, storage, frontmatter, research, observability, artifacts
├── mcp/                 # thin MCP compatibility server
├── templates/           # default KB templates
└── ui/                  # optional read-only web UI
```

## Primary Exports

```python
from kvault import (
    SimpleStorage,
    normalize_entity_id,
    scan_entities,
    count_entities,
    list_entity_records,
    EntityRecord,
    parse_frontmatter,
    build_frontmatter,
    merge_frontmatter,
    EntityResearcher,
    ResearchCandidate,
    ObservabilityLogger,
    SummaryQualityIssue,
    audit_summary_quality,
    format_summary_quality_warnings,
    generate_daily_artifact,
    DailyArtifactResult,
    parse_iso_date,
)
```

## Interface Layers

- CLI commands (`kvault`) are the primary runtime interface.
- Core operations layer (`kvault/core/operations.py`) provides shared business logic.
- MCP compatibility server (`kvault/mcp/server.py`) exposes root-bound tools backed by operations.
- Optional web UI (`kvault/ui/`) provides read-only browsing and search.
- Core modules provide reusable library behavior.

## CLI Quick Start

```bash
kvault init my_kb --name "Your Name"
kvault check --kb-root my_kb
kvault artifact daily --kb-root my_kb --date 2026-02-17
kvault log summary --db my_kb/.kvault/logs.db
kvault-mcp --kb-root my_kb
```

## Development

```bash
pip install -e ".[dev,ui,mcp]"
pytest -q
```
