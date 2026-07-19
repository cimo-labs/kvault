"""Tests for 0.12 policy, schema migration, and Moss import."""

import json
from pathlib import Path

import pytest
import yaml

from kvault.core.events import EventStatus, derive_event_states, list_events
from kvault.core.frontmatter import build_frontmatter, parse_frontmatter
from kvault.core.migration import (
    DigestBackfillResult,
    MigrationRequiredError,
    current_schema,
    current_schema_version,
    import_moss_capture,
    migrate,
    migrate_kb,
    require_schema,
)
from kvault.core.policy import (
    PolicyError,
    ReconciliationPolicy,
    ensure_policy,
    load_policy,
    policy_allows_plan,
)
from kvault.core.transactions import FileTransaction, KBWriteLock, LockBusyError


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    root.mkdir()
    (root / "_summary.md").write_text("# Test\n", encoding="utf-8")
    return root


def test_default_policy_is_persisted_and_conservative(tmp_path: Path) -> None:
    root = _root(tmp_path)
    default = load_policy(root)
    assert default.require_event_for_mutations
    assert [item.value for item in default.auto_apply_operations] == [
        "create",
        "update",
        "summary",
    ]
    assert [item.value for item in default.review_operations] == [
        "move",
        "merge",
        "delete",
        "restructure",
    ]
    assert default.review_sensitivities == ["sensitive", "restricted"]
    assert default.additive_updates_only

    assert ensure_policy(root) == default
    persisted = root / ".kvault" / "policy.yaml"
    assert persisted.is_file()
    assert load_policy(root) == default

    safe = policy_allows_plan(
        default,
        {"event_ids": ["evt"], "mutations": [{"operation": "create"}]},
        sensitivities=["personal"],
    )
    assert safe.allowed
    review = policy_allows_plan(
        default,
        {"event_ids": ["evt"], "mutations": [{"operation": "delete"}]},
        sensitivities=["sensitive"],
    )
    assert not review.allowed and review.review_required
    assert len(review.reasons) == 2


def test_invalid_policy_is_not_silently_replaced(tmp_path: Path) -> None:
    root = _root(tmp_path)
    path = root / ".kvault" / "policy.yaml"
    path.parent.mkdir()
    path.write_text("- not\n- a mapping\n", encoding="utf-8")
    with pytest.raises(PolicyError):
        load_policy(root)


def test_migration_dry_run_apply_and_legacy_journal_preservation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    legacy = root / "journal" / "2026-02" / "log.md"
    legacy.parent.mkdir(parents=True)
    original = "# Historical journal\n\nNever rewrite me.\n"
    legacy.write_text(original, encoding="utf-8")

    with pytest.raises(MigrationRequiredError):
        require_schema(root)
    preview = migrate(
        root,
        dry_run=True,
        digest_backfill_hook=lambda _root, dry: DigestBackfillResult(
            planned_paths=["."] if dry else [], updated_paths=[] if dry else ["."]
        ),
    )
    assert preview.success and preview.dry_run
    assert preview.schema_after == 1
    assert not (root / ".kvault" / "schema.json").exists()
    assert preview.legacy_journal_paths == ["journal/2026-02/log.md"]

    applied = migrate_kb(root, dry_run=False)
    assert applied.success
    assert current_schema_version(root) == 1
    assert current_schema(root) == require_schema(root)
    assert load_policy(root) == ReconciliationPolicy()
    assert legacy.read_text(encoding="utf-8") == original

    repeated = migrate(root, dry_run=False)
    assert repeated.success and repeated.changes == []


def test_digest_backfill_error_does_not_install_schema(tmp_path: Path) -> None:
    root = _root(tmp_path)
    result = migrate(
        root,
        dry_run=False,
        digest_backfill_hook=lambda _root, _dry: DigestBackfillResult(errors=["bad node"]),
    )
    assert not result.success
    assert current_schema(root) is None


def test_default_migration_backfills_parent_digest_and_rolls_back_on_audit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    parent_meta = {
        "created": "2026-01-01",
        "updated": "2026-01-01",
        "source": "seed",
        "aliases": [],
    }
    (root / "_summary.md").write_text(
        build_frontmatter(parent_meta) + "# Test\n",
        encoding="utf-8",
    )
    child = root / "people"
    child.mkdir()
    (child / "_summary.md").write_text(
        build_frontmatter(parent_meta) + "# People\n\nKnown people.\n",
        encoding="utf-8",
    )

    preview = migrate(root, dry_run=True)
    assert preview.digest_backfill.planned_paths == ["_summary.md"]
    assert (
        "children_digest"
        not in parse_frontmatter((root / "_summary.md").read_text(encoding="utf-8"))[0]
    )

    applied = migrate(root, dry_run=False)
    assert applied.success
    root_meta, _body = parse_frontmatter((root / "_summary.md").read_text(encoding="utf-8"))
    assert root_meta["children_digest"].startswith("sha256:")
    assert str(root_meta["updated"]) == "2026-01-01"

    broken = tmp_path / "broken"
    broken.mkdir()
    original = build_frontmatter(parent_meta) + "# Broken\n"
    (broken / "_summary.md").write_text(original, encoding="utf-8")
    bad_child = broken / "people" / "alice"
    bad_child.mkdir(parents=True)
    (broken / "people" / "_summary.md").write_text(
        build_frontmatter(parent_meta) + "# People\n",
        encoding="utf-8",
    )
    (bad_child / "_summary.md").write_text("---\n- invalid\n---\n\n# Alice\n", encoding="utf-8")

    original_rollback = FileTransaction.rollback
    rollback_was_locked = False

    def rollback_while_locked(self: FileTransaction, reason: object = None) -> None:
        nonlocal rollback_was_locked
        with pytest.raises(LockBusyError):
            KBWriteLock(self.root, "unexpected-migrator").acquire()
        rollback_was_locked = True
        original_rollback(self, None if reason is None else str(reason))

    monkeypatch.setattr(FileTransaction, "rollback", rollback_while_locked)

    failed = migrate(broken, dry_run=False)
    assert not failed.success
    assert rollback_was_locked
    assert not (broken / ".kvault" / "schema.json").exists()
    assert (broken / "_summary.md").read_text(encoding="utf-8") == original


