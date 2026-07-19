# Migrating to kvault 0.12

Version 0.12 replaces direct semantic writes with journal-first, policy-gated reconciliation. Old
vaults remain readable, but mutation commands return `migration_required` until an explicit
migration succeeds.

## Breaking changes

- New information must enter through `kvault capture` before semantic mutation.
- Direct write, summary, move, delete, and journal commands no longer mutate. They return
  `workflow_required` with capture/reconcile instructions.
- JSON failures now use nonzero process exit codes.
- MCP exposes capture/read/reconciliation parity rather than direct mutation tools.
- Strict mapping-shaped frontmatter, complete visible-node ancestry, safe paths, and exact parent
  child-digests are enforced.
- Reconciliation may require explicit approval under `.kvault/policy.yaml`.

## Before migrating

1. Upgrade the CLI in an isolated environment and verify `kvault status --json` reports 0.12.
2. Stop automated writers and scheduled maintenance for this KB.
3. Back up or commit the KB according to its existing version-control policy. Do not push unless
   explicitly authorized.
4. Resolve existing lock files or running processes with the old runtime; do not delete unknown
   state merely to make migration proceed.
5. Use the absolute KB path for every command below.

## Dry-run and apply

```bash
KB_ROOT="/absolute/path/to/knowledge-base"
kvault --kb-root "$KB_ROOT" --json migrate --dry-run
kvault --kb-root "$KB_ROOT" --json migrate
kvault --kb-root "$KB_ROOT" --json validate
kvault --kb-root "$KB_ROOT" --json check
```

Inspect the process exit status and structured success/errors for every command. The migration:

- creates `.kvault/schema.json` and a conservative `.kvault/policy.yaml`
- validates every existing visible node and its frontmatter
- backfills derived immediate-child digests
- leaves semantic bodies, historical dates, and monthly `journal/YYYY-MM/log.md` files unchanged

Migration is transactional and repeat-safe. A failure must leave the pre-migration KB recoverable;
report the exact validation finding rather than hand-editing schema or transaction files.

## After migration

Install the packaged skill into the agent runtime and replace old write prompts with the
capture-first protocol:

```bash
kvault skill path
kvault skill install /absolute/path/to/runtime/skills/kvault
```

Test a non-sensitive canary event end-to-end:

1. Capture with a stable source reference.
2. Confirm it appears pending.
3. Prepare, navigate, and apply a low-risk additive reconciliation.
4. Confirm the terminal outcome and source reference on the node.
5. Run `validate` and `check`.

Do not re-enable automation until the canary is clean.

## Moss/OpenClaw staged cutover

Use this sequence for an OpenClaw deployment that previously used a custom JSONL inbox. It is a
generic runbook and contains no host-specific paths or personal data.

1. Disable the old capture wrapper and every scheduled semantic writer.
2. Install 0.12 in a separate environment; keep the previous executable available for rollback.
3. Run the vault migration dry-run, then migration and integrity checks.
4. Dry-run the inbox import:

     ```bash
     kvault --kb-root "$KB_ROOT" --json events import \
       --format moss-capture --input /absolute/path/to/kb-inbox.jsonl \
       --processed /absolute/path/to/kb-inbox.processed.jsonl --dry-run
     ```

5. Review pending, archived-unknown, duplicate, and conflict counts; then run the applying import.
6. Install the 0.12 skill in the OpenClaw workspace and remove duplicated old workflow text from
   workspace instructions.
7. Capture and reconcile one canary event. Verify the event, semantic source reference, ancestor
   digests, reconciliation outcome, and full integrity result.
8. Re-enable intake first. Re-enable scheduled reconciliation only after observing clean canaries.
9. Retain the old JSONL and monthly journals as evidence until the owner applies their retention
   policy; do not claim archived records were semantically incorporated.

Rollback means stopping 0.12 writers and restoring the pre-migration backup or commit. Do not run
the legacy writer against a partially migrated live directory.

See [openclaw-integration.md](openclaw-integration.md) for memory-tier boundaries, the observed Moss
queue shape, scheduled read-only subagents, and the instruction conflicts that must be removed at
cutover.
