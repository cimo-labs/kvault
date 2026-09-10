# Changelog

All notable changes to `knowledgevault` are documented in this file.

## 0.15.0 - Unreleased

Structure guards, complete and bounded checks, and a maintenance engine.
Motivated by an audit of a 1,045-node KB driven by an MCP-only agent: 119
flat children under `projects/`, 23 root categories, `infra/` beside
`infrastructure/` beside `tech/infrastructure/`, 12 loose root files, two
journal layouts — while `validate` reported 0 errors and 0 warnings. Every
symptom was a legitimate `write --create` that nothing looked at; the
orientation tree prunes to 20 children so the agent never saw the sibling it
duplicated; deep creates minted summary-less parents no surface could see;
and `check` — the only command that knew about fan-out — was not exposed
over MCP. Reproduced on a synthetic fixture before any of this was written.

### Added

- **Write guards on `--create`** (CLI, MCP, Python). A create is refused
  when it would add a root category to a KB that already has roots
  (`--new-root` / `new_root=true` to do it deliberately; a bare KB's first
  roots are allowed and noted) or when a sibling has the same words
  (`ai_overview` beside `ai_overviews`; `--allow-similar` /
  `allow_similar=true` to override). Near-duplicate names (token prefix,
  4+ character prefix on single words, stemmed Jaccard ≥ 0.5), the same
  basename elsewhere in the tree, and a parent pushed past
  `MAX_DIRECT_CHILDREN` are reported as **`structure` notes** — the 11th
  note code, contract "this write changed the tree's shape in a way worth
  a look", `detail.kind` ∈ `similar`, `over_fanout`, `new_root`.
- **No more ghost parents.** A create or move whose path needs missing
  intermediate parents writes a self-flagging stub summary for each
  (`source: kvault-stub`, body says "Placeholder" so `check` keeps
  reporting `placeholder_language` until it is rewritten), emits a
  `created` note naming them, and lists them in `ancestor_paths` so the
  2-call workflow rewrites them. Before: `mkdir(parents=True)` and
  silence; the fixture root showed `[3 children]` while holding 22
  directories.
- **`check` sees the whole tree.** `BRANCH:` now includes the root (it
  was exempt). Four warn-only prefixes: `GHOST:` (directory with no
  summary), `SIBLINGS:` (near-duplicate sibling names, capped at 5 pairs
  per parent; the same basename at more than one depth), `LOOSE:` (files
  outside the node convention), `JOURNAL:` (files off
  `journal/YYYY-MM/log.md`, `journal/archive`, `archive/journal`).
  `--max-children N`. `check --json` gains `version`, `did`, a unified
  `findings` list (`{code, path, message, level, detail, fix}`, hard
  first), `structure_warnings`, `truncated` (hidden count per code), and
  `ignore_patterns`; every warn-class list is capped at 50 per code.
- **`SERIES:`** (warn-only): a parent whose children differ only by
  date/time words — a chronology written as nodes. Calibrated on a real
  KB with 40 daily "source boundary" cards under one parent; those cards
  are one `SERIES:` line now instead of 60 `SIBLINGS:` pairs, and members
  of one series never collide with each other.
- **`LOOSE:` knows what it found.** Markdown with frontmatter is a
  `legacy_node_file` (knowledge in the pre-directory layout, invisible to
  search and tree; the fix is to adopt it as a node and `plan` emits the
  `git mv`); plain Markdown is a `supporting_doc`; anything else an
  `artifact`. Files named `_*` are the KB's own internals and are skipped.
  On a real KB 61 of 70 loose files were legacy node files.
- **`.kvaultignore`** at the KB root: one fnmatch pattern per line
  against the KB-relative path; a directory pattern covers its subtree.
  Tooling directories and files (`scripts/`, `sources/`,
  `requirements.txt`) go here so `check`, `validate`, `tree`, and the
  guards treat them as not-nodes rather than as ghosts.
- **`kvault plan [PATH] [--limit N] [--json]`.** An ordered, bounded
  maintenance worklist derived from `check`: `cluster` items for every
  parent over the ceiling (children grouped by leading word, one new
  parent per group of 3+, with the exact `move --batch` command and a
  `moves` list), then `ghost`, `siblings`, `loose`, `journal`, `summary`
  items with commands. Judgment calls come back as `questions`. Never
  applies anything. On the audit fixture it turns 119 flat children into
  12 groups.
