# kvault Package

Main Python package for `knowledgevault`.

## Module Structure

```
kvault/
├── __init__.py
├── cli/                 # kvault init/check/artifact/log commands
├── core/                # storage, frontmatter, research, observability, artifacts
├── mcp/                 # MCP server + canonical manifest (16 tools)
└── templates/           # default KB templates
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
    generate_daily_artifact,
    DailyArtifactResult,
    parse_iso_date,
)
```

## Interface Layers

- MCP server (`kvault-mcp`) is the preferred runtime interface.
- Core modules provide reusable library behavior.
- CLI commands are local operational wrappers.

## CLI Quick Start

```bash
kvault init my_kb --name "Your Name"
kvault check --kb-root my_kb
kvault artifact daily --kb-root my_kb --date 2026-02-17
kvault log summary --db my_kb/.kvault/logs.db
```

## Development

```bash
pip install -e ".[dev]"
pytest -q
```
