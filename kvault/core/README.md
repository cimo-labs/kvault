# Core Module

Foundation layer for node operations, structured search, storage, and work reporting.

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

### Decision Notes (`notes.py`)

The work-reporting vocabulary. A note is emitted only when kvault invented a value,
deliberately changed nothing, half-failed, hid something, or fell back — operations that
went exactly as asked stay silent. The code set is closed: 10 codes (`autofilled`,
`unchanged`, `partial`, `created`, `removed`, `truncated`, `skipped`, `waited`, `guessed`,
`propagate`), each with a one-sentence contract next to `NOTE_CODES`. An unknown code
raises `UnknownNoteCode` at build time; new reportable behaviour maps onto an existing
code or amends the vocabulary explicitly.

```python
from kvault.core.notes import note, collapse

n = note(
    "skipped",
    "could not read people/alice/_summary.md",
    detail={"path": "people/alice"},
    why="file is not valid UTF-8",
    next_step="fix the encoding, then re-run",
)
# {"code": "skipped", "text": "...", "level": 1,
#  "detail": {"path": "people/alice"}, "why": "...", "next": "..."}

# Batch commands collapse per-item notes by code, so a 40-ancestor
# maintenance run emits one line, not forty:
collapse([n, n, n])
# [{"code": "skipped", "level": 1, "count": 3, "examples": ["people/alice: ...", ...]}]
```

Notes live *inside* the result dict and are never printed from `core` or `mcp` — stdout
there is the MCP JSON-RPC transport. Rendering happens only in `kvault/cli/render.py`.
`partial` is the one code that survives every verbosity tier including `--quiet`:
silencing a half-failure on request is a footgun.

### Durable Ops Log (`oplog.py`)

One row per completed KB operation in the `ops` table of `.kvault/logs.db`: op, path, the
`did` line, notes, changed/partial flags, duration, UTC timestamp, and session. Every
successful mutating command appends automatically (CLI surface `"cli"`, MCP surface
`"mcp"`); read it with `kvault log tail` / `kvault log summary` or the `kvault_log_tail`
MCP tool.

```python
from kvault.core.oplog import OpLog

log = OpLog(kg_root=Path("knowledge_graph"))  # or db_path=...
log.append("write_node", result, ms=12.3, surface="cli")  # False on failure, never raises
recent = log.tail(limit=20)   # newest first; [] on any failure
stats = log.summary()         # {"total_ops": ..., "sessions": ..., "op_counts": ..., "partial_count": ...}
```

The never-fail contract: `append` swallows every exception and returns `False` — a corrupt
or unwritable log can never fail a KB write (the CLI surfaces the miss as a `skipped`
note). It runs after the mutation has committed, never inside its critical section. No
WAL, ever (`-wal`/`-shm` sidecars break gitignore-based sync automation). Bounded: note
payloads capped at 4 KB, table pruned at 5,000 rows. `KVAULT_SESSION` correlates the
several commands of one logical task into one session; `KVAULT_OPS_LOG=0` disables the
append.

### ObservabilityLogger (`observability.py`) — legacy

The phase-based `logs` table in the same `.kvault/logs.db`, frozen for backward
compatibility: existing databases and the MCP `kvault_log_phase` tool keep working, but
new work lands in the `ops` table via `OpLog`.

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
├── __init__.py          # Exports
├── operations.py        # Node-first business logic
├── search.py            # Structured lexical node search
├── storage.py           # SimpleStorage + scan_entities + count/list
├── frontmatter.py       # YAML frontmatter parsing
├── notes.py             # Decision-note vocabulary + collapse policy
├── oplog.py             # Durable ops table (append/tail/summary)
├── events.py            # Capture journal: pending events under .kvault/events/
├── locks.py             # Atomic file writes + per-KB write lock
├── paths.py             # Symlink-aware path containment helpers
├── validation.py        # Shared validation rules for CLI and MCP
├── research.py          # Entity matching + reconciliation suggestions
├── summary_quality.py   # Parent-summary quality auditing
├── daily_artifacts.py   # Deterministic daily artifact generation
├── observability.py     # Legacy phase-based logging (frozen)
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