- **`kvault move --batch [--dry-run] --confirm`** reads a JSON list of
  `{from, to}` from stdin and runs it under one lock with one confirmation
  and one combined `ancestor_paths`; every move is validated before any
  runs, a mid-batch failure is a `partial` note naming what moved and what
  did not. `move --new-root` for single moves.
- **MCP**: `kvault_check` (the `check` document), `kvault_plan`,
  `kvault_move_entities`; `new_root` and `allow_similar` on
  `kvault_write_node` / `kvault_write_entity`; `new_root` on
  `kvault_move_entity`; `children` on `kvault_prepare_summary_update`.
- **`tree`** annotates directories no surface can see: `[N children, M
  total, +K ghost]`.
- **`kvault.core.check`**, **`kvault.core.structure`**,
  **`kvault.core.plan`**; `run_checks`, `build_plan`, and `Finding`
  exported from `kvault`.
- **`skills/kvault-maintenance/SKILL.md`**: per-session, nightly, weekly,
  and monthly procedures that execute `plan`, plus the one-time
  consolidation recipe. Cron and systemd jobs load it alone.

### Changed

- **Same-basename findings skip layouts that are not twins**: `a_m`/`n_z`
  alphabetical buckets under two branches, and one basename under sibling
  parents at the same depth (`customers/{key,standard}/industrial_oem`, a
  tier × segment facet). What remains carries each node's title so an
  agent can dismiss a false twin in one read (`people/family «Family»` vs
  `people/friends/family «Family Friends»`).
- **`plan` collapses**: one `siblings` item per parent (with the top pairs)
  and one `loose` item per directory (with adopt commands for legacy node
  files). The uncollapsed form on a real 564-node KB was 131 items.
- **`tree`** never counts a reserved parent's children as ghosts
  (`journal/` months are the canonical layout).
- **`prepare_summary_update` is bounded.** `children="auto"` (default)
  returns full child bodies up to `MAX_DIRECT_CHILDREN` and
  `{path, kind, title, gist, updated}` above it, with a `truncated` note
  and `children_mode` in the result; `"content"` / `"gist"` force either.
  The digest always covers full content. On the fixture the 119-child
  payload drops from 56 KB to 23 KB. Notes and counts now precede the
  `children` payload in the result.
- **`validate` reports `ghost_directory` warnings** (`valid: false`). A
  KB that keeps tooling directories inside the root needs a
  `.kvaultignore` before its next green `validate`.
- **`missing_child_coverage` messages are bounded** to eight names plus
  `(+N more)`; `details` gain `missing_count` and `child_count`. The 0.14
  line on a 118-child parent was 4,712 characters.
- `kvault.cli.check` is a thin renderer over `kvault.core.check`; it
  re-exports `check_propagation`, `check_journal`, `check_frontmatter`,
  `check_directory_size`, `_get_updated_date` for existing importers.
- `kvault move`'s positional arguments are optional when `--batch` is given.

### Frozen

- `check` human output stays tier-invariant and `--json` one document. The
  line-prefix vocabulary is now `[KB]`, `SUMMARY:`, `PENDING:`,
  `RETRACTED:`, `GHOST:`, `SERIES:`, `SIBLINGS:`, `LOOSE:`, `JOURNAL:`.

## 0.14.0 - 2026-09-06

Bounded outputs and a version handshake. Motivated by an audit of a 191-node
KB whose root summary had grown to 7,300 words / 29 dated "delta" sections
while `check` stayed green, whose agent-facing outputs were unbounded
(`status --json` 56 KB, `ancestors` 116 KB, `events list` 144 KB), and where
a deployed skill document described 0.13.0 against a 0.12.1 runtime with no
way for an agent to notice.

### Added

- **`kvault --version`**, a `version` key first in `status --json` (first
  after `success` in MCP `kvault_status`; also the first human line), and
  **`kvault doctor [--json]`**: version, python, install
  location, MCP extra, KB root and how it was resolved, root-summary size,
  event counts, ops-log writability, allowed-root error, `KVAULT_*` env. A
  missing KB is a finding; the exit code is always 0. In the J1 matrix.
- **`check` ceilings.** Two warn-only `SUMMARY:` codes: `too_long`
  (`min(2000, 1000 + 50*children + 5*descendants)` words;
  `--summary-max-words N` = hard ceiling, `0` = off) and `stale_history`
  (more than `--summary-max-dated-sections` (3) dated/delta H2–H6 headings;
  `0` = off). A parent whose only children are background dirs
  (`deep_context/`, see `core/conventions.py`) is budgeted as a leaf.
  Calibrated on the audited KB: flags exactly its seven accreted parents,
  passes every healthy one.
