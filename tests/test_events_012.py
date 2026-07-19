"""Tests for immutable temporal events and reconciliation records."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from kvault.core.events import (
    EventFormatError,
    EventStatus,
    ImmutableRecordConflict,
    ReconciliationOutcome,
    capture_event,
    derive_event_states,
    get_event,
    list_events,
    read_reconciliation_plan,
    read_reconciliation_result,
    reconciliation_paths,
    write_reconciliation_plan,
    write_reconciliation_result,
)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    root.mkdir()
    (root / "_summary.md").write_text("# Test\n", encoding="utf-8")
    return root


def _capture(root: Path, reference: str, body: str = "exact body"):
    return capture_event(
        root,
        body,
        source="test",
        source_ref=reference,
        captured_at=datetime(2026, 7, 19, 12, 30, tzinfo=timezone.utc),
        tags=["memory", "memory", "  durable "],
    )


def test_capture_preserves_exact_body_and_frontmatter(tmp_path: Path) -> None:
    root = _root(tmp_path)
    body = "\n# Candidate\n\nUnicode: José\nno final newline"

    result = capture_event(
        root,
        body,
        source="telegram",
        source_ref="message:42",
        occurred_at=datetime(2026, 7, 18, 8, 0),
        captured_at=datetime(2026, 7, 19, 12, 30, tzinfo=timezone.utc),
        tags=["person", "person", " update "],
        sensitivity="sensitive",
    )

    assert result.success and result.created
    assert result.event is not None
    assert result.event.body == body
    assert result.event.tags == ["person", "update"]
    assert result.event.sensitivity.value == "sensitive"
    assert result.event.path.relative_to(root).parts[:4] == (
        "journal",
        "events",
        "2026",
        "07",
    )
    assert get_event(root, result.event.event_id) == result.event


def test_capture_content_alias_is_explicit_and_source_ref_is_idempotent(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    first = capture_event(root, content="same", source="mail", source_ref="m-1")
    replay = capture_event(root, content="same", source="mail", source_ref="m-1")
    conflict = capture_event(root, content="changed", source="mail", source_ref="m-1")

    assert first.status == "created"
    assert replay.status == "existing"
    assert replay.event_id == first.event_id
    assert conflict.status == "source_ref_conflict"
    assert conflict.error_code == "source_ref_conflict"
    assert len(list_events(root)) == 1
    with pytest.raises(TypeError):
        capture_event(root, "body", content="other", source="test")


def test_plan_and_result_are_append_only_and_derive_terminal_state(tmp_path: Path) -> None:
    root = _root(tmp_path)
    event = _capture(root, "terminal").event
    assert event is not None
    plan_payload = {
        "schema_version": 1,
        "event_ids": [event.event_id],
        "decisions": [{"event_id": event.event_id, "outcome": "no_op"}],
        "mutations": [],
        "reasoning": "Already known",
    }

    plan = write_reconciliation_plan(root, "rec_terminal", plan_payload)
    assert plan.plan == plan_payload
    assert read_reconciliation_plan(root, "rec_terminal") == plan
    assert derive_event_states(root)[event.event_id].state == EventStatus.RECONCILING

    result_payload = {
        "success": True,
        "status": "applied",
        "event_outcomes": {event.event_id: "no_op"},
    }
    result = write_reconciliation_result(root, "rec_terminal", result_payload)
    assert read_reconciliation_result(root, "rec_terminal") == result
    state = derive_event_states(root)[event.event_id]
    assert state.state == EventStatus.RESOLVED
    assert state.terminal_outcome == ReconciliationOutcome.NO_OP
    assert list_events(root, status="resolved") == [event]

    assert write_reconciliation_plan(root, "rec_terminal", plan_payload) == plan
    assert write_reconciliation_result(root, "rec_terminal", result_payload) == result
    with pytest.raises(ImmutableRecordConflict):
        write_reconciliation_plan(root, "rec_terminal", {**plan_payload, "reasoning": "new"})
    with pytest.raises(ImmutableRecordConflict):
        write_reconciliation_result(
            root,
            "rec_terminal",
            {**result_payload, "event_outcomes": {event.event_id: "duplicate"}},
        )

    result_raw = result.path.read_text(encoding="utf-8")
    result.path.write_text(result_raw.replace('"no_op"', '"duplicate"', 1), encoding="utf-8")
    with pytest.raises(EventFormatError, match="result body hash mismatch"):
        read_reconciliation_result(root, "rec_terminal")
    result.path.write_text(result_raw, encoding="utf-8")

    plan_raw = plan.path.read_text(encoding="utf-8")
    plan.path.write_text(
        plan_raw.replace("Already known", "Changed reasoning", 1), encoding="utf-8"
    )
    with pytest.raises(EventFormatError, match="plan body hash mismatch"):
        read_reconciliation_plan(root, "rec_terminal")


def test_review_and_failed_attempt_states_are_nonterminal(tmp_path: Path) -> None:
    root = _root(tmp_path)
    review = _capture(root, "review").event
    failed = _capture(root, "failed", "other").event
    assert review is not None and failed is not None

    write_reconciliation_plan(
        root,
        "rec_review",
        {"event_ids": [review.event_id], "review_required": True, "mutations": []},
    )
    write_reconciliation_plan(
        root,
        "rec_failed",
        {"event_ids": [failed.event_id], "review_required": False, "mutations": []},
    )
    write_reconciliation_result(
        root,
        "rec_failed",
        {"success": False, "status": "failed", "event_outcomes": {failed.event_id: "no_op"}},
    )

    states = derive_event_states(root)
    assert states[review.event_id].state == EventStatus.NEEDS_REVIEW
    assert states[failed.event_id].state == EventStatus.PENDING
    assert states[failed.event_id].terminal_outcome is None


def test_reconciliation_paths_are_canonical(tmp_path: Path) -> None:
    root = _root(tmp_path)
    paths = reconciliation_paths(
        root,
        "rec_paths",
        created_at=datetime(2025, 12, 31, tzinfo=timezone.utc),
    )
    assert paths.directory.relative_to(root) == Path("journal/reconciliations/2025/12/rec_paths")
    assert paths.plan_path.name == "plan.md"
    assert paths.result_path.name == "result.md"
