# kgraph Package

Main Python package for the knowledge graph framework.

## Module Structure

```
kgraph/
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
# Core configuration and storage (from kgraph)
from kgraph import (
    KGraphConfig,
    load_config,
    FilesystemStorage,
)

# Matching strategies (from kgraph.matching)
from kgraph.matching import (
    load_strategies,
    MatchCandidate,
    EntityIndexEntry,
)

# Storage utilities (from kgraph.core.storage)
from kgraph.core.storage import normalize_entity_id

# Pipeline components (from kgraph.pipeline)
from kgraph.pipeline import (
    Orchestrator,
    StagingDatabase,
    QuestionQueue,
)
```

## Layer Architecture

```
┌─────────────────────────────────────────────┐
│                CLI Layer                     │
│  kgraph process | resume | review | tree    │
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
kgraph process --corpus /path/to/corpus --kg-root /path/to/kg --dry-run

# Apply changes
kgraph process --corpus /path/to/corpus --kg-root /path/to/kg --apply

# Rebuild/search index
kgraph index rebuild --kg-root /path/to/kg
kgraph index search --db /path/to/kg/.kgraph/index.db --query "Acme"

# Logs summary
kgraph log summary --db /path/to/kg/.kgraph/logs.db
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
mypy kgraph/
```