- **`capture` refuses shell-mangled bodies.** The residues left when
  agent-authored text goes through `echo "…"` under zsh (`,208.25`, `.00`,
  `/bin/zsh.00` or `zsh.00` depending on how the shell was invoked) return a
  `validation_error` naming the match and the quoted-heredoc fix;
  `--allow-suspicious` bypasses. A tripwire, not a
  guarantee — a bare `$250` vanishes without residue. Legacy import is
  tolerant.
- **`events retract <id> --reason TEXT [--superseded-by ID]`** (outcome
  `retracted`, allowed on pending and resolved events, prior resolution kept
  under `previous`; calling it again with `--superseded-by` amends the
  supersession) and **`RETRACTED:`** findings in `check` for nodes whose
  `source_refs` cite a retracted event — cleared by rewriting the node with
  `write --event <corrected capture>`, which now drops retracted refs (a
  `removed` note says which). `events list --status retracted`
  (`--status resolved` excludes retracted events).
- **Search filters and reporting**: `--kind {root,category,entity}`
  (repeatable), `--path PREFIX`, `--no-collapse`; `collapsed`,
  `collapsed_paths` and `collapsed_by` (ancestor → the descendant that
  justified dropping it) in the result and a `truncated` note naming what
  was collapsed. MCP `kvault_search(collapse, kind, path_prefix)`. A node
  whose only child is a background dir is `kind: entity`, so `--kind entity`
  keeps it.
- `status --root-summary` / MCP `include_root_summary`; `root_summary_chars`
  always. `ancestors --paths-only` and an `ancestor_paths` list always; MCP
  `kvault_get_parent_summaries(ancestors="paths"|"content")` and aliases.
  `events list --limit N` (default 50, `0` = all) and `--since YYYY-MM-DD`,
  with `total_matched` and a `truncated` note. `artifact daily --json`
  reports `content_chars`.
- `kvault/core/conventions.py`: `BACKGROUND_CHILD_DIRS = ("deep_context",)`
  — the one place two rules (summary ceilings, search collapse) agree on
  which child directories are supporting material rather than nodes.

### Changed

- **Search collapses propagated copies.** A root/category hit whose matched
  fields are only body/headings is dropped when a kept strict descendant
  (never a background child) scores at least half as much AND lands inside
  the returned page — evicting a rollup for a node the caller never sees
  would lose the fact, so such ancestors are reinstated; anchored matches
  (path/title/aliases) never collapse. Equal scores now prefer the deeper
  node (root is depth 0). Before: the tie-break sorted shallowest-first, so
  root outranked the canonical leaf for every propagated fact.
- **`read` defaults to `--parents none`**; MCP `kvault_read_node` and
  `kvault_read_entity` likewise (`parents="immediate"` restores the parent
  summary). Python `read_node`/`read_entity` defaults are unchanged.
- **`status --json` omits `root_summary`** unless requested.
- **`artifact daily --json` omits `content`** unless `--stdout`.
- **`events list` shows the newest 50 by default** (`--limit` must be ≥ 0;
  `0` = all).
- **MCP write tools default `ancestors="paths"`** (announced in 0.13.0).
- `check_events_promotable` judges by outcome, not status: any outcome other
  than `promoted` blocks `write --event`, so a retracted id fails fast without
  touching the tree.
- `__version__` is single-sourced in `kvault/_version.py`.

### Fixed

- The fallback version string in an uninstalled source tree (was 0.13.0,
  duplicated against `pyproject.toml`).

### Frozen

- `check` human output remains tier-invariant and `--json` remains one
  document (J1). The finding set is **not** frozen: the line-prefix
  vocabulary is now `[KB]`, `SUMMARY:`, `PENDING:`, `RETRACTED:`; adding a
  prefix is a minor-version change listed here.

## 0.13.0 - 2026-08-11

Work reporting. kvault now tells you what it decided, not just what you asked
for — on both the CLI and MCP surfaces. Before this release the interesting
decisions (invented frontmatter, no-op writes, half-failed event promotions,
truncated search results) were computed and then thrown away; the observability
database shipped in 0.5 had **no automatic writers at all**.

### Added

- **Decision notes on every operation.** A closed vocabulary of 10 note codes
  (`autofilled`, `unchanged`, `partial`, `created`, `removed`, `truncated`,
  `skipped`, `waited`, `guessed`, `propagate`), each with a one-sentence
  durable contract in `kvault/core/notes.py`. Notes ride *inside* the result:
  human mode renders them as indented lines under the receipt; `--json` and
  MCP carry them as a `notes` array (with `why`/`next` always included).
  Batch commands collapse per-item notes by code (`{code, count, examples}`)
  so a 40-ancestor maintenance run emits one line, not forty.