def test_moss_import_is_repeat_safe_and_processed_state_is_conservative(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    inbox = tmp_path / "kb-inbox.jsonl"
    processed = tmp_path / "kb-processed.jsonl"
    inbox_record = {
        "id": "open-1",
        "content": "Eddie prefers bounded tree navigation.",
        "source": "telegram",
        "timestamp": "2026-07-18T12:00:00Z",
        "tags": ["preference"],
    }
    processed_record = {
        "id": "done-1",
        "text": "A historical processed candidate.",
        "ts": "2026-07-18T12:30:00Z",
        "source": "telegram",
        "tags": ["historical"],
        "status": "archived",
        "archived_ts": "2026-07-18T13:00:00Z",
    }
    inbox.write_text(json.dumps(inbox_record) + "\n", encoding="utf-8")
    processed.write_text(json.dumps(processed_record) + "\n", encoding="utf-8")
    original_inbox = inbox.read_bytes()
    original_processed = processed.read_bytes()

    preview = import_moss_capture(root, inbox, processed, dry_run=True)
    assert preview.success and preview.total == 2
    assert list_events(root) == []

    first = import_moss_capture(root, inbox, processed, dry_run=False)
    assert first.success and first.created == 2
    assert first.pending == 1
    assert first.legacy_archived_unknown == 1
    states = derive_event_states(root)
    assert sorted((state.state for state in states.values()), key=lambda item: item.value) == [
        EventStatus.PENDING,
        EventStatus.RESOLVED,
    ]
    resolved = [state for state in states.values() if state.state == EventStatus.RESOLVED][0]
    assert resolved.terminal_outcome is not None
    assert resolved.terminal_outcome.value == "legacy_archived_unknown"

    repeated = import_moss_capture(root, inbox, processed, dry_run=False)
    assert repeated.success and repeated.created == 0 and repeated.existing == 2
    assert len(list_events(root)) == 2
    assert inbox.read_bytes() == original_inbox
    assert processed.read_bytes() == original_processed


def test_moss_record_present_in_both_queues_is_not_counted_as_pending(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    inbox = tmp_path / "kb-inbox.jsonl"
    processed = tmp_path / "kb-processed.jsonl"
    record = {
        "id": "moved-between-queues",
        "content": "The same candidate was moved from inbox to processed.",
        "timestamp": "2026-07-18T12:00:00Z",
    }
    serialized = json.dumps(record) + "\n"
    inbox.write_text(serialized, encoding="utf-8")
    processed.write_text(serialized, encoding="utf-8")

    preview = import_moss_capture(root, inbox, processed, dry_run=True)
    assert preview.success
    assert preview.pending == 0
    assert preview.legacy_archived_unknown == 1

    applied = import_moss_capture(root, inbox, processed, dry_run=False)
    assert applied.success
    assert applied.created == 1
    assert applied.existing == 1
    assert applied.pending == 0
    assert applied.legacy_archived_unknown == 1
    assert len(list_events(root)) == 1

    state = next(iter(derive_event_states(root).values()))
    assert state.state == EventStatus.RESOLVED
    assert state.terminal_outcome == "legacy_archived_unknown"


def test_moss_record_ids_are_scoped_by_source(tmp_path: Path) -> None:
    root = _root(tmp_path)
    inbox = tmp_path / "kb-inbox.jsonl"
    records = [
        {"id": "record-1", "source": "telegram", "content": "Telegram candidate."},
        {"id": "record-1", "source": "email", "content": "Email candidate."},
    ]
    inbox.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    applied = import_moss_capture(root, inbox, dry_run=False)

    assert applied.success and applied.created == 2 and applied.pending == 2
    events = list_events(root)
    assert len(events) == 2
    assert len({event.event_id for event in events}) == 2


def test_policy_yaml_shape_is_human_editable(tmp_path: Path) -> None:
    root = _root(tmp_path)
    ensure_policy(root)
    raw = yaml.safe_load((root / ".kvault" / "policy.yaml").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert raw["auto_resolve_outcomes"] == ["duplicate", "no_op", "journal_only"]
