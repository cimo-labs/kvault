# kgraph

Config-driven knowledge graph framework for extracting structured knowledge from unstructured data.

## What It Does

kgraph helps you build knowledge graphs from messy data sources like emails, documents, or CRM exports. It uses LLM-powered entity extraction with fuzzy deduplication to create clean, structured knowledge bases.

**Key Features:**
- **Config-driven**: Define entity types, tiers, and matching rules in YAML
- **Fuzzy deduplication**: Multiple matching strategies (alias, fuzzy name, email domain)
- **Human-in-the-loop**: Surface ambiguous cases for review
- **Incremental processing**: Checkpoint-based batch processing

## Installation

```bash
pip install kgraph
```

Or install from source:

```bash
git clone https://github.com/eddiel/kgraph
cd kgraph
pip install -e .
```

## Quick Start

### 1. Initialize a project

```bash
kgraph init my-knowledge-base
cd my-knowledge-base
```

This creates:
```
my-knowledge-base/
├── kgraph.yaml           # Configuration
├── data/                 # Source data goes here
├── knowledge_graph/      # Output knowledge graph
├── prompts/              # LLM prompts
└── entity_types/         # Entity type schemas
```

### 2. Configure your entity types

Edit `kgraph.yaml` to define your entities:

```yaml
entity_types:
  person:
    directory: "people"
    tier_field: "importance"
    required_fields: [name, email]

  project:
    directory: "projects"
    tier_field: "priority"

tiers:
  critical:
    criteria:
      priority_min: 8
    storage_type: directory
  normal:
    criteria:
      priority_min: 4
      priority_max: 8
    storage_type: directory
  backlog:
    criteria:
      priority_max: 4
    storage_type: jsonl
```

### 3. Process your data

```bash
# Process data into knowledge graph
kgraph process

# Resume interrupted processing
kgraph resume

# Review pending questions
kgraph review
```

### 4. Explore your knowledge graph

```bash
# View structure
kgraph tree

# Validate integrity
kgraph validate --strict
```

## Configuration Reference

### Entity Types

```yaml
entity_types:
  customer:
    directory: "customers"      # Where to store entities
    tier_field: "tier"          # Field that determines tier
    required_fields:            # Required fields for validation
      - name
      - status
```

### Tiers

Tiers determine storage strategy and review frequency:

```yaml
tiers:
  strategic:
    criteria:
      revenue_min: 200000
    storage_type: directory     # Full directory with _meta.json
    review_frequency: quarterly
  prospect:
    criteria:
      revenue: 0
    storage_type: jsonl         # Compact JSONL registry
```

### Matching Strategies

```yaml
matching:
  strategies:
    - alias         # Exact match against known aliases (score: 1.0)
    - fuzzy_name    # Fuzzy string matching (score: 0.85-0.99)
    - email_domain  # Match by email domain (score: 0.85-0.95)
  fuzzy_threshold: 0.85
  generic_domains:
    - gmail.com
    - yahoo.com
```

### Confidence Thresholds

```yaml
confidence:
  auto_merge: 0.95    # Score >= this: auto-merge
  auto_update: 0.90   # Score >= this: auto-update
  auto_create: 0.50   # Score < this: auto-create new
  llm_required: [0.50, 0.95]  # Range requiring LLM decision
```

## Architecture

```
Data Source
    ↓
[Batch Processing]
    ↓
[LLM Extraction]  →  Extracts entities, relationships, signals
    ↓
[Research Phase]  →  Finds existing entity matches
    ↓
[Reconciliation]  →  Decides MERGE / UPDATE / CREATE
    ↓
[Entity Writer]   →  Writes to knowledge graph
    ↓
Knowledge Graph (filesystem)
```

## Python API

```python
from kgraph import load_config, FilesystemStorage
from kgraph.matching import load_strategies

# Load configuration
config = load_config("kgraph.yaml")

# Initialize storage
storage = FilesystemStorage(config.kg_path, config)

# List entities
customers = storage.list_entities("customer", tier="strategic")

# Read entity
entity = storage.read_entity("customer", "acme_corp", tier="strategic")

# Write entity
storage.write_entity("customer", "new_corp", {
    "name": "New Corp",
    "tier": "strategic",
    "industry": "technology",
    "contacts": [{"name": "John", "email": "john@newcorp.com"}]
}, tier="strategic")

# Load matching strategies
strategies = load_strategies(config.matching.strategies)
```

## Extending

### Custom Matching Strategy

```python
from kgraph.matching import MatchStrategy, register_strategy, MatchCandidate

@register_strategy("semantic")
class SemanticMatchStrategy(MatchStrategy):
    @property
    def name(self):
        return "semantic"

    @property
    def score_range(self):
        return (0.7, 0.95)

    def find_matches(self, entity, index, threshold=0.0):
        # Your embedding-based matching logic
        ...
```

### Custom Storage Backend

```python
from kgraph.core.storage import StorageInterface

class PostgresStorage(StorageInterface):
    def write_entity(self, entity_type, entity_id, data, tier=None):
        # Write to PostgreSQL
        ...

    def read_entity(self, entity_type, entity_id, tier=None):
        # Read from PostgreSQL
        ...
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black kgraph/

# Type check
mypy kgraph/
```

## License

MIT