- **Output tiers**: `-q/--quiet`, `--explain` (adds the reasoning and the
  exact next command), `--trace` (adds lock waits and mechanics), plus a
  `KVAULT_VERBOSITY` env var for hooks and cron jobs. `partial` notes survive
  even `--quiet` — silencing a half-failure on request is a footgun.
- **`--strict`**: exit 3 when any warning-class note (`partial`, `skipped`,
  broken lock) was emitted. Rejected on `check`, whose exit codes are already
  a contract.
- **The durable ops log finally has writers.** Every successful mutating
  command (CLI and MCP) appends one row to a new `ops` table in
  `.kvault/logs.db`: op, path, `did`, notes, changed/partial flags, duration,
  UTC timestamp, session. Read it with the new **`kvault log tail`** (CLI) or
  **`kvault_log_tail`** (MCP). Appends are failure-proof and off the write
  path — a corrupt or unwritable log can never fail a KB write; the miss
  surfaces as a `skipped` note on both surfaces. Bounded: notes capped at 4 KB, table pruned
  at 5,000 rows. `KVAULT_OPS_LOG=0` disables it; `KVAULT_SESSION` correlates
  the several commands of one logical task into one session.
- New JSON fields (all additive): `changed`, `did`, `notes`, `ancestor_paths`,
  `partial`, `next`, `attempted`/`failed` (update-summaries), `nodes_deleted`/
  `files_deleted` + ancestors (delete), `nodes_moved`/`ancestors_source`/
  `ancestors_target` (move), `created` (write-summary), `total_matched`/
  `limit`/`budget` (search), and per-search-result `content_omitted_reason`
  (`content_max_chars` | `total_budget_exhausted` | `empty_node`) — which
  finally makes `content_truncated` interpretable.
- MCP: `ancestors="content"|"paths"` on the write tools — `"paths"` omits the
  full `ancestors[].current_content` payload, which can exceed 45,000
  characters (90% of a write result) on a mature KB. The default stays
  `"content"` in 0.13.x and flips to `"paths"` in 0.14.0. Docstrings on the
  write/search/delete/move tools now warn about invented provenance, the
  missing stale-children guard on `kvault_write_summary`, and the char budget
  that `parents != "none"` bypasses.
- `kvault init` now writes `.kvault/.gitignore` (`*.db*`, `lock/`) so a fresh
  KB inside a git repo never stages its runtime state.
- Lock contention is visible: waits ≥1s and stale-lock breaks are reported as
  `waited` notes at normal verbosity; sub-second waits surface only at
  `--trace`, and uncontended acquisitions stay silent.

### Changed

- **A no-op write no longer rewrites the file.** It prints `Unchanged:`
  instead of the false `Updated:`, reports `changed: false`, and leaves mtime
  untouched — the byte-identical rewrite was manufacturing spurious
  `PROPAGATE` warnings in `kvault check` and fake recency in search. The
  legacy `_meta.json` cleanup still runs on the no-op path, and a legacy node
  without frontmatter never takes it (the migrating write must run).
  `ancestors`/`propagation_required` deliberately keep their meaning ("do
  ancestor summaries exist to roll up"), so a stale chain from an earlier
  write stays visible on retry.
- **MCP write results are reordered**: `did`/`notes`/`changed`/
  `propagation_required`/`ancestor_paths` now precede `ancestors`. Key order
  is reading order for a model; the bulk payload was being read first.
- `kvault write` appends the ancestor paths to `Ancestors to update: N`, and
  suppresses the line entirely on a no-op.
- `kvault write-summary` prints `Created summary:` when the node did not
  exist — a typo'd path silently forks a new subtree, and now says so (with a
  `created` note). Passing `meta` reports the frontmatter keys it dropped.
- `kvault search --include-content` prints content in human mode at
  `--explain`; it was previously a silent no-op.
