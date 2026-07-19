# kvault Architecture

Canonical architecture for `knowledgevault` 0.12.

## Design

kvault is a provider-neutral memory substrate for long-lived agents. It separates immutable source
evidence from curated current state and derived navigation:

```text
host agent runtime
  → CLI or root-bound MCP
  → capture / read / reconciliation services
  → policy + revision + integrity enforcement
  → transactional filesystem storage
      ├── immutable temporal events
      ├── curated semantic nodes
      └── derived parent-summary indexes
```

The agent performs semantic interpretation. kvault supplies deterministic intake, concurrency,
policy, storage, and validation. It never calls a model provider.

## Storage model

```text
knowledge-base/
├── _summary.md
├── people/.../_summary.md
├── projects/.../_summary.md
├── journal/
│   ├── events/YYYY/MM/<event-id>.md
│   ├── reconciliations/YYYY/MM/<reconciliation-id>/
│   │   ├── plan.md
│   │   └── result.md
│   └── YYYY-MM/log.md                 # preserved legacy journal
└── .kvault/
    ├── schema.json
    ├── policy.yaml
    ├── transactions/<id>/
    └── logs.db                        # noncanonical observability
```

### Temporal events

An event is immutable evidence containing:

- schema version and sortable event ID
- UTC capture time and optional occurrence time
- source and optional stable source reference
- exact candidate text and its SHA-256 digest
- tags and sensitivity (`public`, `personal`, `sensitive`, or `restricted`)

`source + source_ref` is the idempotency key when a stable reference exists. Repeating that key
with the same digest returns the existing event. Different content under the same key is a
`source_ref_conflict`.

Event state is derived from immutable event and reconciliation records. Operational states are
`pending`, `reconciling`, `needs_review`, and `resolved`. Terminal outcomes are `applied`,
`journal_only`, `duplicate`, `no_op`, and `legacy_archived_unknown`. A failed attempt stays pending
and retryable.

### Semantic nodes and summaries

Every visible node is a directory containing `_summary.md` with mapping-shaped YAML frontmatter and
a Markdown body. A node may be a leaf or a parent. Every visible ancestor must itself be a node;
orphan directories are invalid.

Node metadata unions `journal:<event-id>` entries into `source_refs`, preserving existing sources
and aliases. Parent metadata stores a digest of its immediate child summaries. Propagation checks
compare exact content digests rather than dates or filesystem mtimes.

Parent summaries are comprehensive current-state rollups. Temporal activity belongs in the event
ledger, not in continuously appended root-summary history.

## Public workflow

### Capture and inspect

- `kvault capture`
- `kvault events list`
- `kvault events show`
- `kvault events import`

Capture is atomic and precedes semantic navigation. The read commands `tree`, `search`, `read`, and
`list` remain available for bounded research.

### Reconcile

- `kvault reconcile prepare`
- `kvault reconcile apply`
- `kvault reconcile approve`
- `kvault reconcile status`
- `kvault reconcile recover`

`prepare` returns source events, active policy, bounded orientation metadata, and current node
revisions. The host agent supplies a versioned JSON plan containing event decisions, complete
proposed content, expected revisions, and every affected ancestor summary.

Core validation derives the required ancestor set. Missing, unrelated, or stale summary changes
are rejected. A move includes both the old and new chains. Every event in the request receives a
decision, including journal-only and no-op outcomes.

### Policy

`.kvault/policy.yaml` is explicit, local, and versioned with the KB. The default policy:

- auto-resolves duplicate, no-op, and journal-only decisions
- permits a create only beneath an existing valid parent
- permits additive updates only with matching revisions and union-only metadata
- permits derived ancestor updates only against matching parent and child revisions
- requires review for merge, move, delete, restructuring, conflicting replacement, and sensitive
  or restricted events

Approval records the actor and rechecks every expected revision. A stale approved plan must be
prepared again; approval never weakens integrity checks.

### Transaction protocol

All mutations use one per-KB cross-platform lock and this order:

1. Persist the immutable reconciliation plan.
2. Recheck expected revisions under the lock.
3. Stage complete resulting files in `.kvault/transactions/<id>/`.
4. Validate paths, policy, hierarchy, frontmatter, source references, and ancestor coverage.
5. Replace leaves followed by ancestors using same-directory atomic replacement and fsync.
6. Run the unified integrity audit.
7. Roll back from backups on failure.
8. Persist the immutable result and release the lock.

`reconcile recover` inspects interrupted state and deterministically completes finalization or
rollback. Unknown locks and transactions are surfaced, never silently discarded.

One coordinator is the only writer. Parallel workers may perform read-only navigation and return
proposals. This parallelizes interpretation without racing leaf files or convergent ancestors.

## Safety and integrity

All supported mutation paths pass through one symlink-aware resolver. It rejects traversal, paths
outside the resolved KB root, root deletion, hidden/internal targets, journal deletion, and
non-node destructive targets.

One core integrity audit is consumed identically by CLI and MCP. It checks:

- strict mapping-shaped YAML frontmatter
- safe and valid visible-node paths
- complete ancestor-node hierarchy
- immutable event and reconciliation references
- expected source references
- exact child-summary digests
- incomplete or placeholder semantic content

Structured command failures use nonzero exit codes. JSON output does not change failure semantics.

`SimpleStorage` is a deprecated, path-contained compatibility adapter and emits
`DeprecationWarning`. Legacy `_meta.json` is read-only compatibility data; no 0.12 operation
creates it. It is not an agent-facing mutation surface.

## Schema and migration

`.kvault/schema.json` records the on-disk protocol version. A pre-0.12 KB remains readable, but
mutations return `migration_required` until `kvault migrate` succeeds.

Migration is transactional and repeat-safe. It creates schema and default policy state, validates
all existing nodes, and backfills child digests without altering semantic bodies, historical dates,
or legacy monthly journals. `migrate --dry-run` reports the proposed changes.

`events import --format moss-capture` imports legacy OpenClaw inbox data. Open records become
pending events; processed records become `legacy_archived_unknown`, preserving evidence without
claiming semantic promotion.

Legacy direct mutation commands are non-mutating compatibility stubs. They return
`workflow_required` and direct callers to capture and reconcile.

## Interfaces and ownership

- **CLI:** primary automation interface; agent-facing commands accept an absolute `--kb-root` and
  machine-readable `--json`.
- **Python:** typed event, policy, reconciliation, migration, and integrity models plus stateless
  service functions.
- **MCP:** root-bound parity for capture, read, reconciliation, migration, and validation; no direct
  mutation tools.
- **Host runtime:** owns model calls, scheduling, inbox discovery, persona, retention decisions,
  approval identity, and read-only subagent orchestration.

The portable skill is canonical at `skills/kvault/` and installed in distributions under
`share/knowledgevault/skills/kvault`. `kvault skill path` discovers it and `kvault skill install`
copies the complete folder to an agent runtime.

## Release invariants

- A published GitHub release is the only PyPI publication trigger.
- The release tag must equal `v` plus the `pyproject.toml` version.
- CI installs and tests the built wheel, validates the packaged skill, and never publishes.
- CLI, Python, MCP, templates, and skill documentation describe the same protocol.
