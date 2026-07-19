# kvault

**Persistent, structured memory for AI agents — plain Markdown, a CLI, and no hosted service.**

```bash
pip install knowledgevault
```

kvault records new information in an immutable temporal journal before an agent reconciles it into
the semantic knowledge tree. That gives long-lived agents both an evidence trail and a concise,
current view of people, projects, decisions, and notes.

## The model

kvault keeps three distinct layers:

1. **Temporal events** preserve what arrived, its source, and when it happened.
2. **Semantic nodes** represent the best current durable state.
3. **Parent summaries** are derived navigation indexes over their descendants.

Each semantic node is a directory containing `_summary.md` with YAML frontmatter. Parent summaries
are comprehensive rollups, so an agent can navigate top-down instead of loading the whole vault.

```text
$ kvault --kb-root /absolute/path/to/my_kb tree --depth 2 --max-children 20
. « Knowledge Base » [3 children, 11 total] ~2026-07-19
  notes [1 children, 1 total] ~2026-04-11
    reading_list ~2026-04-11
  people [2 children, 5 total] ~2026-07-19
    contacts [2 children, 2 total] ~2026-07-19
    friends [1 children, 1 total] ~2026-03-14
  projects [2 children, 2 total] ~2026-06-07
    launch_plan « Launch Plan — v2 » ~2026-06-07
    website_redesign ~2026-05-28
```

Anything omitted by `--depth` or `--max-children` appears as an explicit `…` marker.

## Quickstart

```bash
pip install knowledgevault
kvault init ./my_kb --name "Your Name"
KB_ROOT="$(cd ./my_kb && pwd)"
```

Capture a memory candidate before navigating or writing:

```bash
kvault --kb-root "$KB_ROOT" --json capture \
  --source conversation \
  --source-ref message:123 \
  --occurred-at 2026-07-19T12:34:00Z \
  --sensitivity personal <<'EOF'
Sarah moved the launch review to Thursday and owns the revised agenda.
EOF
```

The result contains an immutable event ID. Give that ID to an agent and ask it to reconcile the
event using the generated `AGENTS.md` instructions:

```bash
kvault --kb-root "$KB_ROOT" --json reconcile prepare EVENT_ID
# The agent navigates with tree/search/read and creates plan.json.
kvault --kb-root "$KB_ROOT" --json reconcile apply < plan.json
kvault --kb-root "$KB_ROOT" --json validate
```

The agent must inspect both the process exit status and structured `success`/error fields. A failed
apply leaves the event pending and retryable rather than claiming that memory was incorporated.

## Safe agent workflow

The normal lifecycle is:

```text
capture → pending event → bounded navigation → proposal → policy gate
        → serialized semantic update → bottom-up summaries → integrity check → outcome
```

Outcomes include applied, journal-only, duplicate, no-op, and needs-review. Not every event needs a
semantic node. Merge, move, delete, destructive replacement, structural changes, and sensitive
events require review under the default policy.

For batches, multiple workers may inspect separate branches and return read-only proposals. One
coordinator deduplicates those proposals and remains the only writer. Shared ancestors and root are
updated once after all leaf decisions are known.

## Portable agent skill

The wheel and source distribution include the complete portable skill. Locate or install it without
copying files from a repository checkout:

```bash
kvault skill path
kvault skill install ~/.openclaw/workspace/skills/kvault
```

The skill contains the capture-first workflow plus an on-demand reference for safe parallel
analysis. It never authorizes direct Markdown edits or automatic Git commit/push.

## Navigation and review

Always use an absolute root for automation and delegation:

```bash
kvault --kb-root "$KB_ROOT" --json tree --depth 2 --max-children 20
kvault --kb-root "$KB_ROOT" --json search "Sarah launch"
kvault --kb-root "$KB_ROOT" --json tree projects --depth 2 --gist
kvault --kb-root "$KB_ROOT" --json read projects/launch_plan
```

Inspect pending work and reconciliation history with:

