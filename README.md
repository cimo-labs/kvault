# kvault

**Tell your AI agent to build you a knowledge base. That's it.**

```bash
pip install knowledgevault
```

kvault gives your coding agent persistent, structured memory. It runs as a CLI tool that any agent can call via shell — Claude Code, OpenAI Codex, Cursor, or any tool that can execute commands. kvault itself requires no extra API keys, hosted service, or database.

Your agent creates entities (people, projects, notes), navigates the hierarchy via parent summaries, and keeps everything in sync — all through simple CLI commands.

## Who is this for?

Developers using **Claude Code**, **OpenAI Codex**, **Cursor**, **VS Code + Copilot**, or any AI coding tool who want their agent to remember things between sessions — contacts, projects, meeting notes, research — in a structured, navigable format.

## What makes it different?

| | kvault | [Anthropic memory server](https://github.com/anthropics/claude-code/tree/main/packages/memory) | [Notion AI](https://www.notion.so/product/ai) / [Mem.ai](https://mem.ai) | [obsidian-claude-pkm](https://github.com/4lph4-lab/obsidian-claude-pkm) |
|---|---|---|---|---|
| **Structure** | Hierarchical entities with navigable tree | Flat JSON | Rich docs, flat search | Obsidian vault |
| **Agent-native** | CLI commands, works in any subprocess | MCP server | Chat/sidebar workflow | Template, not runtime |
| **Service model** | Plain files + local CLI | Local server | Hosted workspace | Local vault |
| **Navigation** | Parent summaries at every level | None | AI-generated | Manual |
| **Search** | Agent uses its own search tools (grep, find, etc.) | Built-in | Built-in | Manual |

## Quickstart (30 seconds)

**1. Install**

```bash
pip install knowledgevault
```

**2. Initialize a knowledge base**

```bash
kvault init ./my_kb --name "Your Name"
```

**3. Tell your agent**

> "Use kvault CLI commands to manage my knowledge base at ./my_kb"

Your agent reads the generated `AGENTS.md` for workflow instructions and starts working.
Use `--kb-root ./my_kb` from the directory containing `my_kb`, or pass an absolute path.

**Tool-specific tips:**

| Tool | Setup |
|------|-------|
| **Claude Code** | Works automatically — reads `AGENTS.md` as project instructions |
| **OpenAI Codex CLI** | Tell it: *"Read AGENTS.md for the kvault workflow, then use shell commands to manage ./my_kb"* |
| **Gemini CLI** | Symlink `AGENTS.md` → `GEMINI.md`, or paste the workflow rules into your system prompt |
| **Cursor / Copilot** | Add `AGENTS.md` contents to your `.cursorrules` or workspace instructions |

## Try it: import your ChatGPT history

The best way to see kvault in action is to point it at data you already have. ChatGPT lets you export your entire conversation history — years of questions, people mentioned, projects discussed, decisions made — and your agent can turn it into a structured, navigable knowledge base in minutes.

Chat exports can contain sensitive information. kvault stores the files locally in your KB, but your agent may read excerpts while processing them and send those excerpts to its model provider. Review or redact the export first if that matters for your use case.

**1. Export your ChatGPT data**

Download a ChatGPT data export from ChatGPT's export controls. The archive includes `conversations.json`.

**2. Unzip it into your KB**

```bash
unzip chatgpt-export.zip -d my_kb/sources/chatgpt
```

**3. Tell your agent to process it**

```
Read through my ChatGPT export in sources/chatgpt/conversations.json.
Extract the people, projects, and ideas I've discussed most frequently.
Create entities for each one in the knowledge base.
```

Your agent will use kvault CLI commands to create structured entries with frontmatter and propagate summaries.

## The 2-call write workflow

```bash
# Call 1: Write entity (stdin = frontmatter + markdown body)
kvault write people/contacts/acme --create --reasoning "New customer" --json --kb-root ./my_kb <<'EOF'
---
source: meeting_2026-02-25
aliases: [ACME Corp]
---
# ACME Corp
Key customer acquired at trade show...
EOF
# → {"success": true, "ancestors": [{path, current_content, has_meta}, ...]}

# Call 2: Agent reads ancestors, composes updated summaries
kvault update-summaries --json --kb-root ./my_kb <<'EOF'
[
  {"path": "people/contacts", "content": "# Contacts\n...updated..."},
  {"path": "people", "content": "# People\n...updated..."}
]
EOF
# → {"success": true, "updated": ["people/contacts", "people"], "count": 2}
```

## What an entity looks like

Each entity is a directory with a single `_summary.md` file containing YAML frontmatter:

```markdown
---
created: 2026-02-06
updated: 2026-02-06
source: manual
aliases: [Morgan Lee, morgan@example.com]
email: morgan@example.com
---
# Morgan Lee

Research collaborator tracking evaluation notes and follow-up questions.
```

**Required frontmatter:** `source`, `aliases` (kvault sets `created`/`updated` automatically)

## What a knowledge base looks like

```
my_kb/
├── _summary.md                          # Root: executive overview
├── AGENTS.md                            # Agent workflow instructions
├── people/
│   ├── _summary.md                      # "12 contacts across 3 categories"
│   ├── family/
│   │   └── _summary.md
│   ├── friends/
│   │   ├── _summary.md
│   │   └── alex_rivera/
│   │       └── _summary.md
│   └── contacts/
│       ├── _summary.md
│       └── sarah_chen/
│           └── _summary.md
├── projects/
│   ├── _summary.md
│   └── launch_plan/
│       └── _summary.md
├── journal/
│   └── 2026-02/
│       └── log.md
└── .kvault/
    └── logs.db                          # Observability
```

## CLI commands

| Category | Commands |
|----------|----------|
| **Entity** | `kvault read`, `kvault write`, `kvault list`, `kvault delete`, `kvault move` |
| **Summary** | `kvault read-summary`, `kvault write-summary`, `kvault update-summaries`, `kvault ancestors` |
| **Journal** | `kvault journal` |
| **Status** | `kvault status`, `kvault tree` |
| **Validation** | `kvault validate`, `kvault check` |
| **Artifacts** | `kvault artifact daily` |
| **Logs** | `kvault log summary` |
| **UI** | `kvault ui` |
| **Init** | `kvault init` |

Agent-facing commands support `--json` for machine-readable output. `--kb-root` overrides
auto-detection on KB-bound commands, and it works before or after the subcommand:

```bash
kvault read people/friends/alice --json --kb-root ./my_kb
kvault artifact daily --json --kb-root ./my_kb
```

## Optional local UI

Install the UI extra to browse a KB in a local read-only web app:

```bash
pip install "knowledgevault[ui]"
kvault ui --kb-root ./my_kb
```

## Optional MCP compatibility

CLI remains the primary interface for shell-capable agents. For MCP-native clients, kvault also
ships a stdio MCP compatibility server:

```bash
pip install "knowledgevault[mcp]"
kvault-mcp --kb-root /absolute/path/to/my_kb
```

`knowledgevault[mcp]` requires Python 3.10+. The server is bound to one KB root per process; use a
separate `kvault-mcp` process for each KB. You can pass the root with `--kb-root` or set
`KVAULT_KB_ROOT`.

Generic MCP client config:

```json
{
  "mcpServers": {
    "kvault": {
      "command": "kvault-mcp",
      "args": ["--kb-root", "/absolute/path/to/my_kb"]
    }
  }
}
```

Root-pinned config for shared runtimes:

```json
{
  "mcpServers": {
    "kvault": {
      "command": "kvault-mcp",
      "args": ["--kb-root", "/absolute/path/to/my_kb"],
      "env": {
        "KVAULT_ALLOWED_ROOTS": "/absolute/path/to/my_kb"
      }
    }
  }
}
```

The compatibility server exposes root-bound tools for status, entity CRUD, listing, summary
read/write/update, ancestor lookup, journaling, daily artifact generation, validation, and phase
logging:

| Category | Tools |
|----------|-------|
| **Lifecycle** | `kvault_init`, `kvault_status` |
| **Entities** | `kvault_read_entity`, `kvault_write_entity`, `kvault_list_entities`, `kvault_delete_entity`, `kvault_move_entity` |
| **Summaries** | `kvault_read_summary`, `kvault_write_summary`, `kvault_update_summaries`, `kvault_get_parent_summaries`, `kvault_get_ancestors`, `kvault_propagate_all` |
| **Journal / artifacts** | `kvault_write_journal`, `kvault_generate_daily_artifact` |
| **Validation / logging** | `kvault_validate_kb`, `kvault_log_phase` |

Compatibility tools accept an optional `kg_root` argument for older clients, but it must match the
server-bound root. `kvault_init` reports bound-root status and rejects mismatched roots; it does not
create or reinitialize a KB.

MCP clients should use the same propagation workflow as the CLI:

1. Call `kvault_status` or `kvault_list_entities` to orient.
2. Call `kvault_read_entity` / `kvault_read_summary` before editing.
3. Call `kvault_write_entity` with durable frontmatter and body content.
4. Use the returned ancestors or `kvault_get_parent_summaries` to update parent summaries.
5. Call `kvault_update_summaries` so every parent remains a useful rollup.
6. Call `kvault_validate_kb` after larger edits.

## Optional root pinning (multi-tenant hardening)

For shared runtimes, pin allowed roots:

```bash
export KVAULT_ALLOWED_ROOTS="/absolute/path/to/my_kb"
```

## Python API

```python
from pathlib import Path
from kvault.core import operations as ops

kg_root = Path("my_kb")

# Read/write entities
entity = ops.read_entity(kg_root, "people/contacts/sarah_chen")
result = ops.write_entity(kg_root, "people/contacts/new_person", "# Content", create=True)

# Scan and search
from kvault import scan_entities, EntityResearcher
entities = scan_entities(kg_root)
researcher = EntityResearcher(kg_root)
action, target, confidence = researcher.suggest_action("Morgan Lee")
```

## Integrity check

Run `kvault check` to catch stale summaries and weak parent rollups:

```bash
kvault check --kb-root /absolute/path/to/my_kb
```

`[KB]` warnings keep the existing nonzero exit behavior. `SUMMARY:` warnings are warn-only by
default, capped at 5 lines, and call out parent summaries that are too short, omit immediate
children, or contain placeholder/redirect language. Use `--no-summary-quality` to skip that audit.

If your tool supports pre-prompt hooks, you can automate this. For example, in Claude Code's `.claude/settings.json`:

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

## It's just files

kvault produces Markdown with YAML frontmatter in a plain directory. No proprietary format, no database to export from. Your existing tools work out of the box:

| Want to... | Use |
|---|---|
| **Semantic search** | Embed the `.md` files with any vector tool (OpenAI, Chroma, txtai, etc.) |
| **Visual browsing** | Open the KB directory in Obsidian or Logseq |
| **Publish as a site** | Point Hugo, Jekyll, or Astro at the directory |
| **CI validation** | Run `kvault validate` or `kvault check` in a GitHub Action |
| **Bulk export** | `find . -name _summary.md` + `yq` over the frontmatter |

## Development

```bash
pip install -e ".[dev,ui,mcp]"
pytest -q
ruff check .
black --check kvault/ tests/
mypy kvault/ --ignore-missing-imports
```

## License

MIT
