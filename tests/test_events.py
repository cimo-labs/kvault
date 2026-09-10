"""Capture journal: capture, resolve, write --event promotion, import, check."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import events as ev
from kvault.core import operations as ops


@pytest.fixture
def kb(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    (root / "_summary.md").write_text("# Root\n")
    return root


def _capture(kb, body="Alice moved to Larkspur.", source="conversation", **kwargs):
    result = ev.capture_event(kb, body=body, source=source, **kwargs)
    assert result["success"], result
    return result


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


# Residue produced by `echo "…" | kvault capture` under zsh (Moss's shell), where
# any $<digits> run is expanded: $1,208.25 -> ,208.25 ; $5.00 -> .00 ; $0.00 -> /bin/zsh.00
MANGLED = [
    "Gusto AutoPilot payroll will debit ,208.25 from the account.",
    "a /bin/zsh.00 debit scheduled for Friday",
    "(including a .00 fee)",
    "wire of ,000.00 ACH landed",
    "PATH problem: -zsh reported no such command",
    "a zsh.00 debit scheduled",  # `$0.00` under `zsh -c` (argv[0] is bare)
    "a bash.00 debit",
]
LEGITIMATE = [
    "$1,208.25 debit scheduled",  # the quoted-heredoc form: intact
    "1,208.25 in the ledger",
    "budget 3,000-4,000 per month",
    "p < .05 and r = .95 in the pilot",
    "shipped v2.00 of the tool",
    "friction coefficient 0.45",
    "one-off ~ .50 rounding in the spreadsheet",
    "upgrade to zsh 5.9 and bash 5.2; see zsh.org",
]
# Documented negatives: no residue is left, so nothing can catch these. The
# quoted heredoc is the control; this table exists so nobody loosens the regex
# chasing them.
UNDETECTABLE = [
    "Paid  for the deposit",  # was "Paid $250 for the deposit"
    "rate is /hr",  # was "rate is $10/hr"
]


@pytest.mark.parametrize("body", MANGLED)
def test_capture_rejects_shell_mangled_body(kb, body):
    result = ev.capture_event(kb, body=body, source="conversation")
    assert not result["success"], body
    assert result["error_code"] == "validation_error"
    assert "shell-mangled" in result["error"]
    assert result["details"]["matches"]
    assert "heredoc" in result["hint"]
    assert ev.list_events(kb)["count"] == 0  # nothing written


@pytest.mark.parametrize("body", LEGITIMATE + UNDETECTABLE)
def test_capture_accepts_legitimate_text(kb, body):
    result = ev.capture_event(kb, body=body, source="conversation")
    assert result["success"], (body, result)


def test_capture_allow_suspicious_bypasses_the_tripwire(kb):
    result = ev.capture_event(kb, body=MANGLED[0], source="conversation", allow_suspicious=True)
    assert result["success"] and result["created"]


def test_suspicious_text_matches_labels_and_context():
    matches = ev.suspicious_text_matches(
        "debit ,208.25 and a /bin/zsh.00 fee and .00 more and a zsh.00 charge"
    )
    labels = [m["label"] for m in matches]
    assert labels == ["shell_path", "shell_name_cents", "orphan_thousands", "orphan_cents"]
    assert matches[2]["match"] == ",208.25"
    assert "debit" in matches[2]["context"]


def test_import_moss_capture_tolerates_mangled_records(kb, tmp_path):
    queue = tmp_path / "inbox.jsonl"
    queue.write_text(
        json.dumps(
            {"id": "r1", "ts": "2026-08-01T00:00:00Z", "source": "telegram", "text": MANGLED[0]}
        )
        + "\n"
    )
    result = ev.import_moss_capture(kb, input_path=queue)
    assert result["success"]
    assert result["counts"]["open"] == 1 and result["counts"]["conflict"] == 0
    assert ev.list_events(kb)["count"] == 1


def test_cli_capture_rejects_mangled_body_with_one_json_document(kb):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--kb-root", str(kb), "--json", "capture", "--source", "conversation"],
        input=MANGLED[0],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["error_code"] == "validation_error"

    forced = runner.invoke(
        cli,
        [
            "--kb-root",
            str(kb),
            "--json",
            "capture",
            "--source",
            "conversation",
            "--allow-suspicious",
        ],
        input=MANGLED[0],
    )
    assert forced.exit_code == 0, forced.output
    assert json.loads(forced.output)["created"] is True


def test_capture_creates_pending_event(kb):
    result = _capture(kb, source_ref="msg:123", tags=["family"])
    assert result["created"] and result["status"] == "pending"

    shown = ev.get_event(kb, result["event_id"])["event"]
    assert shown["body"] == "Alice moved to Larkspur."
    assert shown["source_ref"] == "msg:123"
    assert shown["tags"] == ["family"]
    assert (kb / ".kvault" / "events").is_dir()


def test_capture_is_idempotent(kb):
    first = _capture(kb, source_ref="msg:123")
    second = _capture(kb, source_ref="msg:123")
    assert not second["created"]
    assert second["event_id"] == first["event_id"]
    assert ev.list_events(kb)["count"] == 1


def test_capture_same_ref_different_content_conflicts(kb):
    _capture(kb, source_ref="msg:123")
    result = ev.capture_event(
        kb, body="Something else entirely.", source="conversation", source_ref="msg:123"
    )
    assert not result["success"]
    assert "different content" in result["error"]


def test_capture_without_ref_dedupes_by_content(kb):
    first = _capture(kb)
    second = _capture(kb)
    assert not second["created"]
    assert second["event_id"] == first["event_id"]


def test_capture_rejects_empty_body_or_source(kb):
    assert not ev.capture_event(kb, body="  ", source="x")["success"]
    assert not ev.capture_event(kb, body="text", source=" ")["success"]


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def test_resolve_pending_event(kb):
    event_id = _capture(kb)["event_id"]
    result = ev.resolve_event(kb, event_id, outcome="no_op", note="already represented")
    assert result["success"]
    shown = ev.get_event(kb, event_id)["event"]
    assert shown["status"] == "resolved"
    assert shown["resolution"]["outcome"] == "no_op"


def test_resolve_twice_fails(kb):
    event_id = _capture(kb)["event_id"]
    ev.resolve_event(kb, event_id, outcome="rejected")
    result = ev.resolve_event(kb, event_id, outcome="no_op")
    assert not result["success"]


def test_resolve_unknown_outcome_or_event(kb):
    event_id = _capture(kb)["event_id"]
    assert not ev.resolve_event(kb, event_id, outcome="banana")["success"]
    assert not ev.resolve_event(kb, "ev000000000000", outcome="no_op")["success"]


# ---------------------------------------------------------------------------
# write --event promotion
# ---------------------------------------------------------------------------


def test_write_with_event_stamps_provenance_and_resolves(kb):
    event_id = _capture(kb)["event_id"]
    result = ops.write_entity(
        kb,
        "people/alice",
        "# Alice\n\nLives in Larkspur.\n",
        create=True,
        event_ids=[event_id],
    )
    assert result["success"], result
    assert result["events"]["promoted"] == [event_id]

    node = ops.read_node(kb, "people/alice")
    assert f"journal:{event_id}" in node["meta"]["source_refs"]

    shown = ev.get_event(kb, event_id)["event"]
    assert shown["resolution"] == {
        "outcome": "promoted",
        "target_paths": ["people/alice"],
        "resolved_at": shown["resolution"]["resolved_at"],
    }


def test_write_with_nonpending_event_fails_fast(kb):
    event_id = _capture(kb)["event_id"]
    ev.resolve_event(kb, event_id, outcome="rejected")
    result = ops.write_entity(kb, "people/alice", "# Alice\n", create=True, event_ids=[event_id])
    assert not result["success"]
    assert not (kb / "people" / "alice").exists()  # node untouched


def test_write_with_unknown_event_fails_fast(kb):
    result = ops.write_entity(
        kb, "people/alice", "# Alice\n", create=True, event_ids=["ev000000000000"]
    )
    assert not result["success"]
    assert not (kb / "people" / "alice").exists()


def test_promoted_event_retry_appends_target(kb):
    event_id = _capture(kb)["event_id"]
    first = ops.write_entity(kb, "people/alice", "# Alice\n", create=True, event_ids=[event_id])
    assert first["success"]
    # Idempotent retry on the same node, plus a second node from the same event.
    retry = ops.write_entity(kb, "people/alice", "# Alice\n\nMore.\n", event_ids=[event_id])
    assert retry["success"], retry
    second = ops.write_entity(
        kb, "projects/larkspur_move", "# Move\n", create=True, new_root=True, event_ids=[event_id]
    )
    assert second["success"], second

    shown = ev.get_event(kb, event_id)["event"]
    assert shown["resolution"]["target_paths"] == ["people/alice", "projects/larkspur_move"]


def test_write_multiple_events_resolve_to_one_node(kb):
    id_a = _capture(kb, body="Fact A.")["event_id"]
    id_b = _capture(kb, body="Fact B.")["event_id"]
    result = ops.write_entity(kb, "people/alice", "# Alice\n", create=True, event_ids=[id_a, id_b])
    assert result["success"]
    node = ops.read_node(kb, "people/alice")
    assert {f"journal:{id_a}", f"journal:{id_b}"} <= set(node["meta"]["source_refs"])
    for event_id in (id_a, id_b):
        assert ev.get_event(kb, event_id)["event"]["resolution"]["outcome"] == "promoted"


# ---------------------------------------------------------------------------
# pending findings / check
# ---------------------------------------------------------------------------


def test_pending_findings_flag_old_events(kb):
    fresh = _capture(kb, body="Fresh fact.")["event_id"]
    stale = ev.capture_event(
        kb, body="Old fact.", source="conversation", captured_at="2026-01-01T00:00:00Z"
    )
    assert stale["success"]

    findings = ev.pending_event_findings(kb, max_age_days=7)
    ids = [f["event_id"] for f in findings]
    assert stale["event_id"] in ids
    assert fresh not in ids


def test_check_reports_pending_events(kb):
    ev.capture_event(
        kb, body="Old fact.", source="conversation", captured_at="2026-01-01T00:00:00Z"
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["--kb-root", str(kb), "check", "--json"])
    payload = json.loads(result.output)
    assert payload["pending_event_count"] == 1
    assert payload["success"] is True  # warn-only

    text = runner.invoke(cli, ["--kb-root", str(kb), "check"])
    assert "PENDING:" in text.output
    assert text.exit_code == 0


# ---------------------------------------------------------------------------
# CLI round trip
# ---------------------------------------------------------------------------


def test_cli_capture_write_event_roundtrip(kb):
    runner = CliRunner()
    captured = runner.invoke(
        cli,
        ["--kb-root", str(kb), "--json", "capture", "--source", "conversation"],
        input="Alice moved to Larkspur.\n",
    )
    payload = json.loads(captured.output)
    assert payload["success"], captured.output
    event_id = payload["event_id"]

    written = runner.invoke(
        cli,
        ["--kb-root", str(kb), "--json", "write", "people/alice", "--create", "--event", event_id],
        input="# Alice\n\nLives in Larkspur.\n",
    )
    result = json.loads(written.output)
    assert result["success"], written.output
    assert result["events"]["promoted"] == [event_id]

    listed = runner.invoke(cli, ["--kb-root", str(kb), "--json", "events", "list"])
    events = json.loads(listed.output)["events"]
    assert events[0]["status"] == "resolved"


# ---------------------------------------------------------------------------
# moss-capture import
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, records) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def test_import_moss_capture(kb, tmp_path):
    active = _write_jsonl(
        tmp_path / "kb-inbox.jsonl",
        [
            {
                "id": "rec-open",
                "ts": "2026-07-18T10:00:00Z",
                "source": "telegram",
                "tags": ["cje"],
                "text": "SAJA cites the CJE paper.",
                "status": "new",
            }
        ],
    )
    processed = _write_jsonl(
        tmp_path / "kb-inbox.processed.jsonl",
        [
            {
                "id": "rec-archived",
                "ts": "2026-07-01T10:00:00Z",
                "source": "telegram",
                "tags": [],
                "text": "Old archived fact.",
                "status": "archived",
                "archived_ts": "2026-07-02T10:00:00Z",
            },
            {"id": "", "text": "invalid record"},
        ],
    )

    dry = ev.import_moss_capture(kb, active, processed, dry_run=True)
    assert dry["counts"] == {
        "open": 1,
        "archived": 1,
        "invalid": 1,
        "duplicate": 0,
        "conflict": 0,
    }
    assert ev.list_events(kb)["count"] == 0  # dry run imported nothing

    applied = ev.import_moss_capture(kb, active, processed)
    assert len(applied["imported"]) == 2

    pending = ev.list_events(kb, status="pending")["events"]
    assert len(pending) == 1
    assert pending[0]["source_ref"] == "moss-inbox:rec-open"

    resolved = ev.list_events(kb, status="resolved")["events"]
    assert len(resolved) == 1
    assert resolved[0]["resolution"]["outcome"] == "journal_only"
    assert "legacy_archived_unknown" in resolved[0]["resolution"]["note"]


def test_import_is_repeat_safe(kb, tmp_path):
    active = _write_jsonl(
        tmp_path / "kb-inbox.jsonl",
        [
            {
                "id": "rec-1",
                "ts": "2026-07-18T10:00:00Z",
                "source": "telegram",
                "text": "A fact.",
                "status": "new",
            }
        ],
    )
    first = ev.import_moss_capture(kb, active)
    assert len(first["imported"]) == 1
    second = ev.import_moss_capture(kb, active)
    assert second["imported"] == []
    assert second["counts"]["duplicate"] == 1
    assert ev.list_events(kb)["count"] == 1


def test_import_record_in_both_files_treated_archived(kb, tmp_path):
    record = {
        "id": "rec-both",
        "ts": "2026-07-18T10:00:00Z",
        "source": "telegram",
        "text": "Queued then archived.",
        "status": "new",
    }
    active = _write_jsonl(tmp_path / "in.jsonl", [record])
    processed = _write_jsonl(tmp_path / "done.jsonl", [{**record, "status": "archived"}])
    ev.import_moss_capture(kb, active, processed)
    events = ev.list_events(kb)["events"]
    assert len(events) == 1
    assert events[0]["status"] == "resolved"


# ---------------------------------------------------------------------------
# retract (0.14.0)
# ---------------------------------------------------------------------------


def _promote(kb, event_id, path="people/alice", body="# Alice\n\nLives in Larkspur.\n"):
    # reasoning= auto-journals the write, so check's LOG: hard warning stays quiet.
    result = ops.write_node(kb, path, body, create=True, event_ids=[event_id], reasoning="test")
    assert result["success"], result
    return result


def test_retract_pending_and_promoted_events(kb):
    pending = _capture(kb, body="Pending fact.")["event_id"]
    promoted = _capture(kb, body="Promoted fact.")["event_id"]
    _promote(kb, promoted)

    r1 = ev.retract_event(kb, pending, reason="shell-mangled amount")
    assert r1["success"] and r1["resolution"]["outcome"] == "retracted"
    assert "previous" not in r1["resolution"]
    assert ev.get_event(kb, pending)["event"]["status"] == "resolved"

    r2 = ev.retract_event(kb, promoted, reason="shell-mangled amount")
    assert r2["success"]
    assert r2["resolution"]["previous"]["outcome"] == "promoted"
    assert r2["resolution"]["previous"]["target_paths"] == ["people/alice"]

    listed = ev.list_events(kb, status="retracted")["events"]
    assert {e["id"] for e in listed} == {pending, promoted}
    assert ev.list_events(kb, status="resolved")["count"] == 0
    assert ev.list_events(kb, status="pending")["count"] == 0


def test_retract_twice_fails_unless_amending_supersession(kb):
    event_id = _capture(kb, body="Wrong.")["event_id"]
    first = ev.retract_event(kb, event_id, reason="x")
    assert first["success"]
    again = ev.retract_event(kb, event_id, reason="x")
    assert not again["success"] and again["error_code"] == "workflow_error"
    fixed = _capture(kb, body="Right.")["event_id"]
    amended = ev.retract_event(kb, event_id, reason="x (corrected)", superseded_by=fixed)
    assert amended["success"]
    assert amended["resolution"]["superseded_by"] == fixed
    assert amended["resolution"]["retracted_at"] == first["resolution"]["retracted_at"]
    other = _capture(kb, body="Another wrong.")["event_id"]
    missing = ev.retract_event(kb, other, reason="x", superseded_by="evdoesnotexist")
    assert not missing["success"] and missing["error_code"] == "not_found"
    self_ref = ev.retract_event(kb, other, reason="x", superseded_by=other)
    assert not self_ref["success"] and self_ref["error_code"] == "validation_error"
    no_reason = ev.retract_event(kb, other, reason="  ")
    assert not no_reason["success"]


def test_resolve_rejects_retracted_outcome(kb):
    event_id = _capture(kb, body="Fact.")["event_id"]
    result = ev.resolve_event(kb, event_id, outcome="retracted")
    assert not result["success"]
    assert "events retract" in result["error"]


def test_write_with_retracted_event_fails_fast_and_keeps_resolution(kb):
    event_id = _capture(kb, body="Wrong amount ,208.25", source="t", allow_suspicious=True)[
        "event_id"
    ]
    retracted = ev.retract_event(kb, event_id, reason="mangled")
    before = ev.get_event(kb, event_id)["event"]["resolution"]
    result = ops.write_node(kb, "people/bob", "# Bob\n\nx.\n", create=True, event_ids=[event_id])
    assert not result["success"] and result["error_code"] == "workflow_error"
    assert "retracted" in result["error"]
    assert not (kb / "people" / "bob").exists()  # nothing written
    assert ev.get_event(kb, event_id)["event"]["resolution"] == before == retracted["resolution"]
    promote = ev.promote_events(kb, [event_id], "people/bob")
    assert not promote["success"]


def test_check_reports_retracted_refs_and_supersession_clears_them(kb):
    bad = _capture(kb, body="Gusto debit ,208.25", source="t", allow_suspicious=True)["event_id"]
    _promote(kb, bad, path="people/self", body="# Self\n\nGusto debit 1208.25.\n")
    good = _capture(kb, body="Gusto debit $1,208.25", source="t")["event_id"]
    assert ev.retract_event(kb, bad, reason="shell-mangled amount", superseded_by=good)["success"]

    runner = CliRunner()
    as_json = runner.invoke(cli, ["--kb-root", str(kb), "check", "--json"])
    payload = json.loads(as_json.output)
    assert payload["retracted_ref_count"] == 1
    assert payload["retracted_refs"][0] == {
        "type": "retracted_ref",
        "path": "people/self",
        "event_id": bad,
        "reason": "shell-mangled amount",
        "superseded_by": good,
    }
    text = runner.invoke(cli, ["--kb-root", str(kb), "check"])
    assert text.exit_code == 0  # warn-only
    assert f"RETRACTED: people/self cites retracted {bad}" in text.output
    assert f"write --event {good}" in text.output

    # Close-out: rewrite the node with the superseding event -> finding clears.
    fixed = ops.write_node(
        kb, "people/self", "# Self\n\nGusto debit $1,208.25.\n", event_ids=[good], reasoning="fix"
    )
    assert fixed["success"]
    after = json.loads(runner.invoke(cli, ["--kb-root", str(kb), "check", "--json"]).output)
    assert after["retracted_ref_count"] == 0


def test_retracted_finding_clears_via_fresh_capture_without_superseded_by(kb):
    """The printed close-out ('rewrite, then write --event <corrected capture>') must work
    even when the retraction did not name a successor: re-linking drops the retracted ref."""
    bad = _capture(kb, body="Wrong amount.", source="t")["event_id"]
    _promote(kb, bad, path="people/self", body="# Self\n\nWrong amount.\n")
    assert ev.retract_event(kb, bad, reason="mangled")["success"]
    runner = CliRunner()
    assert (
        json.loads(runner.invoke(cli, ["--kb-root", str(kb), "check", "--json"]).output)[
            "retracted_ref_count"
        ]
        == 1
    )

    good = _capture(kb, body="Right amount.", source="t")["event_id"]
    fixed = ops.write_node(
        kb, "people/self", "# Self\n\nRight amount.\n", event_ids=[good], reasoning="fix"
    )
    assert fixed["success"]
    removed = next(n for n in fixed["notes"] if n["code"] == "removed")
    assert removed["detail"]["retracted_refs_dropped"] == [bad]
    refs = ops.read_node(kb, "people/self", parents="none")["meta"]["source_refs"]
    assert refs == [f"journal:{good}"]
    assert (
        json.loads(runner.invoke(cli, ["--kb-root", str(kb), "check", "--json"]).output)[
            "retracted_ref_count"
        ]
        == 0
    )

    # And the human RETRACTED: line is tier-invariant like every other check line.
    other = _capture(kb, body="Also wrong.", source="t")["event_id"]
    _promote(kb, other, path="people/other", body="# Other\n\nx.\n")
    ev.retract_event(kb, other, reason="mangled too")
    baseline = runner.invoke(cli, ["--kb-root", str(kb), "check"])
    assert "RETRACTED: people/other" in baseline.output
    for tier in (["-q"], ["--explain"], ["--trace"]):
        assert runner.invoke(cli, ["--kb-root", str(kb), *tier, "check"]).output == baseline.output


def test_check_retracted_fast_path_without_retractions(kb):
    _promote(kb, _capture(kb, body="Fine.")["event_id"])
    assert ev.retracted_reference_findings(kb) == []


def test_cli_events_retract_json(kb):
    event_id = _capture(kb, body="Wrong.")["event_id"]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--kb-root", str(kb), "--json", "events", "retract", event_id, "--reason", "mangled"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["success"] and payload["did"] == f"retracted {event_id}"
    listed = runner.invoke(
        cli, ["--kb-root", str(kb), "--json", "events", "list", "--status", "retracted"]
    )
    assert json.loads(listed.output)["count"] == 1
    again = runner.invoke(
        cli, ["--kb-root", str(kb), "--json", "events", "retract", event_id, "--reason", "x"]
    )
    assert again.exit_code == 1
    json.loads(again.output)


# ---------------------------------------------------------------------------
# events list --limit / --since (0.14.0)
# ---------------------------------------------------------------------------


def test_events_list_limit_and_since(kb):
    for day in range(1, 8):
        ev.capture_event(
            kb, body=f"Fact {day}.", source="t", captured_at=f"2026-03-{day:02d}T00:00:00Z"
        )
    everything = ev.list_events(kb)
    assert everything["count"] == everything["total_matched"] == 7 and "notes" not in everything

    page = ev.list_events(kb, limit=3)
    assert page["count"] == 3 and page["total_matched"] == 7 and page["limit"] == 3
    assert [e["captured_at"][:10] for e in page["events"]] == [
        "2026-03-07",
        "2026-03-06",
        "2026-03-05",
    ]
    note = page["notes"][0]
    assert note["code"] == "truncated" and note["next"].endswith("--limit 0")

    recent = ev.list_events(kb, since="2026-03-06")
    assert [e["captured_at"][:10] for e in recent["events"]] == ["2026-03-07", "2026-03-06"]
    assert ev.list_events(kb, limit=0)["count"] == 7  # 0 = all

    bad = ev.list_events(kb, since="yesterday")
    assert not bad["success"] and bad["error_code"] == "validation_error"
    neg = ev.list_events(kb, limit=-1)
    assert not neg["success"] and "limit must be >= 0" in neg["error"]

    # check sees every pending event regardless of any default page size.
    assert len(ev.pending_event_findings(kb, max_age_days=0)) == 7


def test_cli_events_list_default_page_and_since_error(kb):
    for i in range(60):
        ev.capture_event(
            kb, body=f"Fact {i}.", source="t", captured_at=f"2026-01-01T00:{i:02d}:00Z"
        )
    runner = CliRunner()
    listed = json.loads(
        runner.invoke(cli, ["--kb-root", str(kb), "--json", "events", "list"]).output
    )
    assert listed["count"] == 50 and listed["total_matched"] == 60
    assert listed["notes"][0]["code"] == "truncated"
    human = runner.invoke(cli, ["--kb-root", str(kb), "events", "list"])
    assert "(showing 50 of 60" in human.output
    everything = json.loads(
        runner.invoke(
            cli, ["--kb-root", str(kb), "--json", "events", "list", "--limit", "0"]
        ).output
    )
    assert everything["count"] == 60
    bad = runner.invoke(cli, ["--kb-root", str(kb), "--json", "events", "list", "--since", "bogus"])
    assert bad.exit_code == 1
    assert json.loads(bad.output)["error_code"] == "validation_error"  # still one document
