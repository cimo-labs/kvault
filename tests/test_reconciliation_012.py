"""End-to-end tests for journal-first reconciliation and recovery semantics."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import operations as ops
from kvault.core.events import EventStatus, capture_event, derive_event_states
from kvault.core.frontmatter import parse_frontmatter
from kvault.core.reconciliation import (
    ReconciliationError,
    ReconciliationPlan,
    apply_reconciliation,
    approve_reconciliation,
    prepare_reconciliation,
    recover_reconciliations,
)
from kvault.core.transactions import FileTransaction, KBWriteLock, LockBusyError
from kvault.core.validation import audit_kb


def _initialized_root(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    result = CliRunner().invoke(cli, ["init", str(root), "--name", "Test Owner"])
    assert result.exit_code == 0, result.output
    return root


def _summary_mutations(
    root: Path,
    revisions: Dict[str, str],
    paths: List[str],
    suffix: str,
) -> List[Dict[str, Any]]:
    mutations: List[Dict[str, Any]] = []
    for path in paths:
        current = ops.read_node(root, path, parents="none")
        assert current is not None
        mutations.append(
            {
                "operation": "summary",
                "path": path,
                "content": current["content"].rstrip() + f"\n\n{suffix}\n",
                "meta": {},
                "expected_revision": revisions[path],
            }
        )
    return mutations


def _create_plan(
    root: Path,
    event_id: str,
    *,
    target: str = "people/friends/alice",
    name: str = "Alice",
) -> ReconciliationPlan:
    ancestors = ["people/friends", "people", "."]
    prepared = prepare_reconciliation(root, [event_id], paths=ancestors)
    revisions = prepared["revisions"]
    return ReconciliationPlan.model_validate(
        {
            "schema_version": 1,
            "event_ids": [event_id],
            "decisions": [
                {
                    "event_id": event_id,
                    "outcome": "apply",
                    "reasoning": "A durable personal preference belongs on the person node.",
                    "target_paths": [target],
                }
            ],
            "mutations": [
                {
                    "operation": "create",
                    "path": target,
                    "content": f"# {name}\n\n{name} prefers jasmine tea.\n",
                    "meta": {"aliases": [name]},
                },
                *_summary_mutations(
                    root,
                    revisions,
                    ancestors,
                    f"- {name}: prefers jasmine tea.",
                ),
            ],
            "reasoning": "Create the person and refresh every ancestor rollup.",
            "requested_by": "pytest",
        }
    )


def test_create_is_staged_applied_and_provenance_linked(tmp_path: Path) -> None:
    root = _initialized_root(tmp_path)
    captured = capture_event(
        root,
        "Alice prefers jasmine tea.",
        source="test",
        source_ref="message-1",
    )
    assert captured.event_id is not None

    result = apply_reconciliation(root, _create_plan(root, captured.event_id))

    assert result.success and result.status == "applied"
    assert result.validation["valid"] is True
    assert derive_event_states(root)[captured.event_id].state == EventStatus.RESOLVED
    for relative in (
        "people/friends/alice/_summary.md",
        "people/friends/_summary.md",
        "people/_summary.md",
        "_summary.md",
    ):
        meta, _body = parse_frontmatter((root / relative).read_text(encoding="utf-8"))
        assert f"journal:{captured.event_id}" in meta["source_refs"]
    assert audit_kb(root)["valid"] is True

    state_path = root / ".kvault" / "transactions" / result.reconciliation_id / "state.json"
    assert '"status": "committed"' in state_path.read_text(encoding="utf-8")
    assert not state_path.parent.joinpath("stage").exists()
    assert not state_path.parent.joinpath("backups").exists()


def test_batch_provenance_is_scoped_to_each_events_declared_targets(tmp_path: Path) -> None:
    root = _initialized_root(tmp_path)
    alice = capture_event(root, "Alice prefers jasmine tea.", source="test")
    bob = capture_event(root, "Bob works at Acme.", source="test")
    redundant = capture_event(root, "The KB owner is already known.", source="test")
    assert alice.event_id and bob.event_id and redundant.event_id
    ancestors = ["people/friends", "people/contacts", "people", "."]
    prepared = prepare_reconciliation(
        root,
        [alice.event_id, bob.event_id, redundant.event_id],
        paths=ancestors,
    )
    plan = {
        "schema_version": 1,
        "event_ids": [alice.event_id, bob.event_id, redundant.event_id],
        "decisions": [
            {
                "event_id": alice.event_id,
                "outcome": "apply",
                "reasoning": "Alice belongs in friends.",
                "target_paths": ["people/friends/alice"],
            },
            {
                "event_id": bob.event_id,
                "outcome": "apply",
                "reasoning": "Bob belongs in contacts.",
                "target_paths": ["people/contacts/bob"],
            },
            {
                "event_id": redundant.event_id,
                "outcome": "no_op",
                "reasoning": "The owner is already represented.",
            },
        ],
        "mutations": [
            {
                "operation": "create",
                "path": "people/friends/alice",
                "content": "# Alice\n\nPrefers jasmine tea.\n",
            },
            {
                "operation": "create",
                "path": "people/contacts/bob",
                "content": "# Bob\n\nWorks at Acme.\n",
            },
            *_summary_mutations(
                root,
                prepared["revisions"],
                ancestors,
                "- Alice and Bob were updated.",
            ),
        ],
        "reasoning": "Apply two independent facts in one serialized batch.",
    }

    result = apply_reconciliation(root, plan)
    assert result.success

    refs: Dict[str, set[str]] = {}
    for path in [
        "people/friends/alice",
        "people/contacts/bob",
        "people/friends",
        "people/contacts",
        "people",
        ".",
    ]:
        relative = "_summary.md" if path == "." else f"{path}/_summary.md"
        meta, _body = parse_frontmatter((root / relative).read_text(encoding="utf-8"))
        refs[path] = set(meta["source_refs"])

    alice_ref = f"journal:{alice.event_id}"
    bob_ref = f"journal:{bob.event_id}"
    redundant_ref = f"journal:{redundant.event_id}"
    assert alice_ref in refs["people/friends/alice"]
    assert bob_ref not in refs["people/friends/alice"]
    assert bob_ref in refs["people/contacts/bob"]
    assert alice_ref not in refs["people/contacts/bob"]
    assert alice_ref in refs["people/friends"] and bob_ref not in refs["people/friends"]
    assert bob_ref in refs["people/contacts"] and alice_ref not in refs["people/contacts"]
    assert {alice_ref, bob_ref}.issubset(refs["people"])
    assert {alice_ref, bob_ref}.issubset(refs["."])
    assert all(redundant_ref not in path_refs for path_refs in refs.values())


def test_root_current_state_can_be_updated_with_event_provenance(tmp_path: Path) -> None:
    root = _initialized_root(tmp_path)
    captured = capture_event(root, "The current focus includes launch readiness.", source="test")
    assert captured.event_id is not None
    prepared = prepare_reconciliation(root, [captured.event_id], paths=["."])
    current = ops.read_node(root, ".", parents="none")
    assert current is not None
    plan = {
        "schema_version": 1,
        "event_ids": [captured.event_id],
        "decisions": [
            {
                "event_id": captured.event_id,
                "outcome": "apply",
                "reasoning": "This is current vault-wide context.",
                "target_paths": ["."],
            }
        ],
        "mutations": [
            {
                "operation": "update",
                "path": ".",
                "content": current["content"].rstrip() + "\n\nLaunch readiness is a focus.\n",
                "expected_revision": prepared["revisions"]["."],
            }
        ],
        "reasoning": "Append current root context without changing the hierarchy.",
    }

    result = apply_reconciliation(root, plan)

    assert result.success
    meta, body = parse_frontmatter((root / "_summary.md").read_text(encoding="utf-8"))
    assert "Launch readiness is a focus." in body
    assert f"journal:{captured.event_id}" in meta["source_refs"]


def test_stale_revision_fails_without_overwriting_competing_change(tmp_path: Path) -> None:
    root = _initialized_root(tmp_path)
    captured = capture_event(root, "Alice prefers tea.", source="test")
    assert captured.event_id is not None
    plan = _create_plan(root, captured.event_id)

    target = root / "people" / "friends" / "_summary.md"
    competing = target.read_text(encoding="utf-8") + "\nConcurrent update.\n"
    target.write_text(competing, encoding="utf-8")

    result = apply_reconciliation(root, plan)

    assert not result.success and result.status == "failed"
    assert result.error is not None and "stale_plan" in result.error
    assert target.read_text(encoding="utf-8") == competing
    assert not (root / "people" / "friends" / "alice").exists()
    assert derive_event_states(root)[captured.event_id].state == EventStatus.PENDING


def test_sensitive_plan_requires_approval_then_rechecks_and_applies(tmp_path: Path) -> None:
    root = _initialized_root(tmp_path)
    captured = capture_event(
        root,
        "Alice prefers jasmine tea.",
        source="test",
        sensitivity="sensitive",
    )
    assert captured.event_id is not None

    review = apply_reconciliation(root, _create_plan(root, captured.event_id))

    assert not review.success and review.status == "needs_review"
    assert any("sensitivity" in reason for reason in review.review_reasons)
    assert not (root / "people" / "friends" / "alice").exists()
    assert derive_event_states(root)[captured.event_id].state == EventStatus.NEEDS_REVIEW

    approved = approve_reconciliation(root, review.reconciliation_id, "owner@example.test")
    assert approved.success and approved.approved_by == "owner@example.test"
    assert (root / "people" / "friends" / "alice" / "_summary.md").is_file()


def test_injected_live_apply_failure_rolls_back_complete_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _initialized_root(tmp_path)
    captured = capture_event(root, "Alice prefers tea.", source="test")
    assert captured.event_id is not None
    before = (root / "people" / "friends" / "_summary.md").read_bytes()

    from kvault.core import reconciliation

    original = reconciliation.atomic_write_bytes
    calls = 0

    def fail_after_first(path: Path, data: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected live write failure")
        original(path, data)

    monkeypatch.setattr(reconciliation, "atomic_write_bytes", fail_after_first)
    result = apply_reconciliation(root, _create_plan(root, captured.event_id))

    assert not result.success and result.rollback_performed
    assert not (root / "people" / "friends" / "alice").exists()
    assert (root / "people" / "friends" / "_summary.md").read_bytes() == before
    assert derive_event_states(root)[captured.event_id].state == EventStatus.PENDING


def test_no_op_resolves_event_without_tree_mutation(tmp_path: Path) -> None:
    root = _initialized_root(tmp_path)
    captured = capture_event(root, "Already represented.", source="test")
    assert captured.event_id is not None
    before = (root / "_summary.md").read_bytes()
    plan = {
        "schema_version": 1,
        "event_ids": [captured.event_id],
        "decisions": [
            {
                "event_id": captured.event_id,
                "outcome": "no_op",
                "reasoning": "The durable fact is already represented exactly.",
            }
        ],
        "mutations": [],
        "reasoning": "No semantic mutation is needed.",
        "requested_by": "pytest",
    }

    result = apply_reconciliation(root, plan)

    assert result.success
    assert result.event_outcomes[captured.event_id] == "no_op"
    assert (root / "_summary.md").read_bytes() == before
    assert derive_event_states(root)[captured.event_id].state == EventStatus.RESOLVED

    with pytest.raises(ReconciliationError, match="not pending") as repeated:
        apply_reconciliation(root, plan)
    assert repeated.value.code == "event_not_pending"


def test_plan_creation_and_event_state_check_are_serialized(tmp_path: Path) -> None:
    root = _initialized_root(tmp_path)
    captured = capture_event(root, "Already represented.", source="test")
    assert captured.event_id is not None
    plan = {
        "schema_version": 1,
        "event_ids": [captured.event_id],
        "decisions": [
            {
                "event_id": captured.event_id,
                "outcome": "no_op",
                "reasoning": "The fact is already represented.",
            }
        ],
        "mutations": [],
        "reasoning": "No semantic mutation is needed.",
    }

    with KBWriteLock(root, "competing-writer"):
        with pytest.raises(ReconciliationError) as locked:
            apply_reconciliation(root, plan)

    assert locked.value.code == "lock_busy"
    assert derive_event_states(root)[captured.event_id].state == EventStatus.PENDING
    reconciliations = root / "journal" / "reconciliations"
    assert not reconciliations.exists() or not list(reconciliations.rglob("plan.md"))


def test_recovery_releases_plan_created_before_transaction_begin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _initialized_root(tmp_path)
    captured = capture_event(root, "Alice prefers tea.", source="test")
    assert captured.event_id is not None
    plan = _create_plan(root, captured.event_id)
    original_begin = FileTransaction.begin

    def interrupt_before_begin(self: FileTransaction, _paths: List[str]) -> None:
        raise KeyboardInterrupt("injected interruption before transaction begin")

    monkeypatch.setattr(FileTransaction, "begin", interrupt_before_begin)
    with pytest.raises(KeyboardInterrupt):
        apply_reconciliation(root, plan)

    state = derive_event_states(root)[captured.event_id]
    assert state.state == EventStatus.RECONCILING
    assert state.reconciliation_id is not None

    monkeypatch.setattr(FileTransaction, "begin", original_begin)
    recovered = recover_reconciliations(root)
    assert recovered["recovered"] == [
        {"reconciliation_id": state.reconciliation_id, "action": "released_pending_plan"}
    ]
    assert derive_event_states(root)[captured.event_id].state == EventStatus.PENDING


def test_recovery_rolls_back_active_transaction_and_releases_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _initialized_root(tmp_path)
    captured = capture_event(root, "Alice prefers tea.", source="test")
    assert captured.event_id is not None

    from kvault.core import reconciliation

    original_stage = reconciliation._stage_reconciliation

    def interrupt_after_begin(*_args: Any, **_kwargs: Any) -> Any:
        raise KeyboardInterrupt("injected interruption after transaction begin")

    monkeypatch.setattr(reconciliation, "_stage_reconciliation", interrupt_after_begin)
    with pytest.raises(KeyboardInterrupt):
        apply_reconciliation(root, _create_plan(root, captured.event_id))

    state = derive_event_states(root)[captured.event_id]
    assert state.state == EventStatus.RECONCILING
    assert state.reconciliation_id is not None
    assert FileTransaction(root, state.reconciliation_id).state["status"] == "prepared"

    monkeypatch.setattr(reconciliation, "_stage_reconciliation", original_stage)
    recovered = recover_reconciliations(root)
    assert recovered["recovered"] == [
        {"reconciliation_id": state.reconciliation_id, "action": "rolled_back"}
    ]
    assert derive_event_states(root)[captured.event_id].state == EventStatus.PENDING


def test_failure_rolls_back_before_releasing_writer_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _initialized_root(tmp_path)
    captured = capture_event(root, "Alice prefers tea.", source="test")
    assert captured.event_id is not None
    original_rollback = FileTransaction.rollback
    observed_lock = False

    def rollback_while_locked(self: FileTransaction, reason: Optional[str] = None) -> None:
        nonlocal observed_lock
        with pytest.raises(LockBusyError):
            KBWriteLock(self.root, "unexpected-writer").acquire()
        observed_lock = True
        original_rollback(self, reason)

    from kvault.core import reconciliation

    monkeypatch.setattr(FileTransaction, "rollback", rollback_while_locked)
    monkeypatch.setattr(
        reconciliation,
        "_apply_staged_to_live",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected failure")),
    )

    result = apply_reconciliation(root, _create_plan(root, captured.event_id))

    assert not result.success and result.rollback_performed
    assert observed_lock


def test_success_result_is_commit_authority_when_cleanup_is_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _initialized_root(tmp_path)
    captured = capture_event(root, "Alice prefers tea.", source="test")
    assert captured.event_id is not None
    original_commit = FileTransaction.commit

    def interrupted_commit(self: FileTransaction) -> None:
        raise OSError("injected cleanup interruption")

    monkeypatch.setattr(FileTransaction, "commit", interrupted_commit)
    applied = apply_reconciliation(root, _create_plan(root, captured.event_id))

    assert applied.success
    assert applied.validation["transaction_finalization_pending"] is True
    assert (root / "people" / "friends" / "alice" / "_summary.md").is_file()
    transaction = FileTransaction(root, applied.reconciliation_id)
    assert transaction.state["status"] == "applying"

    monkeypatch.setattr(FileTransaction, "commit", original_commit)
    recovered = recover_reconciliations(root)
    assert recovered["recovered"] == [
        {"reconciliation_id": applied.reconciliation_id, "action": "finalized"}
    ]
    assert FileTransaction(root, applied.reconciliation_id).state["status"] == "committed"


def test_move_and_delete_are_review_gated_and_transactional(tmp_path: Path) -> None:
    root = _initialized_root(tmp_path)
    create = capture_event(root, "Alice prefers tea.", source="test")
    assert create.event_id is not None
    assert apply_reconciliation(root, _create_plan(root, create.event_id)).success

    move_event = capture_event(root, "Alice is now a professional contact.", source="test")
    assert move_event.event_id is not None
    source = "people/friends/alice"
    target = "people/contacts/alice"
    ancestors = ["people/friends", "people/contacts", "people", "."]
    prepared = prepare_reconciliation(root, [move_event.event_id], paths=[source, *ancestors])
    move_plan = {
        "schema_version": 1,
        "event_ids": [move_event.event_id],
        "decisions": [
            {
                "event_id": move_event.event_id,
                "outcome": "apply",
                "reasoning": "The relationship classification changed.",
                "target_paths": [source, target],
            }
        ],
        "mutations": [
            {
                "operation": "move",
                "path": target,
                "source_path": source,
                "expected_source_revision": prepared["revisions"][source],
            },
            *_summary_mutations(
                root,
                prepared["revisions"],
                ancestors,
                "- Alice moved to professional contacts.",
            ),
        ],
        "reasoning": "Move the node and refresh both ancestor chains.",
    }

    move_review = apply_reconciliation(root, move_plan)
    assert move_review.status == "needs_review"
    assert (root / source).is_dir()
    moved = approve_reconciliation(root, move_review.reconciliation_id, "owner")
    assert moved.success
    assert not (root / source).exists()
    assert (root / target / "_summary.md").is_file()
    moved_meta, _body = parse_frontmatter((root / target / "_summary.md").read_text())
    assert f"journal:{move_event.event_id}" in moved_meta["source_refs"]

    delete_event = capture_event(root, "Remove Alice from current durable state.", source="test")
    assert delete_event.event_id is not None
    delete_ancestors = ["people/contacts", "people", "."]
    delete_prepared = prepare_reconciliation(
        root,
        [delete_event.event_id],
        paths=[target, *delete_ancestors],
    )
    delete_plan = {
        "schema_version": 1,
        "event_ids": [delete_event.event_id],
        "decisions": [
            {
                "event_id": delete_event.event_id,
                "outcome": "apply",
                "reasoning": "Explicit owner-requested removal.",
                "target_paths": [target],
            }
        ],
        "mutations": [
            {
                "operation": "delete",
                "path": target,
                "expected_revision": delete_prepared["revisions"][target],
            },
            *_summary_mutations(
                root,
                delete_prepared["revisions"],
                delete_ancestors,
                "- Alice removed from current contacts.",
            ),
        ],
        "reasoning": "Delete the node and refresh ancestors.",
    }

    delete_review = apply_reconciliation(root, delete_plan)
    assert delete_review.status == "needs_review"
    deleted = approve_reconciliation(root, delete_review.reconciliation_id, "owner")
    assert deleted.success
    assert not (root / target).exists()
    assert audit_kb(root)["valid"] is True


def test_merge_refuses_to_discard_source_attachments(tmp_path: Path) -> None:
    root = _initialized_root(tmp_path)
    alice_event = capture_event(root, "Alice prefers tea.", source="test")
    bob_event = capture_event(root, "Bob prefers coffee.", source="test")
    assert alice_event.event_id and bob_event.event_id
    assert apply_reconciliation(root, _create_plan(root, alice_event.event_id)).success
    assert apply_reconciliation(
        root,
        _create_plan(root, bob_event.event_id, target="people/friends/bob", name="Bob"),
    ).success

    source = "people/friends/alice"
    target = "people/friends/bob"
    attachment = root / source / "notes.txt"
    attachment.write_text("Must not be discarded.\n", encoding="utf-8")
    merge_event = capture_event(root, "Alice and Bob records may be the same.", source="test")
    assert merge_event.event_id is not None
    ancestors = ["people/friends", "people", "."]
    prepared = prepare_reconciliation(
        root,
        [merge_event.event_id],
        paths=[source, target, *ancestors],
    )
    target_node = ops.read_node(root, target, parents="none")
    assert target_node is not None
    plan = {
        "schema_version": 1,
        "event_ids": [merge_event.event_id],
        "decisions": [
            {
                "event_id": merge_event.event_id,
                "outcome": "apply",
                "reasoning": "A human must review the possible identity merge.",
                "target_paths": [source, target],
            }
        ],
        "mutations": [
            {
                "operation": "merge",
                "path": target,
                "source_path": source,
                "content": target_node["content"].rstrip() + "\n\nPossible Alice alias.\n",
                "expected_revision": prepared["revisions"][target],
                "expected_source_revision": prepared["revisions"][source],
            },
            *_summary_mutations(
                root,
                prepared["revisions"],
                ancestors,
                "- Alice may merge into Bob after review.",
            ),
        ],
        "reasoning": "Attempt a reviewed identity merge.",
    }

    review = apply_reconciliation(root, plan)
    assert review.status == "needs_review"
    failed = approve_reconciliation(root, review.reconciliation_id, "owner")

    assert not failed.success
    assert failed.error is not None and "merge_source_not_leaf" in failed.error
    assert attachment.read_text(encoding="utf-8") == "Must not be discarded.\n"
    assert (root / source / "_summary.md").is_file()
