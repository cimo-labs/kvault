# Knowledge Base — Operating Rules

> Instructions for AI coding agents (Claude Code, OpenAI Codex, Gemini CLI, Cursor, GitHub Copilot, etc.)

## RULES (read these first, every session)

1. **PROPAGATE ALL ANCESTORS.** After any entity write, update EVERY `_summary.md` from parent to root.
   If the entity is at `people/contacts/professional/education/stella/`, update ALL FIVE:
   - `people/contacts/professional/education/_summary.md`
   - `people/contacts/professional/_summary.md`
   - `people/contacts/_summary.md`
   - `people/_summary.md`
   - `_summary.md` (root)

2. **FIX INTEGRITY WARNINGS FIRST.** If `kvault check` reports `[KB]` issues (or a pre-prompt hook
   surfaces them), fix every PROPAGATE and LOG warning before doing anything else.

3. **JOURNAL EVERY SESSION.** If you modified any entity today, `journal/YYYY-MM/log.md`
   must have an entry for today before the session ends. (Auto-logged if you pass `--reasoning` to `kvault write`.)

4. **FRONTMATTER REQUIRED.** Every entity needs `source` and `aliases` in YAML frontmatter.
   `created` and `updated` are set automatically by kvault.

5. **CHECK BEFORE WRITE.** Always browse the tree and read parent summaries before creating new entities.
   Search for existing entities (grep, find, or your tool's search) before creating. Never create duplicates.

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
Browse the tree and read parent summaries. Use your tool's search/read capabilities and kvault CLI:
```bash
kvault status --json                       # Get hierarchy tree
kvault read <path> --json                  # Returns entity + parent summary (sibling context)
kvault list [category] --json              # List entities in a category
```

Then decide:

| Situation | Action |
|-----------|--------|
| Entity exists, info is relevant | **UPDATE** existing |
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
incorporating the new entity, then batch-update:
```bash
kvault update-summaries --json <<'EOF'
[
  {"path": "people/friends", "content": "# Friends\n\nUpdated..."},
  {"path": "people", "content": "# People\n\nUpdated..."},
  {"path": ".", "content": "# Root\n\nUpdated..."}
]
EOF
```

---

## Entity Format

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

**Entity:** `kvault read`, `kvault write` (stdin), `kvault list`, `kvault delete`, `kvault move`
**Summary:** `kvault read-summary`, `kvault write-summary` (stdin), `kvault update-summaries` (stdin JSON), `kvault ancestors`
**Journal:** `kvault journal --source TEXT` (stdin JSON)
**Validation:** `kvault validate`, `kvault check`
**Status:** `kvault status`, `kvault tree`

All commands support `--json` for machine-readable output. Use `--kb-root` to specify the KB root
(auto-detected from cwd by default).

---

## Session Startup

No init call needed. kvault CLI auto-detects the KB root from the current directory.
Just `cd` into (or use `--kb-root` to point at) the knowledge base and start working.
