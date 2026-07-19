# OpenClaw integration

OpenClaw agents often accumulate several useful memory tiers over time. kvault should replace only
the durable structured-memory write path; it should not ingest every chat turn, daily log, or
operational note.

## Memory-tier boundary

| OpenClaw material | Role after 0.12 |
|---|---|
| Current conversation | Source of admitted memory candidates |
| Daily `memory/YYYY-MM-DD.md` | Operational chronology; capture only durable candidates |
| Curated `MEMORY.md` | Agent-level working memory; reconcile selected durable facts |
| Capture inbox JSONL | Transitional queue imported into the immutable event journal |
| kvault temporal journal | Durable source evidence and reconciliation history |
| kvault semantic tree | Current, source-backed durable state |

“Capture first” applies when new information is admitted for durable memory. It does not require an
event merely to read the KB at task start. Do not bulk-copy a daily note or `MEMORY.md`; extract one
coherent source record per admitted candidate and preserve its source reference.

## Existing Moss queue compatibility

The established OpenClaw helper format uses:

```json
{
  "id": "stable-record-id",
  "ts": "2026-07-19T12:34:00Z",
  "source": "conversation-source",
  "tags": ["topic"],
  "text": "Exact candidate text",
  "status": "new"
}
```

Archived records add `archived_ts` and use `status: archived`. Import the conventional files with:

```bash
kvault --kb-root "$KB_ROOT" --json events import \
  --format moss-capture \
  --input /absolute/openclaw/workspace/capture/kb-inbox.jsonl \
  --processed /absolute/openclaw/workspace/capture/kb-inbox.processed.jsonl \
  --dry-run
```

Then rerun without `--dry-run` after reviewing counts. Open records become pending. Archived
records become `legacy_archived_unknown`: the old archive flag proves queue disposition, not that a
semantic update succeeded. The importer never rewrites either source file and is repeat-safe by
`source + id`.

## Cutover sequence

1. Stop the legacy capture wrapper, nightly semantic writer, weekly repair writer, and any
   maintenance option that directly edits or auto-commits semantic nodes.
2. Leave sync/health monitoring read-only while the cutover is in progress.
3. Back up the KB, preview migration, apply migration, and run `validate` plus `check`.
4. Dry-run and apply the legacy queue import shown above.
5. Install the bundled skill into the OpenClaw workspace.
6. Remove or replace workspace instructions that mention direct writes, the old two-call
   write/summary workflow, or archiving a candidate as proof of promotion.
7. Run one non-sensitive capture/reconcile canary and inspect its event, node provenance, ancestor
   digests, reconciliation result, and integrity output.
8. Re-enable intake. Re-enable scheduled reconciliation only after clean canaries.
9. Keep Git commit/push as a separate host-authorized step after reconciliation and validation;
   read-only workers never perform it.

Do not point the old writer at a schema-1 vault during rollback. Stop 0.12 writers and restore the
pre-migration backup or commit instead.

## Scheduled reconciliation

A nightly coordinator may:

1. list pending events;
2. group them by stable identity and likely branch;
3. delegate independent branches to read-only OpenClaw sessions using the work packet in the
   packaged skill reference;
4. validate and deduplicate returned proposal manifests;
5. prepare all event IDs and the union of target/ancestor paths;
6. apply one policy-gated reconciliation at a time;
7. recover interruptions and run `validate` plus `check`;
8. report unresolved or review-gated events without guessing.

The coordinator is the only writer. Workers may use `events show`, `tree`, `search`, `read`, and
`list`; they may not capture, reconcile, approve, recover, migrate, edit files, or run Git.

Maintenance findings also enter through evidence. For example, a stale summary or suspected
duplicate can be captured with a stable audit-run source reference, then reconciled under the same
policy and approval rules. A validator finding is a review signal, not authority to restructure or
delete.

## Capture wrapper replacement

An OpenClaw wrapper may remain for convenience only if it delegates directly to `kvault capture`,
passes an absolute root, preserves exact candidate text, supplies a stable source reference when
available, and returns the structured kvault result. It must not maintain a second authoritative
inbox, rewrite events, or archive a record independently of its reconciliation outcome.
