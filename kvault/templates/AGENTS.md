# Knowledge Base — Operating Rules

> Instructions for AI coding agents that can read files or run shell commands.

## RULES (read these first, every session)

1. **PROPAGATE ALL ANCESTORS.** After any node write, update EVERY `_summary.md` from parent to root.
   If the node is at `people/contacts/professional/education/stella/`, update ALL FIVE:
   - `people/contacts/professional/education/_summary.md`
   - `people/contacts/professional/_summary.md`
   - `people/contacts/_summary.md`
   - `people/_summary.md`
   - `_summary.md` (root)

2. **FIX INTEGRITY WARNINGS FIRST.** If `kvault check` reports `[KB]` issues (or a pre-prompt hook
   surfaces them), fix every PROPAGATE and LOG warning before doing anything else. If it reports
   `SUMMARY:` warnings, improve those parent rollups when touching that area; they are warn-only.

3. **JOURNAL EVERY SESSION.** If you modified any node today, `journal/YYYY-MM/log.md`
   must have an entry for today before the session ends. (Auto-logged if you pass `--reasoning` to `kvault write`.)

4. **FRONTMATTER REQUIRED.** Every node needs `source` and `aliases` in YAML frontmatter.
   `created` and `updated` are set automatically by kvault.

5. **CHECK BEFORE WRITE.** Always browse the tree and read parent summaries before creating new nodes.
   Use `kvault search` and native tools such as `rg` before creating. Never create duplicates.

6. **PARENT SUMMARIES ARE ROLLUPS.** Every parent `_summary.md` must be a comprehensive current-state
   summary of all descendant summaries. Do not replace parent summaries with placeholders such as
   "see child files for details."

---

## About the Owner

**This knowledge base belongs to {{OWNER_NAME}}.**

Customize this section with your details.

---

## Structure

```
./
├── _summary.md              # Root: executive view
├── people/
│   ├── family/
│   ├── friends/
│   └── contacts/
├── projects/
├── accomplishments/
├── journal/YYYY-MM/log.md
└── .kvault/
    └── logs.db              # Observability
```

---

## Writing to the Knowledge Base (2-call workflow)

### 1. NAVIGATE — Find what exists and decide
Browse the tree and read parent summaries. Use native text search for exact phrases and kvault CLI
for structured node discovery:
```bash
kvault status --json                       # Get hierarchy tree
rg -n "search phrase" .                    # Raw filesystem search
kvault search "search phrase" --json       # Structured node search
kvault read <path> --json                  # Returns node + parent summary
kvault list [path] --json                  # List child nodes
```

Then decide:

| Situation | Action |
|-----------|--------|
| Node exists, info is relevant | **UPDATE** existing |
| Doesn't exist, is significant | **CREATE** new |
| Doesn't exist, is trivial | **LOG** in journal only |

### 2. WRITE — Create/update (Call 1)
```bash
kvault write <path> --create --reasoning "why" --json <<'EOF'
---
source: meeting_2026-02-25
aliases: [Alice Smith, alice@acme.com]
---

# Alice Smith

Context and notes here.
EOF
```
Output: `{"success": true, "ancestors": [{path, current_content, has_meta}, ...], "journal_logged": true}`

### 3. PROPAGATE — Batch-update ancestors (Call 2)
Read the `ancestors` array from Call 1's output. For each ancestor, compose an updated summary
incorporating the new node, then batch-update:
```bash
kvault update-summaries --json <<'EOF'
[
  {"path": "people/friends", "content": "# Friends\n\nUpdated..."},
  {"path": "people", "content": "# People\n\nUpdated..."},
  {"path": ".", "content": "# Root\n\nUpdated..."}
]
EOF
```

MCP clients should use strict parent-summary tools when available:

1. Call `kvault_write_node` with Markdown body content and metadata in `meta`.
2. For each returned ancestor, closest-first, call `kvault_prepare_summary_update`.
3. Compose the parent summary from the returned parent and immediate child summaries.
4. Call `kvault_write_parent_summary` with the new content and returned `children_digest`.
5. If `workflow_error` reports a stale digest, prepare that parent again and rewrite from the
   current direct children.
6. If a `hierarchy_hint` is returned, split the hierarchy when there is an obvious grouping;
   otherwise still keep the parent summary comprehensive.
7. Call `kvault_validate_kb` after larger edits.

---

## Node Format

```markdown
---
created: 2026-01-23
updated: 2026-01-23
source: manual
aliases: [Alice Smith, alice@acme.com]
---

# Alice Smith

Context and notes here.
```

**Required:** `source`, `aliases`
**Auto-set:** `created`, `updated`

---

## CLI Commands Reference

**Node:** `kvault search`, `kvault read`, `kvault write` (stdin), `kvault list`
**Compatibility:** `kvault read-summary`, `kvault write-summary` (stdin), `kvault update-summaries` (stdin JSON), `kvault ancestors`, `kvault delete`, `kvault move`
**Journal:** `kvault journal --source TEXT` (stdin JSON)
**Validation:** `kvault validate`, `kvault check`
**Status:** `kvault status`, `kvault tree`

All agent-facing commands support `--json` for machine-readable output and `--kb-root` to specify
the KB root (auto-detected from cwd by default). These flags work before or after the subcommand:
`kvault read people/friends/alice --json --kb-root ~/example_kb`.

---

## Session Startup

No init call needed. kvault CLI auto-detects the KB root from the current directory.
Just `cd` into (or use `--kb-root` to point at) the knowledge base and start working.
