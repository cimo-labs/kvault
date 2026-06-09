# kvault

**Tell your AI agent to build you a knowledge base. That's it.**

```bash
pip install knowledgevault
```

kvault gives your coding agent persistent, structured memory. It runs as a CLI tool that any agent can call via shell: terminal coding agents, editors, IDE assistants, or any tool that can execute commands. kvault itself requires no extra API keys, hosted service, or database.

Your agent creates nodes (people, projects, notes, categories), navigates the hierarchy via parent summaries, and keeps everything in sync — all through simple CLI commands.

## Who is this for?

Developers using AI coding tools who want their agent to remember things between sessions — contacts, projects, meeting notes, research — in a structured, navigable format.

## What makes it different?

| | kvault | Flat memory files | Hosted docs / memory apps | Note-vault templates |
|---|---|---|---|---|
| **Structure** | Hierarchical nodes with navigable tree | Flat JSON | Rich docs, flat search | Obsidian vault |
| **Agent-native** | CLI commands, works in any subprocess | MCP server | Chat/sidebar workflow | Template, not runtime |
| **Service model** | Plain files + local CLI | Local server | Hosted workspace | Local vault |
| **Navigation** | Parent summaries at every level | None | AI-generated | Manual |
| **Search** | Structured node search + native grep/find | Built-in | Built-in | Manual |

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

**Tool setup tips:**

| Tool | Setup |
|------|-------|
| **Project-instruction agents** | Keep `AGENTS.md` in the KB root so the agent reads the workflow automatically |
| **Terminal agents** | Tell the agent: *"Read AGENTS.md for the kvault workflow, then use shell commands to manage ./my_kb"* |
| **Custom-instruction agents** | Paste the generated `AGENTS.md` workflow into the workspace or system instructions |

## Try it: import an exported history

The best way to see kvault in action is to point it at data you already have. Many chat, email, calendar, and notes tools can export JSON, Markdown, CSV, mbox, or zip archives. Your agent can turn those raw exports into a structured, navigable knowledge base in minutes.

Exports can contain sensitive information. kvault stores files locally in your KB, but your agent may read excerpts while processing them and send those excerpts to its model provider. Review or redact exports first if that matters for your use case.

**1. Export source data**

Download an archive from a tool you already use. Keep the raw files under `sources/` so they stay separate from curated nodes.

**2. Unzip it into your KB**

```bash
mkdir -p my_kb/sources/conversations
unzip conversation-export.zip -d my_kb/sources/conversations
```

**3. Tell your agent to process it**

```
Read through the exported files in sources/conversations.
Extract the people, projects, and ideas I've discussed most frequently.
Create nodes for each one in the knowledge base.
```

Your agent will use kvault CLI commands to create structured nodes with frontmatter and propagate summaries.

## The CLI 2-call write workflow

```bash
# Call 1: Write node (stdin = frontmatter + markdown body)
kvault write people/contacts/acme --create --reasoning "New customer" --json --kb-root ./my_kb <<'EOF'
---
source: meeting_2026-02-25
aliases: [ACME Corp]
---
# ACME Corp
Key customer acquired at trade show...
EOF
# → {"success": true, "ancestors": [{path, current_content, has_meta}, ...]}

# Call 2: Agent reads ancestors, composes updated summaries, including root
kvault update-summaries --json --kb-root ./my_kb <<'EOF'
[
  {"path": "people/contacts", "content": "# Contacts\n...updated..."},
  {"path": "people", "content": "# People\n...updated..."},
  {"path": ".", "content": "# Knowledge Base\n...updated..."}
]
EOF
# → {"success": true, "updated": ["people/contacts", "people", "."], "count": 3}
```

## What a node looks like

Each node is a directory with a single `_summary.md` file containing YAML frontmatter. Leaf nodes often represent entities such as people, projects, or notes; parent nodes summarize their descendants.

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
| **Node** | `kvault search`, `kvault read`, `kvault write`, `kvault list` |
| **Compatibility** | `kvault read-summary`, `kvault write-summary`, `kvault ancestors`, `kvault delete`, `kvault move` |
| **Journal** | `kvault journal` |
| **Status** | `kvault status`, `kvault tree [path] [--depth N] [--max-children N] [--gist]` |
| **Validation** | `kvault validate`, `kvault check` |
| **Artifacts** | `kvault artifact daily` |
| **Logs** | `kvault log summary` |
| **Init** | `kvault init` |

Agent-facing commands support `--json` for machine-readable output. `--kb-root` overrides
auto-detection on KB-bound commands, and it works before or after the subcommand:

```bash
kvault read people/friends/alice --json --kb-root ./my_kb
kvault search "alice project notes" --json --kb-root ./my_kb
kvault artifact daily --json --kb-root ./my_kb
```

