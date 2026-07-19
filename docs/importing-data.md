# Importing source data

Chat, email, calendar, and note exports can contain durable memory candidates. Treat the raw export
as source material, not as semantic nodes.

> Exports may contain sensitive information. kvault stores evidence locally, but the agent used to
> interpret it may send excerpts to its model provider. Redact or choose a local provider when the
> source requires it.

## Keep raw data separate

Place exports outside the KB root so structural validation never mistakes raw folders for semantic
nodes:

```bash
mkdir -p source-data/conversations
unzip conversation-export.zip -d source-data/conversations
KB_ROOT="$(cd my_kb && pwd)"
SOURCE_ROOT="$(cd source-data && pwd)"
```

## Capture candidates in bounded batches

Have the importer extract one coherent memory candidate at a time and call `capture` before doing
semantic research:

```bash
kvault --kb-root "$KB_ROOT" --json capture \
  --source "conversation-export" \
  --source-ref "conversation:42:message:8" \
  --occurred-at "2026-07-01T15:20:00Z" \
  --sensitivity personal < candidate.md
```

Use a stable source reference when the export supplies one. This makes retries idempotent. Reusing
a stable reference with different content is a conflict that must be investigated.

Do not combine unrelated facts solely to reduce the event count. Do not create nodes directly from
raw files or mark an event applied before reconciliation succeeds.

## Reconcile after capture

For each batch:

1. List pending events with `kvault events list`.
2. Inspect each candidate with `kvault events show`.
3. Use a bounded `tree`, then scoped `search` and `read` commands.
4. Prepare and apply one reconciliation for the batch.
5. Confirm reconciliation status, `validate`, and `check`.

Read-only subagents can analyze separate branches for a large batch, but one coordinator must merge
their proposals and perform the serialized reconciliation.

Watch the first batch closely. Correct capture granularity, stable references, sensitivity labels,
and semantic placement before scaling up.

## Importing an older capture queue

`kvault events import` imports supported queue formats without bypassing the ledger. For the Moss
OpenClaw JSONL format:

```bash
kvault --kb-root "$KB_ROOT" --json events import \
  --format moss-capture --input /path/to/kb-inbox.jsonl \
  --processed /path/to/kb-inbox.processed.jsonl --dry-run
kvault --kb-root "$KB_ROOT" --json events import \
  --format moss-capture --input /path/to/kb-inbox.jsonl \
  --processed /path/to/kb-inbox.processed.jsonl
```

Open records become pending. Previously processed records become `legacy_archived_unknown` because
the old queue cannot prove whether they were promoted into semantic nodes. Import is repeat-safe;
review aggregate counts and conflicts after both the dry-run and applying pass.

See [migrating-0.12.md](migrating-0.12.md) before importing into a pre-0.12 vault.
