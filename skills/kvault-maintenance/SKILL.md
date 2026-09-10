---
name: kvault-maintenance
description: Keep a kvault knowledge base (knowledgevault package) structurally healthy on a cadence. Use for per-session hygiene, nightly and weekly maintenance jobs, and one-time consolidations of a sprawling KB. Executes what `kvault plan` says; never invents topology.
---

# kvault maintenance — cadence and procedure

A KB rots in a specific way under multi-agent writes: flat parents with
dozens of children, near-duplicate siblings (`infra/` beside
`infrastructure/`), root categories minted one per sub-problem, directories
with no summary that no surface can see, and two journal layouts. `kvault
check` names each of these; `kvault plan` orders the fixes and emits the
commands. This skill is the procedure around those two commands. The plan
comes from kvault, not from you, so every runtime makes the same moves.

Requires knowledgevault 0.15 or later. The first line of every unattended
job is `kvault doctor`, so a runtime older than the skill text is visible in
the job log instead of silently missing the signals below.

## Vocabulary

`kvault check` prints one bounded group per prefix. Hard findings (exit 1)
are on the `[KB]` line; everything else is warn-only maintenance work.

| Prefix | Meaning | What to do |
|--------|---------|------------|
| `[KB]` PROPAGATE / LOG / WRITE / BRANCH | Stale parent, missing journal entry, missing frontmatter, parent over the child ceiling | Fix before other work; BRANCH → `kvault plan <path>` |
| `SUMMARY:` | A parent rollup is too short, misses children, has placeholder text, is too long, or accretes dated sections | `too_short`/`missing_child_coverage`/`placeholder_language`: rewrite as a rollup. `too_long`/`stale_history`: **fold, never split** — chronology goes to `journal/`, detail to `<node>/deep_context/` |
| `GHOST:` | A directory with no `_summary.md`: invisible to tree, search, and check | Write a summary (`kvault write <path> --create`) or list it in `.kvaultignore` if it is tooling |
| `SERIES:` | A parent whose children differ only by date/time words — a chronology written as nodes (daily cards) | Fold into one current-state node under that parent; the timeline goes to `journal/`. Never add another dated node to a series |
| `SIBLINGS:` | Two names under one parent share their words, or one basename lives at two depths | Same thing → merge and delete one. Subtopic → `kvault move`. Undecidable → leave it for the monthly review |
| `LOOSE:` | A file outside the node convention. `legacy_node_file` = Markdown with frontmatter that search cannot see; `supporting_doc`; `artifact` | Adopt legacy node files as nodes (`plan` emits the `git mv`); supporting docs into `<node>/deep_context/`; artifacts ignored |
| `JOURNAL:` | Files off the `journal/YYYY-MM/log.md` layout, or a second history | Fold into the canonical log with `kvault journal`, then remove |
| `PENDING:` / `RETRACTED:` | Captured events never promoted; nodes citing retracted events | Promote or resolve; rewrite and re-link |

Write-time notes you will see in the ops log (`kvault log tail`): a
`structure` note means a create was near a duplicate, pushed a parent past
the ceiling, or added a root; a `created` note means kvault stubbed a
missing intermediate parent — that stub says "Placeholder" until someone
rewrites it, and `check` keeps flagging it.

## Cadence

### Every session (any agent, before writing)

```bash
kvault check -q --kb-root "$KB"
```

- Act on `[KB]` lines first.
- Never create a root category. If a create is refused with
  `reason: new_root`, put the node under an existing root; if it truly
  needs a root, stop and ask the owner. A headless job never passes `--new-root`.
- If a create is refused with `reason: similar`, read the named sibling and
  update it. Pass `--allow-similar` only when you have read both and they
  are different things.
- Never write into `journal/` by hand; use `kvault journal` or `--reasoning`.
- Never create a node with a file tool. `kvault write --create` is the only path that runs
  the guards and lands in the ops log; on a real KB 56 of 59 new nodes in a month bypassed it.
- Rewrite any stub in `ancestor_paths` in the same session (call 2 of the
  2-call workflow covers it).

### Nightly (headless)

```bash
kvault doctor --kb-root "$KB"
kvault check --json --kb-root "$KB" > "$LOG/check.json"
```

1. `PENDING:` — promote each event that belongs in the tree
   (`kvault write <path> --event <id>`) or resolve it
   (`kvault events resolve <id> --outcome journal_only|duplicate|no_op|rejected --note "why"`).