- `kvault tree` prints `(showing X of Y nodes — …)` when the view is pruned.
- `kvault log summary` honours `--kb-root` (it was the only KB-scoped command
  that didn't — it read a cwd-relative path) and the group-level `--json`,
  includes ops-table counts, and reports `session_id: null` on an empty
  database instead of a session id minted by the reader itself.
- MCP `kvault_log_phase` no longer mints a new session per call (a production
  DB had accumulated 146 rows across 61 one-row "sessions"); one server
  process is one session. sqlite errors return a structured `system_error`
  instead of escaping as an unhandled ToolError.
- `kvault validate` on a clean KB prints one line, not two.

### Fixed

- A non-UTF-8 `_summary.md` no longer crashes `kvault search` with a
  traceback (`UnicodeDecodeError` is a `ValueError`, not an `OSError`); it is
  excluded and reported as a `skipped` note naming the file.
- An unparseable `--date` on `kvault journal` was silently discarded; the
  entry still files under today, now with a `guessed` note saying so.
- The stale `__version__` fallback (0.11.3) in an uninstalled source tree.
- `tests/fixtures/sample_kb/.kvault/logs.db` is no longer committed to git.

### Frozen

- `kvault check` human output and exit codes are byte-identical at every
  verbosity tier — it feeds `UserPromptSubmit` hooks and automation that
  parses its stdout. New signal is `--json`-only. Pinned by test.
- In `--json` mode kvault emits exactly one JSON document and writes nothing
  to stderr, at every tier. Pinned by a test matrix over every JSON-capable
  command × every tier.

## 0.12.1 - 2026-07-27

Bug-fix release. Three defects found by an external audit of a live 441-entity
knowledge base on 2026-07-26. No migration required.

### Fixed

- **Parent-summary propagation could silently erase children (data loss).**
  `_children_digest()` hashed the *filtered* child list, so any child the node
  API could not read was quietly omitted. The stale-write guard exists precisely
  to reject a parent summary composed from incomplete child state, so it would
  **approve** a rewrite that deleted them. Reproduced against real data: with two
  date-named children on disk, `prepare_summary_update` reported `child_count: 0`
  and `write_parent_summary` accepted a summary reading "No months yet".
  It now raises `ChildDigestError` naming every unreadable path, surfaced through
  MCP as a structured error with a remediation hint. Comparison is normalized on
  both sides, so a mixed-case directory on a case-insensitive filesystem is not
  mistaken for a missing child.

- **Node component pattern rejected valid on-disk nodes.** `^[a-z][a-z0-9_]*$`
  made date-named directories (`journal/2026-03` — which kvault itself writes)
  and digit-leading names (`customers/3d_engineering`) unreachable through the
  node API. Widened to `^[a-z0-9][a-z0-9_-]*$`. The pattern had drifted into two
  copies; it is now defined once in `validation.py`. Applied per component after
  splitting on `/`, so `..` and absolute paths still cannot match.

- **`kvault check` exited 0 on a bad `--kb-root`.** A path that was missing, not
  a directory, or not a KB reported success with no output — indistinguishable
  from a clean knowledge base. Explicit bad paths are now a hard error. The
  silent exit when *no* `--kb-root` is given and auto-detection fails is
  deliberately preserved (it is a `UserPromptSubmit` hook firing in every
  directory) and is pinned by a test.

### Changed

- **Dev tooling is now version-bounded.** `black`, `ruff` and `mypy` were all
  unbounded (`>=23.0`, `>=0.1.0`, `>=1.0`), so CI installed whatever released
  most recently and drifted out from under the code. This caused two separate
  outages: black changed formatting and CI went red on 2026-07-20, unnoticed for
  a week; then ruff 0.16 promoted new rules to stable and `ruff check .` reported
  538 errors against unchanged source. Now `black>=26.1,<27` (marked
  `python_version>='3.10'`, since black 26.x dropped 3.9 while the test matrix
  still covers it), `ruff>=0.14,<0.15`, `mypy>=1.0,<2`.
- mypy now analyses against Python 3.10 rather than 3.9. `click` is an unpinned
  runtime dependency and 8.3+ uses `match` statements, so mypy targeting 3.9
  aborted while parsing click's own source before checking any of ours. 3.9
  support is unchanged and still enforced by ruff's `target-version = "py39"`
  and by the test matrix, which runs on 3.9.

## 0.12.0 - 2026-07-19

**0.12 is additive — no migration, no breaking changes.** The existing
`write` → `update-summaries` workflow is unchanged; capture is optional.

### Added

- **Capture journal**: `kvault capture` records a memory candidate verbatim as a
  pending event under `.kvault/events/` (idempotent by source + source_ref +
  content). `kvault write --event <id>` promotes it — stamps `journal:<id>` into
  the node's `source_refs` and resolves the event in one step; retrying a
  promoted event appends the target, so retries are idempotent. `kvault events
  list/show/resolve` cover triage and non-promotion outcomes; `kvault events
  import --format moss-capture` imports legacy OpenClaw inbox queues
  repeat-safely. `kvault check` flags events pending > 7 days (warn-only
  `PENDING:` findings).
- **Per-KB write lock** (`.kvault/lock/`): mutating operations from concurrent
  kvault processes now serialize, with acquire-time staleness detection and an
  atomic lock break. All file writes are atomic (temp file + rename).
- **Path safety layer** (`kvault/core/paths.py`): traversal, absolute-path, NUL,
  and symlink-component rejection shared by CLI, MCP, and legacy storage.
- `kvault validate` reports `malformed_frontmatter` (such nodes were previously
  invisible to entity scans); `parse_frontmatter_strict` rejects unclosed
  blocks, invalid YAML, duplicate keys, and non-mapping payloads.

### Changed

- `kvault delete` and `kvault move` in `--json` mode require `--confirm`
  (structured `confirmation_required` error otherwise); interactive use keeps
  the y/N prompt. `move` previously had no confirmation in any mode.
- `build_frontmatter` uses `yaml.safe_dump` and requires a mapping.
- Tolerant frontmatter reads now degrade non-mapping payloads to
  no-frontmatter instead of returning a non-dict to callers.

### Fixed

- **`kvault delete` with an empty path could delete the entire KB root**, and
  `.kvault` itself was deletable. Deletion now requires a real semantic node.
- `move` no longer allows moving a node into its own subtree.

## 0.11.3 - 2026-06-14

### Fixed

- **`kvault validate` no longer false-flags filled entities as stubs.** The
  `incomplete_entity` check matched the bare substring `"TBD"` anywhere in an entity
  body, so a fully-populated entity with one real field like `Lead time: TBD` was
  reported as placeholder content. It now flags an entity only when its body is empty
  or consists *entirely* of placeholder lines (`TBD`, `Context: TBD`, `TODO`, …),
  matched whole-line rather than as a substring.

## 0.11.2 - 2026-06-09

### Added

- **Agent skill** (`skills/kvault/SKILL.md`): the orient → research → write → propagate
  workflow and maintenance playbook in the portable `SKILL.md` agent-skills format, usable
  by any skills-aware agent runtime (Claude Code, OpenClaw, etc.). README documents
  per-tool installs.

### Changed

- **README overhauled**: leads with real annotated `kvault tree` output, motivates the
  parent-summaries-as-index design, adds the maintenance loop; comparison table removed,
  import tutorial moved to `docs/importing-data.md`, MCP section condensed.

## 0.11.1 - 2026-06-09

### Added

- **Periodic Maintenance guidance in generated `AGENTS.md`**: `kvault init` now ships a
  maintenance playbook with deterministic refactor triggers driven by `kvault tree`
  annotations (child counts, subtree recency) and `kvault check` summary-quality warnings —
  split fat branches, review stale branches, rewrite flagged rollups, merge duplicates.
  Reinforces search-before-create discipline.

## 0.11.0 - 2026-06-09

### Added

- **Annotated tree outline**: `kvault tree` now prints a compact annotated outline — node
  titles (when they differ from the slug), `[N children, M total]` counts on branches, and
  `~date` most-recent-activity markers (max frontmatter `updated` across each subtree).
- **Explicit truncation markers**: anything pruned by `--depth` or `--max-children` is called
  out in place (`…M nodes below (deepest activity ~DATE)`, `…K more children (M nodes) elided`)
  so a partial view can never silently hide nodes. Counts and recency are always computed from
  the full walk, even for pruned subtrees.
- **Tree options**: `kvault tree [PATH]` accepts a subtree start path, `--max-children`
  (default 20), and `--gist` (one-line summary excerpt per node).
- **MCP `kvault_tree`**: the outline over MCP (params: `path`, `depth`, `max_children`,
  `gist`, `format: text|json`). Text format is roughly 3-4x cheaper in tokens than
  `kvault_list_nodes(recursive=true)` while carrying more information.
- **Python API**: `build_outline(...)`, `render_outline_text(...)`, and `outline_counts(...)`
  in `kvault.core.operations`.

### Changed

- **`kvault tree` depth default**: now unlimited (was 3). The old default silently hid most
  nodes in deep KBs; prefer explicit `--depth` plus the new truncation markers.
- **`kvault tree --json`**: returns a structured outline envelope (`total_nodes`,
  `shown_nodes`, nested `outline` with counts and `truncated` markers) instead of a wrapped
  text string.
- **`kvault status` hierarchy**: uses the annotated outline (depth 2) instead of the bare
  directory tree.
- **No-op writes preserve dates**: `kvault write` / `kvault_write_node` with an identical
  body and meta no longer refreshes `updated` (or `created`), so bulk re-writes don't flatten
  the recency signal.

### Removed

- **`build_hierarchy_tree`**: replaced by `build_outline` + `render_outline_text`.
- **Tree output of non-node directories**: directories without `_summary.md` (e.g. raw
  `journal/` subfolders) no longer appear in `kvault tree`; the `✓` summary markers are gone
  since every listed node has a summary by definition.

## 0.10.0 - 2026-05-03

### Added

- **Strict MCP parent-summary updates**: Added `kvault_prepare_summary_update` and
  `kvault_write_parent_summary` so MCP clients can read all direct child summaries before writing
  a parent rollup.
- **Stateless child-summary digests**: Parent summary writes can now reject stale MCP update
  attempts when a direct child summary changed after preparation.
- **Hierarchy pressure hints**: Strict prepare calls return an advisory `hierarchy_hint` when a
  parent has more than 10 direct children.

### Compatibility

- Existing summary tools, including `kvault_update_summaries`, `kvault_write_summary`,
  `kvault_get_parent_summaries`, `kvault_get_ancestors`, and `kvault_propagate_all`, remain
  available unchanged.

## 0.9.0 - 2026-05-03

### Added

- **Node-first interface**: `kvault read`, `kvault write`, and `kvault list` now operate on any
  visible `_summary.md` node, including root, parent branches, and leaf entities.
- **Structured lexical search**: Added `kvault search`, Python `search_nodes(...)`, and MCP
  `kvault_search` for node-aware discovery across path, title, aliases, headings, and body text.
- **Node MCP tools**: Added `kvault_read_node`, `kvault_write_node`, and `kvault_list_nodes`.

### Changed

- **Read context**: Node reads return the full requested node plus immediate parent context by
  default, with options for no parents or full ancestry.
- **Write behavior**: Node writes preserve existing frontmatter when stdin omits frontmatter, and
  still return ancestor summaries for propagation.
- **CI dependencies**: Development validation now installs `[dev,mcp]` only.
- **Docs and fixtures**: Public examples and test fixtures now use neutral sample data; maintainer
  notes moved to a provider-neutral filename.
- **Packaging metadata**: Release builds use SPDX-style license metadata.

### Removed

- **Optional web UI**: Removed `kvault ui`, the `[ui]` extra, and the Starlette/Jinja/htmx UI
  package to keep kvault focused on files, CLI, MCP, and Python APIs.

### Compatibility

- Existing entity and summary CLI/MCP names remain available as compatibility aliases.

## 0.8.0 - 2026-02-27

### Added

- **Read-only web UI** (`kvault ui`): Browse your knowledge base in a local web browser. Starlette + htmx + Jinja2 — no npm/node required. Optional install: `pip install 'knowledgevault[ui]'`.
  - Dashboard with entity count, health status, and tree preview
  - Two-column tree browser with lazy-loaded navigation (htmx)
  - Entity detail with server-side Markdown rendering (mistune)
  - Live search with 300ms debounce (htmx)
  - Breadcrumb navigation, category summaries
  - Pico CSS (CDN) for responsive classless styling; htmx vendored (~50KB, no CDN dependency for JS)
  - All routes read-only with path traversal defense-in-depth
- **`[ui]` optional dependency group**: `starlette`, `uvicorn`, `jinja2`, `mistune`
- **Summary-quality audit**: `kvault check` now emits warn-only `SUMMARY:` findings for
  parent summaries that are too short, omit immediate child coverage, or contain placeholder
  redirect language.
- **Thin MCP compatibility server**: Restored `[mcp]` extra and `kvault-mcp` entry point with
  root-bound tools backed by `kvault.core.operations`.
- **`httpx`** added to `[dev]` dependencies for Starlette test client
- **New tests**: `test_ui.py` (integration), `test_ui_search.py` (unit)

### Changed

- **CLI option ordering**: Agent-facing commands accept `--json` and `--kb-root` before or after
  the subcommand.
- **Artifact CLI**: `kvault artifact daily` now honors top-level `--kb-root` and supports JSON
  output for machine-readable artifact generation.
- **Entity path validation**: Removed the old max-depth cap while keeping safe lowercase
  component validation and root-escape protection.
- **Init templates**: Freshly initialized KBs now start with parent summaries that satisfy the
  summary-quality audit.
- **Root pinning**: `KVAULT_ALLOWED_ROOTS` is enforced at CLI and MCP boundaries.
- **Public API**: `ObservabilityLogger` is exported from top-level `kvault`, and `__version__`
  is read from package metadata when installed.

### Fixed

- **Starlette/Jinja compatibility**: `TemplateResponse` calls now work across current and older
  Starlette signatures.
- **CI workflow**: Installs `[dev,ui,mcp]` so UI and MCP compatibility tests run where supported.

## 0.7.1 - 2026-02-27

### Changed

- **Multi-tool compatibility**: Renamed the tool-specific agent template to `AGENTS.md`. `kvault init` now generates `AGENTS.md`. Template language generalized for AI coding agents.
- **README**: Added multi-tool quickstart tips table; integrity hook section now shows CLI command first with generic tool language.

### Fixed

- **Click 8.2 compatibility**: Removed `mix_stderr` kwarg from `CliRunner()` in tests (removed in Click 8.2).

## 0.7.0 - 2026-02-25

### Added

- **CLI-first architecture**: All KB operations now available as CLI commands (`kvault read`, `kvault write`, `kvault list`, `kvault delete`, `kvault move`, `kvault read-summary`, `kvault write-summary`, `kvault update-summaries`, `kvault ancestors`, `kvault journal`, `kvault validate`, `kvault status`, `kvault tree`).
- **Shared operations layer** (`kvault/core/operations.py`): Stateless functions backing all CLI commands. All functions take `kg_root: Path` as first argument.
- **Validation moved to core** (`kvault/core/validation.py`): Business rules used by CLI and operations layer.
- **CLI helpers** (`kvault/cli/_helpers.py`): KB root auto-detection, stdin reading, JSON output.
- **Group-level options**: `--kb-root` and `--json` flags on the top-level `kvault` group, inherited by all subcommands.
- **Source tracking**: CLI uses `default_source="auto:cli"` to identify write origins.
- **New tests**: `test_operations.py` (26 tests), `test_cli_commands.py` (28 tests), `test_cli_write_workflow.py` (2 tests).

### Changed

- **`kvault init` output**: Changed "Next steps" from MCP config JSON to CLI usage instructions.
- **Templates**: The generated agent instructions were rewritten for CLI workflow (shell commands, not MCP tool calls). (Renamed to `AGENTS.md` in 0.7.1.)
- **Documentation**: README, generated agent instructions, and CHANGELOG updated for CLI-first architecture.

### Removed

- **MCP server**: The `kvault/mcp/` package, `kvault-mcp` entry point, and `[mcp]` install extra have been removed. CLI commands are now the sole interface. Install with `pip install knowledgevault` (no extras needed).

## 0.6.3 - 2026-02-17

### Security

- Added optional KB-root pinning guard:
  - Init now enforces `KVAULT_ALLOWED_ROOTS` when configured.
  - Returns structured `validation_error` if requested `kg_root` is outside allowed roots.
- Status now reports configured `allowed_kg_roots` when root pinning is enabled.

### Compatibility & Docs

- Aligned README workflow language with staged flow (research -> decide -> execute -> propagate -> log -> rebuild/validate).
- Added packaging excludes for Python cache artifacts (`__pycache__`, `*.py[cod]`) across wheel + sdist (`pyproject.toml`, `MANIFEST.in`) to keep releases clean.

### Testing

- Added root guard coverage.

## 0.6.2 - 2026-02-17

### Added

- Added shared research primitives in `kvault.core.research`:
  - `EntityResearcher`
  - `ResearchCandidate`
- Added `kvault log summary` CLI command for observability session summaries.
- Added tests for research primitives and log CLI behavior.

### Changed

- `ObservabilityLogger.get_session_summary()` now defaults to the latest logged session.
- Added `ObservabilityLogger.list_sessions()` helper.
- Refactored downstream adapter integration to reuse `kvault.core.research.EntityResearcher` instead of local duplicate logic.
- Reconciled architecture and maintainer docs with current module layout.

## 0.6.1 - 2026-02-17

### Security

- Hardened path handling to prevent writes or moves that escape the configured KB root.
  - Summary writes now reject paths outside KB root.
  - Entity moves now validate source and target paths and enforce root containment for both.

### Testing

- Added path traversal regression coverage in `tests/test_e2e_workflows.py`:
  - summary write escape attempts
  - move source/target traversal attempts
  - batch summary update escape attempts
