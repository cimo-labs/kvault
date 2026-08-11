# Importing exported data

The fastest way to see kvault working on real content is to point your agent at data you
already have. Many chat, email, calendar, and notes tools can export JSON, Markdown, CSV,
mbox, or zip archives. Your agent can turn those raw exports into a structured, navigable
knowledge base in minutes.

> **Privacy note:** exports can contain sensitive information. kvault stores files locally in
> your KB, but your agent may read excerpts while processing them and send those excerpts to
> its model provider. Review or redact exports first if that matters for your use case.

## 1. Export source data

Download an archive from a tool you already use. Keep the raw files under `sources/` so they
stay separate from curated nodes.

## 2. Unzip it into your KB

```bash
mkdir -p my_kb/sources/conversations
unzip conversation-export.zip -d my_kb/sources/conversations
```

## 3. Tell your agent to process it

```
Read through the exported files in sources/conversations.
Extract the people, projects, and ideas I've discussed most frequently.
Create nodes for each one in the knowledge base.
```

The agent will use kvault CLI commands to create structured nodes with frontmatter and
propagate summaries. Expect it to follow the workflow from the generated `AGENTS.md`:
search for existing nodes before creating, write with `--reasoning`, and batch-update
ancestor summaries after each write.

Each write prints a receipt of what kvault actually did — including decisions it made on
the agent's behalf, as indented note lines — and names the ancestor summaries that now
need updating:

```
Created: people/alice_smith
  autofilled  source=auto:cli · aliases=[Alice Smith] · name=Alice Smith
Journal: journal/2026-08/log.md
Ancestors to update: 2  (people, .)
```

The batch summary update then confirms what it touched:

```
Updated 2 summaries: people, .
```

## Tips

- Set `KVAULT_SESSION=import-conversations` (any label) in the agent's environment before
  a long import, so the many commands it runs are correlated as one session in the ops
  log. Afterwards, `kvault log tail --session import-conversations` replays the whole
  import as a single unit of work.
- Process in batches (one week of conversations, one folder of notes) and let the agent
  run `kvault validate` between batches.
- Watch the first few nodes it creates and correct the structure early — category choices
  compound.
- After a large import, run `kvault tree` and apply the maintenance loop: split branches
  that exceeded ~10 children, and fix any `SUMMARY:` warnings from `kvault check`.