2. `PROPAGATE:` — rewrite each stale parent with `kvault update-summaries`.
3. `SUMMARY: placeholder_language` on a path that ends in a stub you or
   another agent created today — rewrite it as a rollup now; do not leave
   stubs overnight twice.
4. Finish with `kvault check --strict`-equivalent discipline: log the exit
   code and the `did` line. **No restructuring at night.** Moves are weekly.

### Weekly (headless or supervised)

```bash
kvault doctor --kb-root "$KB"
kvault plan --json --limit 5 --kb-root "$KB" > "$LOG/plan.json"
```

1. Execute the top items in order. A `cluster` item carries the exact
   batch:

   ```bash
   kvault move --batch --confirm --kb-root "$KB" <<'EOF'
   [{"from": "projects/aio_reporting", "to": "projects/aio/aio_reporting"}, …]
   EOF
   ```

   One batch per item; the result's `ancestor_paths` is the full list of
   summaries to rewrite. Rewrite the new hub first (it is a parent now),
   then its parent, then the root.
2. `ghost` items: read what is inside, then either write the summary or add
   the path to `.kvaultignore`. Tooling directories (`scripts/`,
   `sources/`) are ignore entries, not nodes.
3. `siblings`, `loose`, `journal` items: follow the item's commands. For a
   `siblings` item, read both nodes; the summaries decide, not the names.
4. `summary` items last.
5. `kvault validate --kb-root "$KB"`, then journal the moves
   (`kvault journal`), then stop. **One structural batch per run** unless a
   person is watching. Stop early when `plan` returns `nothing to do`.

Rules a weekly job never breaks: fold, never split, for `too_long`; overflow
goes to `deep_context/`; never mint a root; never merge two nodes on name
alone. A merge needs evidence: for people, an exact identifier match
(email, phone); for topics, both summaries read and plainly describing one
thing. If the evidence is there, an agent decides; if it is not, the
question waits for the monthly review.

A `cluster` item's hub is named by the leading word (`projects/ai`). That
is a placeholder, not a name: read the `members` gists, decide what the
group is, and rename the hub in the `to` paths before running the batch.
Adjacent groups that are one initiative (`aio` and `ai_overview`) are
merged the same way, by pointing both groups' `to` paths at one hub.

### Monthly (the owner, or an agent with the owner's standing)

- Read the `questions` list from `kvault plan --json --limit 0`. Those are
  the calls the clusterer will not make (`people` vs `team`, `aio` vs
  `ai_overview`). Answer each from the summaries; only a question the
  summaries cannot settle goes to the owner. The next weekly run executes.
- Confirm `kvault doctor` reports the version this skill text describes.
- Prune `.kvaultignore` of paths that no longer exist.

## One-time consolidation of a sprawling KB

For a KB that has already rotted (dozens of flat children, split-brain
roots), do this once, supervised:

1. `kvault doctor`; upgrade if below 0.15.
2. Create `.kvaultignore` for tooling directories and files first, so
   `check` reports knowledge problems, not tooling.
3. `kvault plan --json --limit 0`. Read the `cluster` items and the
   `questions`. Merge adjacent clusters that are one initiative by editing
   the `to` paths before running the batch (the plan is input, not law).
4. Run one `move --batch --confirm` per cluster, rewrite the hub and chain,
   `validate`, commit. Repeat per cluster.
5. Then the root: if the root has more than ten categories, cluster it the
   same way. Root moves need `--new-root` only when the hub is new.
6. Remove the "split files over N lines into sub-files" rule from any
   maintenance prompt; it increases directory count. `too_long` means fold.

## Command reference

| Purpose | Command |
|---------|---------|
| Health, one document | `kvault check [--json] [--max-children N] [--summary-max-warnings N]` |
| Worklist | `kvault plan [PATH] [--json] [--limit N\|0]` |
| Batch move | `kvault move --batch [--dry-run] --confirm` (stdin: JSON list of `{from, to}`) |
| Guards on create | `kvault write <path> --create [--new-root] [--allow-similar]` |
| Ignore tooling | `.kvaultignore` at the KB root, one fnmatch pattern per line; a directory pattern covers its subtree |
| Runtime handshake | `kvault doctor`, `kvault --version` |
| What ran recently | `kvault log tail [--session ID]`, `kvault log summary` |

Over MCP the same signals are `kvault_check`, `kvault_plan`,
`kvault_move_entities`, and the `new_root` / `allow_similar` arguments on
`kvault_write_node`; `kvault_validate_kb` is integrity only.
