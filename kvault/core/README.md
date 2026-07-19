# Core modules

kvault 0.12 separates immutable evidence, semantic reconciliation, and read-only navigation.

## Supported services

- `events.py` captures immutable memory candidates and stores append-only plan/result records.
- `policy.py` loads machine-enforced automatic-apply and review gates.
- `reconciliation.py` prepares revision context, stages complete semantic trees, applies one
  serialized transaction, and recovers interrupted work.
- `migration.py` installs schema/policy state, backfills exact child digests, and imports legacy
  Moss/OpenClaw capture JSONL.
- `validation.py` provides the shared tree, provenance, frontmatter, and digest audit.
- `paths.py` and `transactions.py` centralize containment, locking, staging, rollback, and atomic
  replacement.
- `operations.py`, `search.py`, and the scanning functions in `storage.py` provide read-oriented
  tree navigation and search.

The supported mutation flow is:

```python
from pathlib import Path

from kvault.core.events import capture_event
from kvault.core.reconciliation import prepare_reconciliation, apply_reconciliation

root = Path("/absolute/path/to/knowledge-base")
event = capture_event(root, "Candidate text", source="conversation")
context = prepare_reconciliation(root, [event.event_id], paths=["people", "."])
# An external agent uses the event, bounded tree reads, policy, and revisions to form a full plan.
result = apply_reconciliation(root, plan)
```

kvault deliberately does not perform model inference. The caller forms the semantic proposal;
kvault validates evidence coverage, policy, revisions, paths, propagation, and filesystem state.

## Compatibility surface

`SimpleStorage` and the direct write helpers in `operations.py` are deprecated implementation
compatibility code for pre-0.12 callers. They are not agent-facing mutation APIs and are not
exposed by the 0.12 CLI or MCP server. New integrations must use capture and reconciliation.

Legacy `_meta.json` is read-only fallback data. New canonical nodes store mapping-shaped YAML
frontmatter and Markdown together in `_summary.md`.
