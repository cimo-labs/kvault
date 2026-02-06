# kvault Package

Main Python package for the personal knowledge base framework.

## Module Structure

```
kvault/
├── __init__.py          # Package exports
├── cli/                 # Command-line interface
├── core/                # Storage, indexing, frontmatter, observability, research
├── matching/            # Entity matching strategies (alias, fuzzy, email domain)
├── mcp/                 # MCP server (20 tools for Claude Code, Codex, etc.)
├── orchestrator/        # Headless workflow runner (6-step pipeline)
└── templates/           # Default templates for new KBs
```

## Package Exports

```python
# Core
from kvault import (
    EntityIndex,
    IndexEntry,
    SimpleStorage,
    normalize_entity_id,
    ObservabilityLogger,
    LogEntry,
    EntityResearcher,
)

# Matching strategies
from kvault import (
    MatchStrategy,
    MatchCandidate,
    EntityIndexEntry,
    AliasMatchStrategy,
    FuzzyNameMatchStrategy,
    EmailDomainMatchStrategy,
    register_strategy,
    get_strategy,
    list_strategies,
    load_strategies,
)
```

## Layer Architecture

```
┌─────────────────────────────────────────────┐
│              MCP Server (Preferred)          │
│  20 tools: kvault_init, kvault_search, ...  │
│  kvault-mcp entry point                     │
└─────────────────────────────────────────────┘
                      │
┌─────────────────────────────────────────────┐
│          Orchestrator Layer                  │
│  HeadlessOrchestrator → 6-step workflow     │
└─────────────────────────────────────────────┘
                      │
┌─────────────────────────────────────────────┐
│              Core Layer                      │
│  SimpleStorage │ EntityIndex │ Matching     │
│  Frontmatter   │ Research    │ Observability│
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

# Index operations
kvault index rebuild --kg-root my_kb
kvault index search --db my_kb/.kvault/index.db --query "Acme"

# Observability
kvault log summary --db my_kb/.kvault/logs.db
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/
mypy kvault/
```
