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

**Agent skill included.** [`skills/kvault/SKILL.md`](https://github.com/cimo-labs/kvault/blob/main/skills/kvault/SKILL.md)
carries the full workflow in the portable `SKILL.md` agent-skills format, so the agent loads
it on demand from any directory — no per-KB setup. Install it wherever your tool discovers
skills:

```bash
# Claude Code
cp -r skills/kvault ~/.claude/skills/kvault

# OpenClaw (per workspace)
cp -r skills/kvault ~/.openclaw/workspace/skills/kvault

# Other agents: copy into your tool's skills directory, or paste the
# SKILL.md body into its custom instructions
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
# → {"success": true, "changed": true, "did": "created people/contacts/sarah_chen",
#    "notes": [{"code": "autofilled", "text": "name=Sarah Chen", ...}],
#    "ancestor_paths": ["people/contacts", "people", "."],
#    "ancestors": [{path, current_content}, ...], "journal_logged": true}

# Call 2: the agent rewrites the returned ancestors, including root
kvault update-summaries --json --kb-root ./my_kb <<'EOF'
[
  {"path": "people/contacts", "content": "# Contacts\n...updated..."},
  {"path": "people", "content": "# People\n...updated..."},
  {"path": ".", "content": "# Knowledge Base\n...updated..."}
]
EOF
```

In human mode the same write narrates its decisions under the receipt:

```text
Created: people/contacts/sarah_chen
  autofilled  name=Sarah Chen
Journal: journal/2026-08/log.md
Ancestors to update: 3  (people/contacts, people, .)
```

Re-sending identical content is a detected no-op — the file is not rewritten, mtime and
`created`/`updated` stay put, so the recency signal in the tree stays honest:

```text
Unchanged: people/contacts/sarah_chen
  unchanged   body and metadata identical — file not rewritten, created 2026-08-10, updated 2026-08-10 preserved
Journal: journal/2026-08/log.md
```

Required frontmatter: `source`, `aliases` — kvault stamps `created`/`updated` automatically.

## What kvault tells you

kvault reports what it *decided*, not what you asked for. A note is emitted only when kvault
invented a value, deliberately changed nothing, half-failed, hid something, or fell back —
silence means the operation went exactly as asked. Notes render as indented lines under the
receipt (human mode) and as a `notes` array in `--json` and over MCP, each
`{code, text, level}` — a note's `why`/`next` ride in the JSON at every tier, and print in
human mode at `--explain`. Batch commands collapse
repeated notes by code (`{code, count, examples}`), so a 40-ancestor maintenance run emits
one line, not forty. The vocabulary is closed — 11 codes:

| Code | Contract |
|------|----------|
| `autofilled` | kvault invented a value you did not supply |
| `unchanged` | the operation ran and deliberately changed nothing |
| `partial` | part succeeded, part did not; manual repair needed |
| `created` | something came into existence as a side effect |
| `removed` | something was destroyed, with a count |
| `truncated` | you are not seeing everything that matched or exists |
| `skipped` | kvault could not read something and continued without it |
| `waited` | kvault blocked on, or broke, another process's lock |
| `guessed` | an input was unusable and a fallback was chosen |
| `propagate` | ancestor summaries are stale because of this operation |
| `structure` | this write changed the tree's shape in a way worth a look: a near-duplicate name, a parent past the child ceiling, or a new root category |

**Tiers.** `-q/--quiet` (receipt and warnings only) → normal → `--explain` (adds each note's
`why` and the exact next command) → `--trace` (adds lock waits and mechanics). Flags work
before or after the subcommand; `KVAULT_VERBOSITY=quiet|normal|explain|trace` sets the tier
for hooks and cron jobs (flags win; a typo silently means normal). `partial` notes survive
even `--quiet` — silencing a half-failure on request is a footgun.

**`--strict`** exits 3 when any warning-class note (`partial`, `skipped`, a broken lock) was
emitted — for CI and unattended runs. `check` rejects it: its exit codes are already a
contract, and its human output is frozen.

**Durable ops log.** Every successful mutating command (CLI and MCP) appends one row to
`.kvault/logs.db`: op, path, `did`, notes, changed/partial flags, duration, session.
`kvault log tail` shows what this KB's other agents and sessions did recently;
`KVAULT_SESSION` groups the commands of one logical task, `KVAULT_OPS_LOG=0` disables. A
failed append can never fail a write — the CLI surfaces the miss as a `skipped` note.

Full 0.13.0 detail — every new JSON field, per-command changes, frozen surfaces — is in the
[CHANGELOG](https://github.com/cimo-labs/kvault/blob/main/CHANGELOG.md).

## Structure that holds up under many agents

A KB rots in a specific way when several agents write to it: flat parents with dozens of
children, `infra/` beside `infrastructure/`, one new root category per sub-problem, and
directories with no summary that no surface can see. kvault guards the write and audits the
tree with the same rules, so the two never disagree.

**At write time** (`kvault write --create`, MCP `kvault_write_node`):

- A create that would add a root category is refused unless you pass `--new-root`.
- A create whose name has the same words as a sibling (`ai_overview` beside `ai_overviews`)
  is refused unless you pass `--allow-similar`. Near-duplicates (`pdp_prompts` beside
  `pdp_prompts_concord`, `infra` beside `infrastructure`) and a parent pushed past 10
  children are reported as a `structure` note, with the node to read next.
- Missing intermediate parents get a stub summary (a `created` note; it says "Placeholder"
  until you rewrite it) instead of becoming an invisible directory.

**In the audit** (`kvault check`), one bounded group per prefix, all warn-only:

| Prefix | Meaning | Fix |
|--------|---------|-----|
| `BRANCH:` (hard) | A parent — the root included — has more than 10 children | `kvault plan <path>` |
| `SUMMARY:` | A parent rollup is too short, misses children, has placeholder text, is too long, or accretes dated sections | Rewrite as a rollup; `too_long`/`stale_history` means **fold**, never split — chronology belongs in `journal/`, detail in `deep_context/` |
| `GHOST:` | A directory with no `_summary.md` — invisible to `tree`, `search`, and `check` | Write a summary, or list it in `.kvaultignore` if it is tooling |
| `SERIES:` | A parent whose children differ only by date or time words: a chronology written as nodes | `kvault plan` folds them: the dated nodes become `deep_context/` material of one current-state node you then write; new timeline entries go to `journal/` |
| `SIBLINGS:` | Two sibling names share their words, or one basename lives at two depths (buckets like `a_m` and tier × segment facets are exempt) | Merge, or nest one with `kvault move` |
| `LOOSE:` | A file outside the node convention: a legacy node file (Markdown with frontmatter, invisible to search), a supporting doc, or an artifact | Adopt it as a node (`plan` emits `kvault write <node> --create < file && git rm file`), move it into `<node>/deep_context/`, or ignore it |
| `JOURNAL:` | Files off `journal/YYYY-MM/log.md`, or a second history | Fold into the canonical log |
| `PENDING:` / `RETRACTED:` | Captured events never promoted; nodes citing retracted events | Promote or resolve; rewrite and re-link |

`.kvaultignore` at the KB root (one fnmatch pattern per line; a directory pattern covers
its subtree) declares the tooling directories and files that are not nodes and are fine.

**One path in.** The guards run only on `kvault write` and the MCP write tools. A node
written with a file tool or a shell redirect skips them and never reaches the ops log; on a
real KB, 56 of the 59 nodes created in a month arrived that way. Agents and pipelines create
nodes through kvault, and `check` is the backstop for the ones that did not.

**The engine**: `kvault plan` turns findings into an ordered worklist with the exact commands —
parents over the ceiling are clustered by leading word into new parents, each with a ready
`kvault move --batch` payload; then ghosts, sibling collisions, loose files, journal drift,
and summary rewrites. It never applies anything, and the judgment calls (are `aio` and
`ai_overview` one initiative?) come back as questions. `kvault move --batch --confirm` runs
a JSON list of `{from, to}` under one lock with one combined propagation list.

```text
$ kvault plan --limit 2
Plan for .: 9 items (showing 2; --limit 0 for all)
1. cluster  projects → projects/aio
     projects has 119 direct children (ceiling 10); 13 share the leading word 'aio'
     kvault move --batch --confirm --kb-root /home/me/my_kb <<'EOF'
     [{"from": "projects/aio_architecture", "to": "projects/aio/aio_architecture"}, …]
     EOF
     then: rewrite projects/aio as a rollup of its 13 children
2. ghost    infra
     no _summary.md — invisible to tree, search, and check
     …
Questions (answer from evidence; defer only when the evidence is not there):
  - projects: are any of these groups one initiative? aio, shopping, ai, pdp, concord, …
```

The cadence — every session, nightly, weekly, monthly — lives in
[`skills/kvault-maintenance/SKILL.md`](https://github.com/cimo-labs/kvault/blob/main/skills/kvault-maintenance/SKILL.md),
written so a cron or systemd job can load it alone.

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
| **Nodes** | `kvault read`, `kvault write` (stdin) `[--new-root] [--allow-similar]`, `kvault list`, `kvault delete`, `kvault move [--batch --dry-run]` |
| **Summaries** | `kvault read-summary`, `kvault write-summary` (stdin), `kvault update-summaries` (stdin JSON), `kvault ancestors` |
| **Quality** | `kvault validate`, `kvault check [--max-children N]`, `kvault plan [PATH] [--limit N]` |
| **Journal & artifacts** | `kvault journal`, `kvault artifact daily`, `kvault log tail`, `kvault log summary` |
| **Lifecycle** | `kvault init`, `kvault status` |

Agent-facing commands accept `--json` for machine-readable output and `--kb-root`
(auto-detected from cwd by default), before or after the subcommand — as do the output
flags `-q/--quiet`, `--explain`, `--trace`, and `--strict` (see
[What kvault tells you](#what-kvault-tells-you)).

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
`kvault_read_node`, `kvault_write_node`, summary/journal/validation tools, `kvault_log_tail`
for the ops log), plus a strict parent-summary workflow with stale-write detection. Results
carry the same `did`/`notes` decision reporting as `--json`, placed before the bulk payload.
The write tools (`kvault_write_node`, `kvault_write_entity`) accept
`ancestors="content"|"paths"`: `"paths"` (the default since 0.14.0) keeps `ancestor_paths` but
omits the full `ancestors[].current_content` payload, which can exceed 45,000 characters on a
mature KB; pass `"content"` to inline it. Set
`KVAULT_ALLOWED_ROOTS` to pin allowed roots on shared runtimes. Protocol details:
[ARCHITECTURE.md](https://github.com/cimo-labs/kvault/blob/main/ARCHITECTURE.md).

Every signal above is on the MCP surface too: `kvault_check` returns the `check` document,
`kvault_plan` the worklist, `kvault_move_entities` runs a batch, `kvault_write_node` takes
`new_root` and `allow_similar`, and `kvault_prepare_summary_update` returns child gists past
the ceiling (`children="content"` for full bodies). `kvault_validate_kb` is integrity only.

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
