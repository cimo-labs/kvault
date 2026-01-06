# Core Module

Foundation layer for configuration and storage.

## Components

### KGraphConfig (`config.py`)

Pydantic v2 configuration model with validation:

```python
from kgraph.core import KGraphConfig

# Load from YAML
config = KGraphConfig.from_yaml("kgraph.yaml")

# Access configuration
print(config.project.name)
print(config.confidence.auto_merge)  # 0.95
print(config.matching.strategies)    # ['alias', 'fuzzy_name', 'email_domain']
```

**Configuration Sections:**
- `project` - Name, paths
- `entity_types` - Entity type definitions
- `tiers` - Tier storage configuration
- `confidence` - Auto-decision thresholds
- `matching` - Matching strategies and thresholds
- `processing` - Batch size, intervals

### FilesystemStorage (`storage.py`)

Tiered filesystem storage for entities:

```python
from kgraph.core import FilesystemStorage

storage = FilesystemStorage(config.kg_path, config)

# Write entity
storage.write_entity("customer", "acme_corp", {
    "name": "Acme Corporation",
    "tier": "strategic",
    "contacts": [{"name": "John", "email": "john@acme.com"}]
}, tier="strategic")

# Read entity
entity = storage.read_entity("customer", "acme_corp", tier="strategic")

# List entities
customers = storage.list_entities("customer", tier="strategic")

# Check existence
exists = storage.entity_exists("customer", "acme_corp", tier="strategic")
```

**Storage Types:**
- `directory` - Full directory with `_meta.json` and `_summary.md`
- `jsonl` - Compact JSONL registry for high-volume tiers

### normalize_entity_id (`storage.py`)

Converts entity names to filesystem-safe IDs:

```python
from kgraph.core import normalize_entity_id

normalize_entity_id("Acme Corporation")  # "acme_corporation"
normalize_entity_id("R&L Carriers")      # "rl_carriers"
normalize_entity_id("Universal Robots A/S")  # "universal_robots_as"
```

## File Structure

```
core/
├── __init__.py    # Exports: KGraphConfig, FilesystemStorage, normalize_entity_id
├── config.py      # Pydantic configuration models
└── storage.py     # Storage interface and filesystem implementation
```

## Entity Storage Format

### Directory Tier

```
customers/strategic/acme_corp/
├── _meta.json     # Machine-readable metadata
└── _summary.md    # Human-readable summary
```

### JSONL Tier

```
customers/prospects/
└── _registry.jsonl   # One JSON object per line
```
