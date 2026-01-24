# Core Module

Foundation layer for storage, indexing, and observability.

## Components

### EntityIndex (`index.py`)

SQLite-backed entity index with full-text search:

```python
from kgraph.core import EntityIndex

index = EntityIndex(Path(".kgraph/index.db"))

# Add entity
index.add("people/alice", "Alice Smith",
          aliases=["Alice", "alice@example.com", "+14155551234"],
          category="people")

# Search
results = index.search("Alice")

# Find by alias (exact match)
entry = index.find_by_alias("alice@example.com")
entry = index.find_by_alias("+14155551234")  # Phone lookup

# Rebuild from filesystem
count = index.rebuild(Path("knowledge_graph"))
```

### SimpleStorage (`storage.py`)

Filesystem storage for entities:

```python
from kgraph.core import SimpleStorage

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
```

### Frontmatter Utilities (`frontmatter.py`)

YAML frontmatter parsing for markdown files:

```python
from kgraph.core.frontmatter import parse_frontmatter, build_frontmatter, merge_frontmatter

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
from kgraph.core import ObservabilityLogger

logger = ObservabilityLogger(Path(".kgraph/logs.db"))

logger.log_research("Alice", "alice", matches, "create")
logger.log_decide("Alice", "create", "No match found", confidence=0.95)
logger.log_write("people/alice", "create", "Created entity")
logger.log_propagate("people/alice", ["people"])
```

### normalize_entity_id (`storage.py`)

Converts entity names to filesystem-safe IDs:

```python
from kgraph.core import normalize_entity_id

normalize_entity_id("Acme Corporation")  # "acme_corporation"
normalize_entity_id("R&L Carriers")      # "rl_carriers"
```

## File Structure

```
core/
├── __init__.py       # Exports
├── index.py          # EntityIndex with SQLite FTS
├── storage.py        # SimpleStorage filesystem operations
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

The index rebuilder (`index.rebuild()`) parses frontmatter first, falls back to `_meta.json`.
