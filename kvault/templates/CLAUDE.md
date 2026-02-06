# Knowledge Base — Operating Rules

## RULES (read these first, every session)

1. **PROPAGATE ALL ANCESTORS.** After any entity write, update EVERY `_summary.md` from parent to root.
   If the entity is at `people/contacts/professional/education/stella/`, update ALL FIVE:
   - `people/contacts/professional/education/_summary.md`
   - `people/contacts/professional/_summary.md`
   - `people/contacts/_summary.md`  <- DON'T STOP HERE
   - `people/_summary.md`
   - `_summary.md` (root)
   Count the levels. If you wrote N entities at different depths, you may need to update
   the same ancestor multiple times — but each ancestor only needs one final update.

2. **FIX HOOK WARNINGS FIRST.** When the UserPromptSubmit hook reports `[KB]` issues,
   fix every PROPAGATE and LOG warning before doing anything else. The hook shows exact
   file paths — edit each one.

3. **JOURNAL EVERY SESSION.** If you modified any entity today, `journal/YYYY-MM/log.md`
   must have an entry for today before the session ends.

4. **FULL CORPUS ONLY.** When processing a data source, analyze ALL of it before writing.
   No sampling. Test: "Could someone who actually read all the data tell I didn't?"

5. **FRONTMATTER REQUIRED.** Every entity needs `source` and `aliases` in YAML frontmatter.
   `created` and `updated` are set automatically by MCP tools (set manually if using direct edits).

6. **RESEARCH BEFORE WRITE.** Always search for existing entities before creating new ones.
   Use Grep, Glob, or `kvault_search`. Never create duplicates.

---

## About the Owner

**This knowledge base belongs to {{OWNER_NAME}}.**

Customize this section with:
- Full name and family members
- Location and relevant context
- Current work and previous roles
- Any identity disambiguation notes

---

## Structure

```
./
├── _summary.md              # Root: executive view
├── people/
│   ├── family/              # Close family
│   ├── friends/             # Personal friends
│   └── contacts/            # Professional contacts, acquaintances
├── projects/
├── accomplishments/
├── journal/                 # YYYY-MM/log.md
└── .kvault/
    ├── index.db             # Entity search index
    └── logs.db              # Observability
```

---

## Workflow (6 steps, every time)

**Every time information is added, these steps happen in order.**

### 1. RESEARCH — Find what exists

Search before creating. Use any method:
- `kvault_search(query="term")` or `kvault_research(name="...", phone="...")`
- Grep/Glob for names, emails, phone numbers
- Read relevant `_summary.md` files

### 2. DECIDE — Create, update, or skip

| Situation | Action |
|-----------|--------|
| Entity exists, info is relevant | **UPDATE** existing |
| Doesn't exist, is significant | **CREATE** new |
| Doesn't exist, is trivial | **LOG** in journal only |

### 3. WRITE — Create/update the entity

Use `kvault_write_entity()` or direct file edits. Either works.

**Required frontmatter:** `source`, `aliases`
**Optional frontmatter:** `phone`, `email`, `relationship_type`, `context`, `related_to`, `last_interaction`, `status`

```markdown
---
created: 2026-01-23
updated: 2026-01-23
source: manual
aliases: [nickname, email@example.com, +14155551234]
phone: +14155551234
email: user@example.com
relationship_type: colleague
context: Work collaborator
---

# Name

**Relationship:** [relationship_type]
**Context:** [context]

## Background
[Freeform content]

## Interactions
- YYYY-MM-DD: [event]

## Follow-ups
- [ ] [action item]
```

**Do NOT create separate `_meta.json` files** — all metadata goes in frontmatter.

### 4. PROPAGATE — Update ALL ancestor summaries

