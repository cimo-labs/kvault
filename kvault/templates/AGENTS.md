# Knowledge Base Operating Rules

This knowledge base belongs to **{{OWNER_NAME}}**. Use kvault as its only mutation interface.

## Non-negotiable rules

1. **Capture first.** Log every admitted memory candidate as an immutable event before navigating
   or changing semantic nodes.
2. **Use an absolute root.** Pass `--kb-root` on every command; do not depend on the current
   directory in delegated or automated work.
3. **One writer.** A coordinator may delegate read-only analysis, but only the coordinator may
   apply, approve, or recover a reconciliation.
4. **No direct edits.** Do not edit `_summary.md`, event, policy, schema, lock, or transaction files
   with filesystem tools. Legacy direct mutation commands are not a substitute for reconciliation.
5. **Search before create.** Inspect the bounded tree, search aliases and body text, and read the
   likely node before proposing a create.
6. **Keep evidence.** Never fabricate facts, discard conflicts, or merge identities on fuzzy
   similarity. Use a stable identifier or request review.
7. **Verify completion.** A semantic write is incomplete until ancestor summaries are current,
   integrity checks pass, and every event has a recorded outcome.
8. **Respect authority.** Do not approve destructive or sensitive work, or commit and push KB
   changes, unless the user explicitly authorizes it.

## Journal-first workflow

Set the root once for the session:

```bash
KB_ROOT="/absolute/path/to/this/knowledge-base"
```

### 1. Capture

Put the exact memory candidate on stdin:

```bash
kvault --kb-root "$KB_ROOT" --json capture \
  --source "conversation" \
  --source-ref "message:stable-id" \
  --occurred-at "2026-07-19T12:34:00Z" \
  --sensitivity personal < candidate.md
```

Retain the event ID. If `source + source_ref` already exists with the same content, reuse the
existing event. Treat different content under the same stable reference as a conflict.

One source record normally becomes one event. Preserve its wording and omit optional occurrence
time or source-reference metadata when the source does not supply them; never invent an identity,
absolute date, or provenance value. Ambiguity blocks semantic promotion, not capture. When capture
returns an existing event, inspect it with `events show` and reconcile it only if it is pending.

### 2. Orient and research

Start bounded, then drill into only relevant branches:

```bash
kvault --kb-root "$KB_ROOT" --json tree --depth 2 --max-children 20
kvault --kb-root "$KB_ROOT" --json search "name or topic"
kvault --kb-root "$KB_ROOT" --json tree people --depth 2 --gist
kvault --kb-root "$KB_ROOT" --json read people/contacts/example
```

An `…` marker means the view is partial. Inspect that branch when it may contain the target.

Choose one outcome per event:

| Evidence | Decision |
|---|---|
| Existing identity and compatible durable fact | `update` |
| New durable subject under an existing parent | `create` |
| Evidence is useful but not semantic current state | `journal_only` |
| Evidence adds nothing | `no_op` or `duplicate` |
| Identity, replacement, structure, or authority is uncertain | leave pending / `needs_review` analyst signal |

### 3. Prepare and reconcile

```bash
kvault --kb-root "$KB_ROOT" --json reconcile prepare EVENT_ID [EVENT_ID ...] \
  --path people/contacts/example --path people/contacts --path people --path .
kvault --kb-root "$KB_ROOT" --json reconcile apply < plan.json
```

Build the plan from current revisions returned by `prepare`. Include complete proposed node
content and every affected parent summary through root. A move affects both old and new ancestor
chains. kvault validates policy, source references, hierarchy, revisions, and propagation before
committing the transaction.

Pass every batch event ID positionally to one prepare command. The plan uses schema version 1,
lists every event ID exactly once in `decisions`, and contains
batch-level `reasoning` and `requested_by`. Decision outcomes are `apply`, `journal_only`,
`duplicate`, or `no_op`. Mutations use `create`, `update`, `summary`, `move`, `merge`, or `delete`;
existing targets carry their `sha256:` revision from `prepare`.

`needs_review` is a policy result or a pre-plan signal, not a plan decision. Use `journal_only`
only when the evidence is intentionally not semantic current state; unresolved dates, identity, or
authority remain pending for clarification.

Policy may return `needs_review` instead of applying:

```bash
kvault --kb-root "$KB_ROOT" --json reconcile status RECONCILIATION_ID
kvault --kb-root "$KB_ROOT" --json reconcile approve RECONCILIATION_ID --actor "human-id"
```

Never bypass review by changing files or decomposing a destructive plan into direct commands.
Re-prepare a `stale_plan`. For an interrupted transaction, inspect and use `reconcile recover`;
never delete its lock or staging directory.

### 4. Verify

```bash
kvault --kb-root "$KB_ROOT" --json reconcile status RECONCILIATION_ID
kvault --kb-root "$KB_ROOT" --json validate
kvault --kb-root "$KB_ROOT" --json check
```

Check the process exit code and JSON `success`, `error`, and `errors` fields for every command.
Parseable JSON is not proof of success. Leave failed work pending and report its exact blocker.

## Read-only delegation

Subagents may run only `events show`, `tree`, `search`, `read`, and `list`. Give each worker the
absolute root, event IDs, branch scope, applicable rules from this file, and a JSON result contract.
Scope is a navigation budget, not a confidentiality boundary. Workers return proposals; they do
not capture, mutate, approve, recover, migrate, or run Git. The coordinator deduplicates all
proposals and performs one serialized reconciliation after every worker finishes.

Use delegation for multi-event or multi-branch analysis, not routine single-node updates.

## Maintenance

Run bounded orientation and `check` periodically. Treat these as review signals, not permission to
perform unrelated restructuring:

| Signal | Response |
|---|---|
| Truncated or crowded branch | Drill down; propose restructuring separately if navigation suffers |
| Old subtree activity | Review whether current state is still accurate |
| Missing child coverage or stale digest | Reconcile a corrected parent rollup |
| Similar names or aliases | Compare stable identifiers; request review if identity is uncertain |
| Pending or interrupted events | Resolve, retry, or recover them before new maintenance writes |

Parent summaries describe current state. Put dated maintenance history in event/reconciliation
records rather than accumulating it in the root summary.

## Commands

| Task | Commands |
|---|---|
| Intake | `capture`, `events list`, `events show`, `events import` |
| Read | `tree`, `search`, `read`, `list` |
| Reconcile | `reconcile prepare`, `reconcile apply`, `reconcile approve`, `reconcile status`, `reconcile recover` |
| Integrity | `validate`, `check`, `status` |
| Upgrade | `migrate`, `migrate --dry-run` |
| Skill | `skill path`, `skill install` |