`kvault search` is structured node discovery: it searches visible `_summary.md` files, ranks title,
path, alias, heading, and body matches, and returns node paths with snippets. Raw filesystem search
is still useful for exact text investigation:

```bash
rg -n "alice|project" ./my_kb
kvault search "alice project" --json --kb-root ./my_kb
kvault read people/friends/alice --json --kb-root ./my_kb
```

`kvault read <path>` is canonical context retrieval. It returns the full requested node plus its
immediate parent summary by default.

## File-native browsing

kvault is just Markdown files and CLI tools. For navigation, combine structured commands with
native file search or your preferred Markdown viewer:

```bash
kvault tree --kb-root ./my_kb
kvault search "alice project" --json --kb-root ./my_kb
kvault read people/friends/alice --json --kb-root ./my_kb
rg -n "alice|project" ./my_kb
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

The compatibility server exposes root-bound tools for node search/read/write/list, legacy entity
and summary calls, ancestor lookup, journaling, daily artifact generation, validation, and phase
logging:

| Category | Tools |
|----------|-------|
| **Lifecycle** | `kvault_init`, `kvault_status` |
| **Nodes** | `kvault_tree`, `kvault_search`, `kvault_read_node`, `kvault_write_node`, `kvault_list_nodes` |
| **Compatibility** | `kvault_read_entity`, `kvault_write_entity`, `kvault_list_entities`, `kvault_read_summary`, `kvault_write_summary`, `kvault_delete_entity`, `kvault_move_entity` |
| **Summaries** | `kvault_prepare_summary_update`, `kvault_write_parent_summary`, `kvault_update_summaries`, `kvault_get_parent_summaries`, `kvault_get_ancestors`, `kvault_propagate_all` |
| **Journal / artifacts** | `kvault_write_journal`, `kvault_generate_daily_artifact` |
| **Validation / logging** | `kvault_validate_kb`, `kvault_log_phase` |

Compatibility tools accept an optional `kg_root` argument for older clients, but it must match the
server-bound root. `kvault_init` reports bound-root status and rejects mismatched roots; it does not
create or reinitialize a KB.

MCP clients should prefer the strict parent-summary workflow:

1. Call `kvault_tree` (annotated outline with counts, recency, and explicit truncation
   markers — the cheapest full-tree view), `kvault_search`, or `kvault_list_nodes` to orient.
2. Call `kvault_read_node` before editing; it returns the node plus parent context.
3. Call `kvault_write_node` with Markdown body content and durable metadata in `meta`.
4. For each returned ancestor, closest-first, call `kvault_prepare_summary_update`.
5. Compose the parent summary from the returned parent and immediate child summaries.
6. Call `kvault_write_parent_summary` with the new content and the returned `children_digest`.
7. Call `kvault_validate_kb` after larger edits.

`children_digest` is stateless and only proves the direct child summaries have not changed since
the prepare call. If another write changes a direct child first, `kvault_write_parent_summary`
returns `workflow_error`; call `kvault_prepare_summary_update` again and compose from the current
children.

When a parent has more than 10 direct children, `kvault_prepare_summary_update` returns a
`hierarchy_hint`. This is advisory: split the hierarchy when natural groups are obvious, otherwise
still keep the parent summary comprehensive. Compatibility tools such as `kvault_update_summaries`
and `kvault_write_summary` remain available for older clients and manual maintenance.

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

# Read/write/search nodes
node = ops.read_node(kg_root, "people/contacts/sarah_chen")
result = ops.write_node(kg_root, "people/contacts/new_person", "# Content", create=True)
matches = ops.search_nodes(kg_root, "sarah follow up")
prepared = ops.prepare_summary_update(kg_root, "people/contacts")
ops.write_parent_summary(
    kg_root,
    "people/contacts",
    "# Contacts\n\nUpdated rollup.",
    prepared["children_digest"],
)

# Entity reconciliation remains available for dedup decisions
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

If your tool supports pre-prompt hooks, you can automate this:

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
| **Semantic search** | Embed the `.md` files with any vector tool |
| **Exact text search** | `rg -n "phrase" ./my_kb` |
| **Visual browsing** | Open the KB directory in Obsidian or Logseq |
| **Publish as a site** | Point Hugo, Jekyll, or Astro at the directory |
| **CI validation** | Run `kvault validate` or `kvault check` in a GitHub Action |
| **Bulk export** | `find . -name _summary.md` + `yq` over the frontmatter |

## Development

```bash
pip install -e ".[dev,mcp]"
pytest -q
ruff check .
black --check kvault/ tests/
mypy kvault/ --ignore-missing-imports
```

## License

MIT
