# kvault Maintainer Notes

## Architecture contract

kvault 0.12 is a journal-first memory substrate:

1. Immutable temporal events preserve evidence.
2. Semantic nodes represent current durable state.
3. Parent summaries are derived navigation indexes.

CLI and MCP adapters must delegate to the same typed core services. Model reasoning, scheduling,
approval identity, and read-only worker orchestration remain host-runtime concerns.

## Critical invariants

1. Every supported 0.12 semantic mutation references at least one previously captured event;
   deprecated pre-0.12 Python compatibility helpers are not agent-facing APIs.
2. Event and reconciliation records are immutable and append-only.
3. One KB lock serializes every mutation transaction.
4. Every write is revision-checked, staged, validated, and atomically replaced.
5. A visible directory is a node only when it has `_summary.md`; every visible ancestor must also
   be a node.
6. Parent `children_digest` values exactly represent immediate child summaries.
7. All path handling uses the canonical, symlink-aware root resolver.
8. Root, hidden state, journals, and paths outside the resolved root are never destructive targets.
9. Frontmatter must parse as a YAML mapping. Legacy compatibility parsing is explicit, never the
   default mutation path.
10. CLI JSON failures return nonzero exit status. CLI and MCP expose equivalent outcomes.
11. Approval rechecks revisions and never bypasses integrity validation.
12. The portable skill, generated `AGENTS.md`, public docs, and adapters describe one protocol.

## Public services

The typed core surface includes event, policy, reconciliation, schema, migration, and audit
services. Important stable names include:

```python
capture_event(...)
get_event(...)
list_events(...)
write_reconciliation_plan(...)
read_reconciliation_plan(...)
write_reconciliation_result(...)
derive_event_states(...)
load_policy(...)
current_schema(...)
require_schema(...)
migrate(...)
import_moss_capture(...)
```

Keep filesystem semantics in core. Click and MCP layers translate inputs and structured outputs;
they must not reimplement policy, path, transaction, or integrity logic.

## Public commands

```text
capture
events list | show | import
reconcile prepare | apply | approve | status | recover
migrate
skill path | install
tree | search | read | list
validate | check | status
```

Legacy direct mutation names are compatibility stubs that return `workflow_required`. Adding a new
mutation escape hatch is a protocol change, not a convenience alias.

## Compatibility and migration

- Pre-0.12 vaults remain readable.
- Mutation requires `.kvault/schema.json` at the current version.
- `migrate --dry-run` must be side-effect-free.
- Applying migration must be transactional and repeat-safe.
- Never alter legacy monthly journal text or semantic body/history solely to migrate metadata.
- Moss-format processed captures import as `legacy_archived_unknown`, not `applied`.
- `SimpleStorage` is deprecated, path-contained compatibility code; it never creates `_meta.json`
  and must not be exposed as a supported 0.12 mutation surface.

## Skill and packaging

The source of truth is `skills/kvault/`. Setuptools installs those same files under
`share/knowledgevault/skills/kvault`; do not maintain a second package copy.

After any workflow or command change:

1. Update `SKILL.md` and, when relevant, its parallel-reconciliation reference.
2. Regenerate and review `agents/openai.yaml` using the skill-creator generator.
3. Update the generated KB `AGENTS.md` template.
4. Build wheel and sdist, install the wheel, and check `kvault skill path` and `skill install`.
5. Run the skill validator and forward-test at least a simple candidate and a read-only batch.

## Verification

```bash
pytest -q --cov=kvault --cov-report=term-missing --cov-fail-under=80
ruff check .
black --check kvault/ tests/
mypy kvault/ --ignore-missing-imports
python -m build
```

Required regression areas include path traversal and symlinks, forbidden deletion, malformed
frontmatter, orphan nodes, event idempotency/conflict, policy gates, stale revisions, concurrent
capture, competing writers, injected transaction failure/recovery, migration repeatability, CLI/MCP
parity, and installed-wheel skill discovery.

## Release procedure

1. Update package version, changelog, protocol docs, templates, skill, and migration notes together.
2. Run all checks on Python 3.9–3.13; run MCP tests on supported Python versions.
3. Build and inspect wheel/sdist, including all three skill files.
4. Merge through a reviewed PR; CI builds artifacts but never publishes.
5. Create and publish a GitHub release tagged exactly `v<pyproject version>`.
6. The `release: published` workflow verifies tag/version equality, rebuilds, smoke-tests the wheel,
   and publishes to PyPI through trusted publishing.

Never publish from a raw tag push or manual workflow dispatch. Never create a GitHub release from
the PyPI workflow.
