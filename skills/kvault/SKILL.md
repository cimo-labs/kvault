---
name: kvault
description: Manage durable agent memory with the knowledgevault CLI. Use when capturing new facts, reconciling journal events into semantic knowledge, navigating or searching a kvault tree, importing memory candidates, reviewing pending events, or maintaining and validating a knowledge base. Covers journal-first intake, bounded navigation, policy-gated reconciliation, and safe read-only subagent workflows.
---

# kvault

Store durable knowledge as source-backed Markdown nodes. Treat the temporal event journal as
evidence, semantic nodes as current state, and parent summaries as derived navigation indexes.

Set an absolute root for every command. Read that KB's `AGENTS.md` before substantial work.

```bash
KB_ROOT="/absolute/path/to/knowledge-base"
```

## Capture first

Capture each admitted memory candidate before navigating or editing the semantic tree:

```bash
kvault --kb-root "$KB_ROOT" --json capture \
  --source "conversation" \
  --source-ref "message:stable-id" \
  --occurred-at "2026-07-19T12:34:00Z" \
  --sensitivity personal < candidate.md
```

Retain the returned event ID. A repeated source reference and content hash returns the existing
event. A reused source reference with different content is a conflict, not a new event.

One source record normally becomes one event, even when it contains several related assertions.
Preserve the source wording. Do not invent an occurrence time, stable reference, identity, absolute
date, or sensitivity classification: omit optional metadata that is unknown and follow the owning
KB's rules for sensitivity. Ambiguity blocks semantic promotion, not capture. For example, capture
"Thursday" verbatim, then resolve its date from authoritative context or leave the event pending.

When capture returns an existing event, run `events show` and inspect its lifecycle. Reconcile it
only when it is still `pending`; do not create a second event or re-resolve a terminal one.

Always check both the process exit status and the JSON `success`, `error`, and `errors` fields.
Never infer success from parseable JSON alone.

## Orient within a budget

Start with a bounded root view, then drill into relevant branches. Treat every `…` marker as an
explicit prompt to inspect that branch when it may contain the target.

```bash
kvault --kb-root "$KB_ROOT" --json tree --depth 2 --max-children 20
kvault --kb-root "$KB_ROOT" --json search "name or topic"
kvault --kb-root "$KB_ROOT" --json tree people/contacts --depth 2 --gist
kvault --kb-root "$KB_ROOT" --json read people/contacts/example
```

Search before proposing a create. Update an existing identity when evidence is sufficient;
otherwise leave the event pending for clarification. Use `journal_only` only when the evidence is
durable but intentionally does not belong in semantic current state. Use `duplicate` when another
identified event already carries the evidence, and `no_op` when the candidate is already fully
represented. Never merge on fuzzy similarity.

## Reconcile

After research identifies the targets, prepare against every target and affected ancestor revision,
then provide one complete plan on stdin:

```bash
kvault --kb-root "$KB_ROOT" --json reconcile prepare EVENT_ID [EVENT_ID ...] \
  --path people/contacts/example --path people/contacts --path people --path .
kvault --kb-root "$KB_ROOT" --json reconcile apply < plan.json
```

The plan must account for every event, contain each complete proposed Markdown body (front matter
is supplied separately in `meta`), carry the expected revisions returned by `prepare`, and include
all affected ancestor summaries. Prepare a batch by passing every event ID positionally in one
command. For moves, include both old and new ancestor chains. Let kvault enforce policy and
serialize the transaction.

Use this versioned shape:

```json
{
  "schema_version": 1,
  "event_ids": ["EVENT_ID"],
  "decisions": [{
    "event_id": "EVENT_ID",
    "outcome": "apply",
    "reasoning": "Why this belongs in current state",
    "target_paths": ["people/contacts/example"]
  }],
  "mutations": [{
    "operation": "update",
    "path": "people/contacts/example",
    "content": "Complete Markdown body",
    "meta": {},
    "expected_revision": "sha256:..."
  }, {
    "operation": "summary",
    "path": "people/contacts",
    "content": "Complete revised parent summary body",
    "meta": {},
    "expected_revision": "sha256:..."
  }],
  "reasoning": "Batch-level rationale",
  "requested_by": "agent-id"
}
```

Use decision outcomes `apply`, `journal_only`, `duplicate`, or `no_op`. Applied plans use leaf
operations `create`, `update`, `move`, `merge`, or `delete`, plus one `summary` mutation for every
affected ancestor. A create has no expected revision; existing targets and summaries require one.
`needs_review` is a policy result or a pre-plan analyst signal, not a decision outcome. Use a stable
runtime agent identity for `requested_by`; approval `actor` must identify the authorizing human.

If the result is `needs_review`, do not bypass it:

```bash
kvault --kb-root "$KB_ROOT" --json reconcile status RECONCILIATION_ID
kvault --kb-root "$KB_ROOT" --json reconcile approve RECONCILIATION_ID --actor "human-id"
```

On `stale_plan`, prepare again and reconsider the proposal from current content. On an interrupted
transaction, inspect status when an ID is known, then run the root-wide recovery command; never
remove a lock or transaction directory:

```bash
kvault --kb-root "$KB_ROOT" --json reconcile recover
```

## Verify

An event is not resolved merely because a node write began. Confirm the reconciliation outcome,
then validate the full KB:

```bash
kvault --kb-root "$KB_ROOT" --json reconcile status RECONCILIATION_ID
kvault --kb-root "$KB_ROOT" --json validate
kvault --kb-root "$KB_ROOT" --json check
```

Treat any nonzero exit, `success: false`, failed mutation, stale revision, propagation error, or
integrity finding as unfinished work. Leave the event pending and report the exact blocker.
Lifecycle states are `pending`, `reconciling`, `needs_review`, and `resolved`; terminal outcomes are
reported separately on resolved events.

## Coordinate subagents safely

Use one coordinator as the only writer. Subagents may navigate, compare evidence, and return
proposals, but must not capture, apply, approve, recover, edit Markdown, or run Git commands.

For multi-event batches or work spanning independent branches, read
[references/parallel-reconciliation.md](references/parallel-reconciliation.md) completely before
delegating. Do not spawn workers for a simple one-node update.

## Hard rules

- Never edit node, event, policy, schema, lock, or transaction files directly.
- Never use legacy direct mutation commands; capture and reconcile instead.
- Never fabricate facts or discard conflicting evidence.
- Never restructure an unrelated branch during an ordinary update.
- Never auto-approve merges, moves, deletes, destructive replacement, or sensitive events.
- Never commit or push KB changes unless the user explicitly requests that Git action.
- Preserve the owning workspace's permissions, retention rules, and version-control policy.

## Command map

| Task | Commands |
|---|---|
| Capture and intake | `capture`, `events list`, `events show`, `events import` |
| Navigate | `tree`, `search`, `read`, `list` |
| Reconcile | `reconcile prepare`, `reconcile apply`, `reconcile approve`, `reconcile status`, `reconcile recover` |
| Integrity | `validate`, `check`, `status` |
| Upgrade | `migrate`, `migrate --dry-run` |
| Skill discovery | `skill path`, `skill install` |