```bash
kvault --kb-root "$KB_ROOT" --json events list
kvault --kb-root "$KB_ROOT" --json events show EVENT_ID
kvault --kb-root "$KB_ROOT" --json reconcile status RECONCILIATION_ID
```

If policy requests review, an authorized actor can approve the persisted plan. Revisions are checked
again before application, so a stale approval cannot overwrite newer state:

```bash
kvault --kb-root "$KB_ROOT" --json reconcile approve RECONCILIATION_ID --actor human-id
```

Use `reconcile recover` for interrupted transactions. Never remove lock or transaction files by
hand.

## Validation and maintenance

```bash
kvault --kb-root "$KB_ROOT" --json validate
kvault --kb-root "$KB_ROOT" --json check
```

Validation covers path safety, frontmatter shape, visible-node hierarchy, source references, and
exact parent child-digests. Maintenance signals are prompts for scoped review, not permission to
restructure unrelated branches.

Parent summaries should describe current state. Dated activity and maintenance history belong in
event and reconciliation records rather than an ever-growing root summary.

## Upgrading from 0.11 or earlier

Existing vaults remain readable under 0.12 but mutations require an explicit migration:

```bash
kvault --kb-root "$KB_ROOT" --json migrate --dry-run
kvault --kb-root "$KB_ROOT" --json migrate
```

Migration creates schema and default-policy state and backfills derived child digests without
rewriting semantic bodies or legacy monthly journals. Review the dry-run and back up or commit the
vault according to its existing policy before applying. See
[`docs/migrating-0.12.md`](docs/migrating-0.12.md) for compatibility and Moss/OpenClaw cutover.
The deeper [`docs/openclaw-integration.md`](docs/openclaw-integration.md) guide maps long-running
OpenClaw memory tiers, the established Moss queue, and safe scheduled subagent reconciliation.

To bring older inbox captures into the event ledger, use `events import`; legacy processed records
are preserved without falsely claiming that they reached the semantic tree.

## Importing source data

Keep raw chat, email, calendar, or note exports separate from curated nodes, then capture candidates
in bounded batches. See [`docs/importing-data.md`](docs/importing-data.md).

## CLI reference

| Category | Commands |
|---|---|
| **Capture and intake** | `capture`, `events list`, `events show`, `events import` |
| **Orient and read** | `tree`, `search`, `read`, `list` |
| **Reconcile** | `reconcile prepare`, `reconcile apply`, `reconcile approve`, `reconcile status`, `reconcile recover` |
| **Integrity** | `validate`, `check`, `status` |
| **Lifecycle** | `init`, `migrate` |
| **Skill** | `skill path`, `skill install` |

Legacy direct mutation commands no longer write in 0.12. They return `workflow_required` and direct
the caller to capture and reconcile. Structured failures use nonzero process exit codes.

## MCP server

The CLI is the primary interface. MCP-native clients can install the optional Python 3.10+ server:

```bash
pip install "knowledgevault[mcp]"
kvault-mcp --kb-root /absolute/path/to/my_kb
```

The root-bound server exposes the same capture, read, reconciliation, migration, and validation
workflow. Direct mutation tools are not exposed. Set `KVAULT_ALLOWED_ROOTS` to pin permitted roots
on shared runtimes. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for protocol boundaries.

## Plain files, portable tools

Canonical knowledge and evidence are Markdown with YAML frontmatter. `.kvault` contains derived
schema, policy, transaction, and observability state; no database is required for canonical
knowledge.

| Want to… | Use |
|---|---|
| Exact text search | `rg -n "phrase" ./my_kb` for read-only investigation |
| Visual browsing | Open semantic Markdown in Obsidian or Logseq |
| CI validation | Run `kvault validate` and `kvault check` |
| Bulk export | Copy the Markdown event and node files |

## Development

```bash
pip install -e ".[dev,mcp]"
pytest -q
ruff check .
black --check kvault/ tests/
mypy kvault/ --ignore-missing-imports
python -m build
```

## License

MIT
