# Knowledge Base — Operating Rules

## RULES (read these first, every session)

1. **PROPAGATE ALL ANCESTORS.** After any entity write, update EVERY `_summary.md` from parent to root.
   If the entity is at `people/contacts/professional/education/stella/`, update ALL FIVE:
   - `people/contacts/professional/education/_summary.md`
   - `people/contacts/professional/_summary.md`
   - `people/contacts/_summary.md`
   - `people/_summary.md`
   - `_summary.md` (root)

2. **FIX HOOK WARNINGS FIRST.** When the UserPromptSubmit hook reports `[KB]` issues,
   fix every PROPAGATE and LOG warning before doing anything else.

3. **JOURNAL EVERY SESSION.** If you modified any entity today, `journal/YYYY-MM/log.md`
   must have an entry for today before the session ends.

4. **FRONTMATTER REQUIRED.** Every entity needs `source` and `aliases` in YAML frontmatter.
   `created` and `updated` are set automatically by MCP tools.

5. **CHECK BEFORE WRITE.** Always browse the tree and read parent summaries before creating new entities.
   Use Grep/Glob/Read to check for existing entities. Never create duplicates.

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

## Workflow (4 steps)

### 1. NAVIGATE — Find what exists and decide
Browse the tree and read parent summaries. Use your own Grep/Glob/Read tools:
```
kvault_init(kg_root=".")              # Get hierarchy tree
kvault_read_entity(path="...")        # Returns entity + parent summary (sibling context)
kvault_list_entities(category="...")  # List entities in a category
```

Then decide:

| Situation | Action |
|-----------|--------|
| Entity exists, info is relevant | **UPDATE** existing |
| Doesn't exist, is significant | **CREATE** new |
| Doesn't exist, is trivial | **LOG** in journal only |

### 2. WRITE — Create/update the entity
```
kvault_write_entity(path="people/friends/alice", meta={...}, content="...", create=true)
```

### 3. PROPAGATE — Update ALL ancestor summaries
```
kvault_propagate_all(path="people/friends/alice")  # returns ancestors
```
Read each ancestor, update content, write back.

### 4. LOG — Journal entry
```
kvault_write_journal(actions=[...], source="manual")
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

## MCP Tools Reference (15)

**Entity:** `kvault_read_entity` (includes parent summary), `kvault_write_entity`, `kvault_list_entities`, `kvault_delete_entity`, `kvault_move_entity`
**Summary:** `kvault_read_summary`, `kvault_write_summary`, `kvault_get_parent_summaries`, `kvault_propagate_all`
**Workflow:** `kvault_log_phase`, `kvault_write_journal`, `kvault_validate_transition`
**Validation:** `kvault_validate_kb`, `kvault_status`
**Init:** `kvault_init`

---

## Session Startup

Call `kvault_init(kg_root=".")` once at the start of any KB session.
