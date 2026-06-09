# kvault

**Persistent, structured memory for AI agents — plain Markdown, a CLI, zero services.**

```bash
pip install knowledgevault
```

Your agent creates nodes (people, projects, notes), keeps every parent summary a rollup of
what's below, and orients itself with one cheap command:

```text
$ kvault tree
. « Knowledge Base » [3 children, 11 total] ~2026-06-07
  notes [1 children, 1 total] ~2026-04-11
    reading_list ~2026-04-11
  people [2 children, 5 total] ~2026-06-02
    contacts [2 children, 2 total] ~2026-06-02
      mike_torres ~2026-01-20
      sarah_chen ~2026-06-02
    friends [1 children, 1 total] ~2026-03-14
      alex_rivera ~2026-03-14
  projects [2 children, 2 total] ~2026-06-07
    launch_plan « Launch Plan — v2 » ~2026-06-07
    website_redesign ~2026-05-28
```

One outline line per node: title, size, and most-recent activity — about 15 tokens each, so a
several-hundred-node KB orients an agent for a few thousand tokens. Anything pruned by
`--depth` or `--max-children` is called out in place (`…74 nodes below`), so a partial view
can never silently hide content.

Built for developers using AI coding tools who want their agent to remember things between
sessions — contacts, projects, meeting notes, research — in a structured, navigable format.
kvault needs no API keys, no hosted service, no database: any agent that can run shell
commands can use it.

## How it works

- **A node is a directory** containing a single `_summary.md` — YAML frontmatter plus
  Markdown. Leaf nodes are entities (a person, a project); parent nodes summarize their
  descendants.
- **Parent summaries are the index.** Every level is a comprehensive rollup of the subtree
  below it, written by the agent itself. Navigation is top-down reading, not blind grepping.
- **Writes propagate.** `kvault write` returns the full ancestor chain so the agent rewrites
  those summaries in one follow-up call — the "2-call write workflow."
- **The KB instructs the agent.** `kvault init` generates an `AGENTS.md` with the workflow,
  the rules (search before create, never fabricate, propagate everything), and a periodic
  maintenance playbook.

## Quickstart (30 seconds)

```bash
pip install knowledgevault
kvault init ./my_kb --name "Your Name"
```

Then tell your agent:

> "Use kvault CLI commands to manage my knowledge base at ./my_kb"

The agent reads the generated `AGENTS.md` and starts working.

| Tool | Setup |
|------|-------|
| **Project-instruction agents** | Keep `AGENTS.md` in the KB root so the agent reads the workflow automatically |
| **Terminal agents** | Tell the agent: *"Read AGENTS.md for the kvault workflow, then use shell commands to manage ./my_kb"* |
| **Custom-instruction agents** | Paste the generated `AGENTS.md` workflow into the workspace or system instructions |

**Using Claude Code?** A ready-made skill ships in this repo — it auto-loads the kvault
workflow whenever you ask the agent to work on your KB, from any directory:

```bash
mkdir -p ~/.claude/skills
cp -r skills/kvault ~/.claude/skills/kvault
```

Already have data? Point your agent at an export from a chat, email, or notes tool — see
[docs/importing-data.md](https://github.com/cimo-labs/kvault/blob/main/docs/importing-data.md).

## The 2-call write workflow

```bash
# Call 1: write the node (stdin = frontmatter + markdown body)
kvault write people/contacts/sarah_chen --create --reasoning "Met at NeurIPS" --json --kb-root ./my_kb <<'EOF'
---
source: manual
aliases: [Sarah Chen, sarah@example.com]
---
# Sarah Chen
Research scientist at Acme AI...
EOF
# → {"success": true, "ancestors": [{path, current_content}, ...], "journal_logged": true}

# Call 2: the agent rewrites the returned ancestors, including root
kvault update-summaries --json --kb-root ./my_kb <<'EOF'
[
  {"path": "people/contacts", "content": "# Contacts\n...updated..."},
  {"path": "people", "content": "# People\n...updated..."},
  {"path": ".", "content": "# Knowledge Base\n...updated..."}
]
EOF
```

Required frontmatter: `source`, `aliases` — kvault stamps `created`/`updated` automatically
(and preserves them on no-op rewrites, so the recency signal in the tree stays honest).

## The maintenance loop

KBs rot without pruning. The tree annotations make refactor triggers deterministic instead of
aspirational — agents read them off the orientation pass:

| Signal | Action |
|--------|--------|
| Branch with >10 children (`[N children, ...]`) | Split into subgroups; `kvault move` entities; re-propagate |
| Branch `~updated_max` older than ~6 months | Review for stale or dead content |
| `SUMMARY:` warnings from `kvault check` | Rewrite flagged parents as comprehensive rollups |
| Near-duplicate titles or aliases | Verify identifiers, merge, delete the duplicate |

`kvault check` also catches stale propagation, and works as a pre-prompt hook:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"type": "command", "command": "kvault check --kb-root /absolute/path/to/my_kb"}
    ]
  }
}
```

## CLI reference

| Category | Commands |
|----------|----------|
| **Orient & discover** | `kvault tree [path] [--depth N] [--max-children N] [--gist]`, `kvault search "<query>"` |
| **Nodes** | `kvault read`, `kvault write` (stdin), `kvault list`, `kvault delete`, `kvault move` |
| **Summaries** | `kvault read-summary`, `kvault write-summary` (stdin), `kvault update-summaries` (stdin JSON), `kvault ancestors` |
| **Quality** | `kvault validate`, `kvault check` |
| **Journal & artifacts** | `kvault journal`, `kvault artifact daily`, `kvault log summary` |
| **Lifecycle** | `kvault init`, `kvault status` |

Agent-facing commands accept `--json` for machine-readable output and `--kb-root`
(auto-detected from cwd by default), before or after the subcommand.

## MCP server (optional)

The CLI is the primary interface. For MCP-native clients, a stdio compatibility server ships
with the `[mcp]` extra (Python 3.10+), bound to one KB root per process:

```bash
pip install "knowledgevault[mcp]"
kvault-mcp --kb-root /absolute/path/to/my_kb
```

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

It exposes the same operations as the CLI (`kvault_tree`, `kvault_search`,
`kvault_read_node`, `kvault_write_node`, summary/journal/validation tools), plus a strict
parent-summary workflow with stale-write detection. Set `KVAULT_ALLOWED_ROOTS` to pin
allowed roots on shared runtimes. Protocol details:
[ARCHITECTURE.md](https://github.com/cimo-labs/kvault/blob/main/ARCHITECTURE.md).

## It's just files

kvault produces Markdown with YAML frontmatter in a plain directory. No proprietary format,
no database to export from. Your existing tools work out of the box:

| Want to... | Use |
|---|---|
| **Semantic search** | Embed the `.md` files with any vector tool |
| **Exact text search** | `rg -n "phrase" ./my_kb` |
| **Visual browsing** | Open the KB directory in Obsidian or Logseq |
| **Publish as a site** | Point Hugo, Jekyll, or Astro at the directory |
| **CI validation** | Run `kvault validate` or `kvault check` in a GitHub Action |
| **Bulk export** | `find . -name _summary.md` + `yq` over the frontmatter |

## Python API

```python
from pathlib import Path
from kvault.core import operations as ops

kg_root = Path("my_kb")
outline = ops.build_outline(kg_root, depth=2)          # annotated tree as nested dict
node = ops.read_node(kg_root, "people/contacts/sarah_chen")
result = ops.write_node(kg_root, "people/contacts/new_person", "# Content", create=True)
matches = ops.search_nodes(kg_root, "sarah follow up")
```

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
