# kgraph Package

Main Python package for the knowledge graph framework.

## Module Structure

```
kgraph/
├── __init__.py          # Package exports
├── cli/                 # Command-line interface
├── core/                # Configuration and storage
├── matching/            # Entity matching strategies
├── pipeline/            # Processing pipeline
│   ├── agents/          # LLM-powered agents
│   ├── apply/           # Execution layer
│   ├── audit/           # Audit logging
│   └── staging/         # Staging database
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

## Development

```bash
# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest tests/

# Type check
mypy kgraph/
```
