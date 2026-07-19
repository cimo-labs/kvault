# Parallel reconciliation

Use this workflow only for a batch with multiple events or independent semantic branches. The
coordinator captures events first and remains the only process allowed to mutate the KB.

## Sequence

1. Capture every memory candidate and collect its event ID.
2. Read the owning `AGENTS.md` and run one bounded root orientation pass.
3. Inspect enough event metadata to partition by stable identity and likely branch; never split one
   identity across workers. Keep ambiguous or cross-branch identities with the coordinator.
4. Give each worker an absolute KB root, event IDs, branch scope, applicable `AGENTS.md` rules, and
   a read-only command allowlist.
5. Collect proposal manifests and deduplicate identities and target paths centrally.
6. Prepare current revisions after all workers finish.
7. Compose one reconciliation plan and let the coordinator apply it.
8. Verify the reconciliation status, `validate`, and `check` before reporting completion.

All workers may run only `events show`, `tree`, `search`, `read`, and `list`. They must not edit
files or run `capture`, any `reconcile` command, `migrate`, or Git commands. Coordinator-supplied
rules are authoritative for the packet; do not assume workers inherited conversation context.

## Work packet

Send a packet with this shape:

```json
{
  "kb_root": "/absolute/path/to/knowledge-base",
  "event_ids": ["event-id-1", "event-id-2"],
  "scope": "people/contacts",
  "read_only": true,
  "allowed_commands": ["events show", "tree", "search", "read", "list"],
  "applicable_rules": ["Exact constraints copied from the owning AGENTS.md"],
  "instructions": "Return one proposal manifest as JSON; make no changes."
}
```

Scope is a navigation budget, not proof that the correct target is inside it or a confidentiality
boundary. Search can reveal an out-of-scope result; report `needs_review` instead of expanding into
that branch.

## Worker result

Require JSON with one proposal per event:

```json
{
  "scope": "people/contacts",
  "proposals": [
    {
      "event_id": "event-id-1",
      "decision": "apply",
      "target_paths": ["people/contacts/example"],
      "evidence": ["journal:event-id-1", "existing stable identifier"],
      "mutations": [{
        "operation": "update",
        "path": "people/contacts/example",
        "proposed_content": "Complete proposed Markdown body",
        "affected_ancestors": ["people/contacts", "people", "."]
      }],
      "confidence": 0.94,
      "notes": "Why this is the same identity"
    }
  ],
  "conflicts": [],
  "unresolved": []
}
```

Allowed decisions are `apply`, `journal_only`, `duplicate`, `no_op`, and `needs_review`. An `apply`
proposal supplies one or more `target_paths` plus a `mutations` entry for each proposed `create` or
`update`; each entry contains its path, complete proposed Markdown body, and affected ancestors
through `.`. Non-apply and `needs_review` proposals use `target_paths: []` and `mutations: []`.
Workers may
describe a possible merge, move, or delete only in `notes` on a `needs_review` proposal; they never
approve or apply it. `conflicts` contains evidence disagreements, while `unresolved` contains
missing identity, date, authority, or placement facts.

Reject a manifest that omits an event, proposes edits outside scope without flagging them, uses a
relative root, or claims to have changed the KB. Ask once for a corrected manifest; if it remains
invalid, retain those events for coordinator or human review. Independently validated events may
be split into another serialized reconciliation only after ruling out shared identities, targets,
and ancestor-summary conflicts.

## Coordinator merge rules

- Group proposals by stable identity and target path before preparing revisions.
- Resolve competing proposals from evidence; do not choose by confidence score alone.
- Preserve every event ID in the final plan, including journal-only and no-op decisions.
- Prepare after proposal collection so revisions are as fresh as possible. Pass all batch event IDs
  as positional arguments in one `reconcile prepare EVENT_ID [EVENT_ID ...]` command.
- Apply leaves and shared ancestors in one serialized reconciliation, never one worker at a time.
- If the plan becomes stale, discard derived summaries and re-prepare.

## OpenClaw example

OpenClaw's `sessions_spawn` is asynchronous. Put the complete packet and output contract in the
task; do not rely on the worker inheriting conversational context.

```text
sessions_spawn {
  "label": "kvault-read-people",
  "task": "Use the kvault skill to analyze the following work packet. You are read-only: do not capture, reconcile, migrate, edit files, or run Git. Run only events show, tree, search, read, and list with the absolute root. Return only the worker-result JSON schema from the skill reference. PACKET: {\"kb_root\":\"/absolute/path/to/knowledge-base\",\"event_ids\":[\"event-id-1\"],\"scope\":\"people/contacts\",\"read_only\":true,\"allowed_commands\":[\"events show\",\"tree\",\"search\",\"read\",\"list\"],\"applicable_rules\":[\"Exact constraints copied from AGENTS.md\"]}"
}
```

Collect each worker result with the runtime's session-history tool. The parent session remains the
coordinator and is the only session that may run `reconcile prepare`, `apply`, `approve`, or
`recover`.