See [Propagation Protocol](#propagation-protocol) below. This is the most-failed step.

### 5. LOG — Journal entry

Add to `journal/YYYY-MM/log.md`:

```markdown
## YYYY-MM-DD

### [Event Title]
- [What happened]
- -> See: [entity](../path/to/entity/)
```

### 6. REBUILD INDEX (new entities only)

```
kvault_rebuild_index()
```

Or: `kvault index rebuild --kg-root .`

---

## Propagation Protocol

After writing entity at `people/contacts/professional/education/stella/`:

**Generate the ancestor list:**
1. `people/contacts/professional/education/_summary.md`
2. `people/contacts/professional/_summary.md`
3. `people/contacts/_summary.md`
4. `people/_summary.md`
5. `_summary.md` (root)

**For each ancestor, in order:**
1. Read the current `_summary.md`
2. Find the line(s) referencing the changed child (or its parent category)
3. Update to reflect what changed
4. Write it back

**When you modify multiple entities**, some ancestors overlap. Update each shared
ancestor once at the end, incorporating all child changes.

**Quick count:** Entity depth minus 1 = number of summaries to update.
`people/contacts/professional/education/stella/` is depth 5 -> update 4 + root = 5 summaries.

**MCP shortcut:** `kvault_propagate_all(path="...")` returns all ancestors at once.
You still need to read each, update the content, and write each back.

---

## Full Source Processing

**When processing ANY data source (messages, emails, documents), you MUST analyze the FULL corpus before writing.**

**NO SAMPLING. NO SKIMMING. NO "representative examples."**

If the user points you at a file, thread, or data source — you process ALL of it.

### Before Writing ANY Entity

Run corpus-level analysis first:
1. Extract ALL names mentioned (frequency matters)
2. Extract ALL places, restaurants, locations
3. Extract ALL activities, interests, recurring topics
4. Extract work/professional mentions
5. Identify communication patterns, relationship texture

### The Test

Before you write a summary, ask: **"Could someone who actually read all this data tell I didn't?"**

If yes — go back and do the work.

---

## Querying the KB

When the user asks a question:

1. **Start with summaries** — they capture what matters about children
2. **Drill down** — read specific entities only when needed
3. **Synthesize** — don't dump raw data, give a coherent answer
4. **Cite sources** — link to entity paths
5. **Update if stale** — offer to update outdated info

**Quick lookup:**
```
kvault_init(kg_root=".")
kvault_search(query="Bryan")
kvault_read_entity(path="people/contacts/bryan")
```

**Exploratory:** Start from summaries -> scan for categories -> drill into entities.

---

## Periodic Maintenance

### Auto-Branching
When a directory exceeds **10 entities**, branch into subdirectories:
- **By type** — family/work/friends for people
- **Alphabetical** — a-m/n-z when no natural grouping
- **Temporal** — by year for journal-like content

Process: Count children -> identify groupings -> create subdirs with `_summary.md` -> move entities -> propagate.

### Other Refactoring
- **Duplicate detection** — similar names, same email domain
- **Stale info** — entities not updated in 6+ months
- **Missing cross-references** — related entities not linked

Triggers: ~10% chance after any workflow, or on user request.

---

## Integrity Hook Setup

Run `kvault check` automatically before each prompt to catch stale summaries:

Add to `.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "type": "command",
        "command": "kvault check --kb-root /absolute/path/to/kb"
      }
    ]
  }
}
```

The hook prints `[KB] Fix before continuing: ...` when issues are found.
Fix all PROPAGATE and LOG warnings before proceeding with other work.

---

## Key Principles

1. **YAML frontmatter is canonical** — no separate `_meta.json`
2. **Verify identifiers exactly** — never merge without exact phone/email match
3. **Flat by default** — subdirectories only when 10+ entities
4. **Summaries are semantic** — they capture what matters, not just list children

---

## Session Startup

Call `kvault_init(kg_root=".")` once at the start of any KB session.
It returns hierarchy, root summary, and entity count.

---

## Reference: MCP Tools

**Init:** `kvault_init`, `kvault_status`
**Search:** `kvault_search`, `kvault_find_by_alias`, `kvault_find_by_email_domain`
**Entity:** `kvault_read_entity`, `kvault_write_entity`, `kvault_list_entities`, `kvault_delete_entity`, `kvault_move_entity`
**Summary:** `kvault_read_summary`, `kvault_write_summary`, `kvault_get_parent_summaries`, `kvault_propagate_all`
**Research:** `kvault_research`
**Workflow:** `kvault_log_phase`, `kvault_write_journal`, `kvault_validate_transition`, `kvault_rebuild_index`
**Validation:** `kvault_validate_kb`

**Notes:**
- Most tools accept optional `session_id` for workflow tracking
- `kvault_write_entity` supports `auto_rebuild=true` to rebuild index automatically
- `kvault_write_entity` validates required frontmatter (`source`, `aliases`); `created`/`updated` set automatically

---

## Reference: kvault CLI

```bash
kvault index search --db .kvault/index.db --query "term"
kvault index rebuild --kg-root .
kvault check                    # Run integrity checks
kvault check --kb-root /path    # Check specific KB
```

---

## Reference: Error Recovery

- **Index stale:** `kvault_rebuild_index()` or `kvault index rebuild --kg-root .`
- **Propagation incomplete:** Read entity -> `kvault_propagate_all(path)` -> read and update each ancestor
- **Duplicates:** Merge into canonical entity -> delete duplicate -> rebuild index
- **Stale summaries:** List current children -> rewrite summary from scratch
