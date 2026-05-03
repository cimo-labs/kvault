# Core Module

Foundation layer for node operations, structured search, storage, and observability.

## Components

### Node Operations (`operations.py`)

Shared business logic for CLI and MCP:

```python
from pathlib import Path
from kvault.core import operations as ops

root = Path("knowledge_graph")

node = ops.read_node(root, "people/friends/alice")
result = ops.write_node(root, "people/friends/alice", "# Alice\n\nUpdated.", create=False)
children = ops.list_nodes(root, "people")
matches = ops.search_nodes(root, "alice follow up")
prepared = ops.prepare_summary_update(root, "people/friends")
ops.write_parent_summary(
    root,
    "people/friends",
    "# Friends\n\nUpdated comprehensive rollup.",
    prepared["children_digest"],
)
```

`read_node` returns the requested node plus immediate parent context by default.
`prepare_summary_update` returns the parent summary, all immediate child summaries, a stateless
digest, and any advisory hierarchy hint. `write_parent_summary` rejects stale digests so callers
rewrite parent rollups from current direct children.

### Structured Search (`search.py`)

Stateless lexical search over visible `_summary.md` nodes:

```python
from kvault.core.search import search_nodes

results = search_nodes(Path("knowledge_graph"), "project notes", limit=5)
```

Search covers root, branch, and leaf summaries and returns ranked node hits with snippets.

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

### EntityResearcher (`research.py`)

Reusable matching and reconciliation suggestions for dedup/update flows:

```python
from kvault.core.research import EntityResearcher

researcher = EntityResearcher(Path("knowledge_graph"))
candidates = researcher.research("Universal Robots", aliases=["UR"])
action, target_path, confidence = researcher.suggest_action("Universal Robots", aliases=["UR"])
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
├── operations.py     # Node-first business logic
├── search.py         # Structured lexical node search
├── storage.py        # SimpleStorage + scan_entities + count/list
├── frontmatter.py    # YAML frontmatter parsing
├── research.py       # Entity matching + reconciliation suggestions
├── observability.py  # Phase-based logging
└── README.md
```

## Node Storage Format

### Preferred: YAML Frontmatter

Single `_summary.md` file with embedded metadata:

```markdown
---
created: 2026-01-23
updated: 2026-01-23
source: meeting_notes_2026_01_23
aliases: [Morgan Lee, Morgan]
topic: research collaboration
---

# Morgan Lee

Node content here.
```

### Legacy: Separate _meta.json

Still supported for backward compatibility:

```
people/alice/
├── _meta.json     # {"created": "...", "aliases": [...]}
└── _summary.md    # Markdown content
```

The scan_entities function parses frontmatter first, falls back to `_meta.json`.
