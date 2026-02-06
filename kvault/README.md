# kvault Package

Main Python package for the knowledge graph framework.

## Module Structure

```
kvault/
├── __init__.py          # Package exports
├── cli/                 # Command-line interface (implemented)
├── core/                # Configuration and storage
├── matching/            # Entity matching strategies
├── pipeline/            # Processing pipeline (planned)
│   ├── agents/          # LLM-powered agents (planned)
│   ├── apply/           # Execution layer (planned)
│   ├── audit/           # Audit logging (planned)
│   └── staging/         # Staging database (planned)
└── templates/           # Default templates
```

## Package Exports

```python
# Core configuration and storage (from kvault)
from kvault import (
    KGraphConfig,
    load_config,
    FilesystemStorage,
)

# Matching strategies (from kvault.matching)
from kvault.matching import (
    load_strategies,
    MatchCandidate,
    EntityIndexEntry,
)

# Storage utilities (from kvault.core.storage)
from kvault.core.storage import normalize_entity_id

# Pipeline components (from kvault.pipeline)
from kvault.pipeline import (
    Orchestrator,
    StagingDatabase,
    QuestionQueue,
)
```

## Layer Architecture

```
┌─────────────────────────────────────────────┐
│                CLI Layer                     │
│  kvault process | resume | review | tree    │
└─────────────────────────────────────────────┘
                      │
┌─────────────────────────────────────────────┐
│             Pipeline Layer                   │
│  Orchestrator → Agents → Staging → Apply    │
└─────────────────────────────────────────────┘
                      │
┌─────────────────────────────────────────────┐
│              Core Layer                      │
│  Configuration │ Storage │ Matching         │
└─────────────────────────────────────────────┘
```

## Key Dependencies

- **pydantic**: Configuration validation
- **pyyaml**: YAML configuration files
- **click**: CLI framework

## CLI Quick Start

```
# Dry-run a corpus
kvault process --corpus /path/to/corpus --kg-root /path/to/kg --dry-run

# Apply changes
kvault process --corpus /path/to/corpus --kg-root /path/to/kg --apply

# Rebuild/search index
kvault index rebuild --kg-root /path/to/kg
kvault index search --db /path/to/kg/.kvault/index.db --query "Acme"

# Logs summary
kvault log summary --db /path/to/kg/.kvault/logs.db
```

Notes:
- The current CLI is web-free and uses heuristic extraction from `.txt/.md` files to seed people and orgs.
- All writes require `--apply`; otherwise commands produce a JSON plan only.

## Development

```bash
# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest tests/

# Type check
mypy kvault/
```
