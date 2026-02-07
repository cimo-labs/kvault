# kvault Package

Main Python package for the personal knowledge base framework.

## Module Structure

```
kvault/
├── __init__.py          # Package exports
├── cli/                 # Command-line interface
├── core/                # Storage, frontmatter, observability
├── mcp/                 # MCP server (15 tools for Claude Code, Codex, etc.)
├── orchestrator/        # Headless workflow runner (5-step pipeline)
└── templates/           # Default templates for new KBs
```

## Package Exports

```python
from kvault import (
    # Core
    SimpleStorage,
    normalize_entity_id,
    scan_entities,
    count_entities,
    list_entity_records,
    EntityRecord,
    # Frontmatter
    parse_frontmatter,
    build_frontmatter,
    merge_frontmatter,
)
```

## Layer Architecture

```
┌─────────────────────────────────────────────┐
│              MCP Server (Preferred)          │
│  15 tools: kvault_init, kvault_read_entity..│
│  kvault-mcp entry point                     │
└─────────────────────────────────────────────┘
                      │
┌─────────────────────────────────────────────┐
│          Orchestrator Layer                  │
│  HeadlessOrchestrator → 5-step workflow     │
└─────────────────────────────────────────────┘
                      │
┌─────────────────────────────────────────────┐
│              Core Layer                      │
│  SimpleStorage │ Search      │ Frontmatter  │
│  Observability │ (filesystem)│              │
└─────────────────────────────────────────────┘
```

## Key Dependencies

- **pydantic**: Configuration validation
- **pyyaml**: YAML frontmatter parsing
- **click**: CLI framework
- **mcp**: MCP server protocol (optional, Python 3.10+)

## CLI Quick Start

```bash
# Create a new knowledge base
kvault init my_kb --name "Your Name"

# Validate integrity
kvault check --kb-root my_kb

# Observability
kvault log summary --db my_kb/.kvault/logs.db
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/
mypy kvault/
```
