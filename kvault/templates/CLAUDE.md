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

5. **RESEARCH BEFORE WRITE.** Always search for existing entities before creating new ones.
   Use `kvault_search`. Never create duplicates.

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

## Workflow (5 steps)

### 1. RESEARCH — Find what exists
```
kvault_search(query="Alice Smith")    # fuzzy name/alias match
kvault_search(query="alice@acme.com") # email → exact alias + domain match
kvault_search(query="@acme.com")      # all entities at that domain
kvault_research(name="Alice Smith")   # dedupe check with suggested action
```

### 2. DECIDE — Create, update, or skip

| Situation | Action |
|-----------|--------|
| Entity exists, info is relevant | **UPDATE** existing |
| Doesn't exist, is significant | **CREATE** new |
| Doesn't exist, is trivial | **LOG** in journal only |

### 3. WRITE — Create/update the entity
```
kvault_write_entity(path="people/friends/alice", meta={...}, content="...", create=true)
```

### 4. PROPAGATE — Update ALL ancestor summaries
```
kvault_propagate_all(path="people/friends/alice")  # returns ancestors
```
Read each ancestor, update content, write back.

### 5. LOG — Journal entry
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

## MCP Tools Reference

**Search:** `kvault_search` — unified search (auto-detects name/email/domain queries)
**Entity:** `kvault_read_entity`, `kvault_write_entity`, `kvault_list_entities`, `kvault_delete_entity`, `kvault_move_entity`
**Summary:** `kvault_read_summary`, `kvault_write_summary`, `kvault_get_parent_summaries`, `kvault_propagate_all`
**Research:** `kvault_research` — dedupe check before creating
**Workflow:** `kvault_log_phase`, `kvault_write_journal`
**Validation:** `kvault_validate_kb`, `kvault_status`

---

## Session Startup

Call `kvault_init(kg_root=".")` once at the start of any KB session.
