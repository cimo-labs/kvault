# kvault

**Personal knowledge base that runs inside Claude Code.**

```bash
pip install knowledgevault[mcp]
```

kvault gives your coding agent persistent, structured memory. It runs as an MCP server inside Claude Code (or any MCP-compatible tool), using the subscription you already pay for. No extra API keys. No extra cost.

Your agent creates entities (people, projects, notes), deduplicates them with fuzzy matching, and keeps hierarchical summaries in sync — all through 17 MCP tools.

## Who is this for?

Developers using **Claude Code**, **OpenAI Codex**, **Cursor**, **VS Code + Copilot**, or any MCP-compatible tool who want their agent to remember things between sessions — contacts, projects, meeting notes, research — in a structured, searchable format.

## What makes it different?

| | kvault | [Anthropic memory server](https://github.com/anthropics/claude-code/tree/main/packages/memory) | [Notion AI](https://www.notion.so/product/ai) / [Mem.ai](https://mem.ai) | [obsidian-claude-pkm](https://github.com/4lph4-lab/obsidian-claude-pkm) |
|---|---|---|---|---|
| **Structure** | Hierarchical entities with dedup | Flat JSON | Rich docs, flat search | Obsidian vault |
| **Agent-native** | 17 MCP tools, built for agents | 4 tools, basic | Chat sidebar | Template, not runtime |
| **Cost** | $0 (uses existing subscription) | $0 | $12-20/mo extra | $0 |
| **Deduplication** | Fuzzy name + alias + email domain | None | None | Manual |
| **Summaries** | Auto-propagating hierarchy | None | AI-generated | Manual |

## Quickstart (60 seconds)

```bash
# 1. Install
pip install knowledgevault[mcp]

# 2. Create a knowledge base
kvault init my_kb --name "Your Name"

# 3. Verify it's clean
kvault check --kb-root my_kb
```

Add the MCP server to your AI tool's config:

**Claude Code** (`.claude/settings.json`):
```json
{
  "mcpServers": {
    "kvault": { "command": "kvault-mcp" }
  }
}
```

**OpenAI Codex** (`.codex/config.toml`):
```toml
[mcp_servers.kvault]
command = "kvault-mcp"
```

**Cursor** (`.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "kvault": { "command": "kvault-mcp" }
  }
}
```

**VS Code + GitHub Copilot** (`.vscode/mcp.json`):
```json
{
  "servers": {
    "kvault": { "command": "kvault-mcp", "type": "stdio" }
  }
}
```

**Windsurf** (`~/.codeium/windsurf/mcp_config.json`):
```json
{
  "mcpServers": {
    "kvault": { "command": "kvault-mcp" }
  }
}
```

Then tell your agent: *"Initialize the knowledge base at ./my_kb"* — it will call `kvault_init` and you're up.

## Try it: import your ChatGPT history

The best way to see kvault in action is to point it at data you already have. ChatGPT lets you export your entire conversation history — years of questions, people mentioned, projects discussed, decisions made — and Claude Code + kvault can turn it into a structured, searchable knowledge base in minutes.

**1. Export your ChatGPT data**

Go to [ChatGPT → Settings → Data controls → Export data](https://chatgpt.com/#settings/DataControls). You'll get an email with a zip file containing `conversations.json`.

**2. Unzip it into your KB**

```bash
unzip chatgpt-export.zip -d my_kb/sources/chatgpt
```

**3. Tell Claude Code to process it**

```
Read through my ChatGPT export in sources/chatgpt/conversations.json.
Extract the people, projects, and ideas I've discussed most frequently.
Create entities for each one in the knowledge base.
```

Claude Code will use the kvault tools to research each entity (deduplicating as it goes), create structured entries with frontmatter, and propagate summaries. You'll end up with a browsable, searchable knowledge base built from years of conversations you've already had.

**Other great data sources to try:**

| Source | How to get it | What you'll extract |
|--------|---------------|---------------------|
| **ChatGPT history** | Settings → Export data | People, projects, decisions, research threads |
| **Google Contacts** | [Google Takeout](https://takeout.google.com/) (Contacts) | Names, emails, phone numbers, notes |
| **iMessage** | `~/Library/Messages/chat.db` (macOS) | Relationships, interaction frequency, context |
| **Gmail** | [Google Takeout](https://takeout.google.com/) (Mail) | Professional contacts, threads, follow-ups |
| **Meeting notes** | Any folder of markdown/text files | People, action items, decisions |
| **Notion export** | Notion → Settings → Export | Projects, notes, wikis |

The pattern is always the same: drop the data into `sources/`, tell your agent to process it, and let kvault handle deduplication and structure.

## What happens next

Every time your agent processes new information, it follows a 5-step workflow:

1. **Research** — Search for existing entities (fuzzy name, alias, email domain matching)
2. **Decide** — Create, update, or skip based on match confidence
3. **Write** — Create/update entity with YAML frontmatter (`_summary.md`)
4. **Propagate** — Update all ancestor `_summary.md` files so summaries stay in sync
5. **Log** — Add entry to `journal/YYYY-MM/log.md`

No index rebuild needed — search reads files directly from disk.

## What an entity looks like

Each entity is a directory with a single `_summary.md` file containing YAML frontmatter:

```markdown
---
created: 2026-02-06
updated: 2026-02-06
source: manual
aliases: [Sarah Chen, sarah@anthropic.com]
email: sarah@anthropic.com
relationship_type: colleague
---
# Sarah Chen

Research scientist at Anthropic working on causal discovery.

## Background
Met at NeurIPS 2025. Collaborator on interpretability project.

## Interactions
- 2026-02-06: Coffee meeting — discussed causal representation learning

## Follow-ups
- [ ] Share CJE paper draft
```

**Required frontmatter:** `source`, `aliases` (the MCP tools set `created`/`updated` automatically)

## What a knowledge base looks like

```
my_kb/
├── _summary.md                          # Root: executive overview
├── people/
│   ├── _summary.md                      # "12 contacts across 3 categories"
│   ├── family/
│   │   ├── _summary.md
│   │   └── mom/
│   │       └── _summary.md
│   ├── friends/
│   │   ├── _summary.md
│   │   └── alex_rivera/
│   │       └── _summary.md
│   └── contacts/
│       ├── _summary.md
│       ├── sarah_chen/
│       │   └── _summary.md
│       └── james_park/
│           └── _summary.md
├── projects/
│   ├── _summary.md
│   └── cje_paper/
│       └── _summary.md
├── journal/
│   └── 2026-02/
│       └── log.md
└── .kvault/
    └── logs.db                          # Observability
```

Every directory with a `_summary.md` is a node. Summaries at each level capture the semantic landscape of their children.

## MCP tools (17)

| Category | Tools |
|----------|-------|
| **Init** | `kvault_init`, `kvault_status` |
| **Search** | `kvault_search` (auto-detects name, email, domain queries) |
| **Entity** | `kvault_read_entity`, `kvault_write_entity`, `kvault_list_entities`, `kvault_delete_entity`, `kvault_move_entity` |
| **Summary** | `kvault_read_summary`, `kvault_write_summary`, `kvault_get_parent_summaries`, `kvault_propagate_all` |
| **Research** | `kvault_research` |
| **Workflow** | `kvault_log_phase`, `kvault_write_journal`, `kvault_validate_transition` |
| **Validation** | `kvault_validate_kb` |

`kvault_write_entity` returns a `propagation_needed` list of ancestor paths, so agents know exactly which summaries to update.

## Python API

kvault also exposes a Python API for programmatic use:

```python
from pathlib import Path
from kvault import SimpleStorage, search, scan_entities, find_by_alias

kg_root = Path("my_kb")
storage = SimpleStorage(kg_root)

# Search (filesystem-based, no index needed)
results = search(kg_root, "Sarah Chen")        # Fuzzy name/alias/keyword
results = search(kg_root, "sarah@anthropic.com")  # Auto-detects email
match = find_by_alias(kg_root, "+14155551234")  # Exact alias lookup

# All entities
entities = scan_entities(kg_root)

# Navigate hierarchy
ancestors = storage.get_ancestors("people/contacts/sarah_chen")
# Returns: ["people/contacts", "people"]
```

## Integrity hook

Catch stale summaries before each prompt by adding to `.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "type": "command",
        "command": "kvault check --kb-root /absolute/path/to/my_kb"
      }
    ]
  }
}
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint, format, type-check
ruff check . && black . && mypy .
```

## License

MIT
