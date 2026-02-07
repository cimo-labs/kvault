# Core Module

Foundation layer for storage and observability.

## Components

### SimpleStorage + Entity Scanning (`storage.py`)

Filesystem storage for entities plus entity scanning:

```python
from kvault.core.storage import SimpleStorage, scan_entities, count_entities, list_entity_records

storage = SimpleStorage(Path("knowledge_graph"))

# Create entity
storage.create_entity("people/alice", {
    "created": "2026-01-05",
    "last_updated": "2026-01-05",
    "sources": ["manual"],
    "aliases": ["Alice"]
}, summary="# Alice\n\nDescription here.")

# Read
meta = storage.read_meta("people/alice")
summary = storage.read_summary("people/alice")

# Navigate hierarchy
ancestors = storage.get_ancestors("people/collaborators/alice")
# Returns: ["people/collaborators", "people"]

# Scan all entities
entities = scan_entities(Path("knowledge_graph"))
count = count_entities(Path("knowledge_graph"), category="people")
```

### Frontmatter Utilities (`frontmatter.py`)

YAML frontmatter parsing for markdown files:

```python
from kvault.core.frontmatter import parse_frontmatter, build_frontmatter, merge_frontmatter

# Parse
content = open("_summary.md").read()
meta, body = parse_frontmatter(content)  # Returns (dict, str)

# Build
frontmatter = build_frontmatter({"created": "2026-01-23", "aliases": ["Alice"]})

# Merge (for updates)
merged = merge_frontmatter(existing_meta, new_meta)
```

### ObservabilityLogger (`observability.py`)

Phase-based logging for debugging:

```python
from kvault.core import ObservabilityLogger

logger = ObservabilityLogger(Path(".kvault/logs.db"))

logger.log_research("Alice", "alice", matches, "create")
logger.log_decide("Alice", "create", "No match found", confidence=0.95)
logger.log_write("people/alice", "create", "Created entity")
logger.log_propagate("people/alice", ["people"])
```

### normalize_entity_id (`storage.py`)

Converts entity names to filesystem-safe IDs:

```python
from kvault.core import normalize_entity_id

normalize_entity_id("Acme Corporation")  # "acme_corporation"
normalize_entity_id("R&L Carriers")      # "rl_carriers"
```

## File Structure

```
core/
├── __init__.py       # Exports
├── storage.py        # SimpleStorage + scan_entities + count/list
├── frontmatter.py    # YAML frontmatter parsing
├── observability.py  # Phase-based logging
└── README.md
```

## Entity Storage Format

### Preferred: YAML Frontmatter

Single `_summary.md` file with embedded metadata:

```markdown
---
created: 2026-01-23
updated: 2026-01-23
source: imessage:abc123
aliases: [Alice, alice@example.com, +14155551234]
phone: +14155551234
email: alice@example.com
---

# Alice Smith

Entity content here.
```

### Legacy: Separate _meta.json

Still supported for backward compatibility:

```
people/alice/
├── _meta.json     # {"created": "...", "aliases": [...]}
└── _summary.md    # Markdown content
```

The scan_entities function parses frontmatter first, falls back to `_meta.json`.
