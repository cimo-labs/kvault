# Core Module

Foundation layer for storage, search, and observability.

## Components

### Filesystem Search (`search.py`)

Searches entities by scanning `_summary.md` files directly — no index needed:

```python
from kvault.core.search import search, scan_entities, find_by_alias, find_by_email_domain

# Unified search (auto-detects name, email, domain queries)
results = search(kg_root, "Alice")
results = search(kg_root, "alice@example.com")

# Exact alias lookup
entry = find_by_alias(kg_root, "alice@example.com")
entry = find_by_alias(kg_root, "+14155551234")  # Phone lookup

# Domain search
results = find_by_email_domain(kg_root, "acme.com")

# Full scan
entities = scan_entities(kg_root)
```

### SimpleStorage (`storage.py`)

Filesystem storage for entities:

```python
from kvault.core import SimpleStorage

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
├── search.py         # Filesystem-based search (no SQLite)
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

The search module parses frontmatter first, falls back to `_meta.json`.
