"""Tests for the decision signals core operations now report.

One test per note code path: it fires exactly when it should, and stays
silent otherwise. Also pins the two behaviour fixes that ride along: the
no-op write no longer rewrites the file (mtime stays put), and the legacy
``_meta.json`` cleanup still runs on the no-op fast path.
"""

import json
import time

from kvault.core import operations as ops


def _codes(result):
    return [n["code"] for n in result.get("notes") or []]


def _make_kb(tmp_path, name="kb"):
    kb = tmp_path / name
    kb.mkdir()
    (kb / ".kvault").mkdir()
    (kb / "_summary.md").write_text("# Test KB\n\nRoot.\n")
    (kb / "people").mkdir()
    (kb / "people" / "_summary.md").write_text("# People\n\nPeople.\n")
    (kb / "people" / "contacts").mkdir()
    (kb / "people" / "contacts" / "_summary.md").write_text("# Contacts\n\nContacts.\n")
    return kb


BODY = "# Jane Doe\n\nMet at a conference.\n"


class TestWriteNodeNarration:
    def test_create_reports_autofilled_source_aliases_and_name(self, tmp_path):
        kb = _make_kb(tmp_path)
        result = ops.write_node(kb, "people/contacts/jane_doe", BODY, create=True)
        assert result["success"] is True
        assert result["changed"] is True
        assert result["did"] == "created people/contacts/jane_doe"
        assert "autofilled" in _codes(result)
        assert result["meta_autofilled"] == {"source": True, "aliases": True, "name": True}
        auto = next(n for n in result["notes"] if n["code"] == "autofilled")
        assert auto["detail"]["source"] == "auto:cli"
        assert auto["detail"]["aliases"] == ["Jane Doe"]
        assert auto["detail"]["name"] == "Jane Doe"

    def test_caller_supplied_meta_emits_no_autofill_note(self, tmp_path):
        kb = _make_kb(tmp_path)
        result = ops.write_node(
            kb,
            "people/contacts/jane_doe",
            BODY,
            meta={"source": "manual", "aliases": ["Jane Doe"], "name": "Jane Doe"},
            create=True,
        )
        assert "autofilled" not in _codes(result)
        assert "meta_autofilled" not in result

    def test_noop_write_reports_unchanged_and_skips_the_rewrite(self, tmp_path):
        kb = _make_kb(tmp_path)
        ops.write_node(kb, "people/contacts/jane_doe", BODY, create=True)
        summary = kb / "people" / "contacts" / "jane_doe" / "_summary.md"
        mtime_before = summary.stat().st_mtime_ns
        time.sleep(0.02)

        result = ops.write_node(kb, "people/contacts/jane_doe", BODY, create=False)

        assert result["changed"] is False
        assert result["did"] == "no change to people/contacts/jane_doe"
        assert "unchanged" in _codes(result)
        # The file must NOT be rewritten: the byte-identical rewrite bumped
        # mtime, which manufactured spurious PROPAGATE warnings in check.
        assert summary.stat().st_mtime_ns == mtime_before
        # Semantics deliberately preserved: an earlier changed write may still
        # be unpropagated, so ancestors/propagation_required stay truthful.
        assert result["propagation_required"] is True
        assert len(result["ancestors"]) == 3

    def test_noop_write_still_removes_legacy_meta_json(self, tmp_path):
        kb = _make_kb(tmp_path)
        ops.write_node(kb, "people/contacts/jane_doe", BODY, create=True)
        node_dir = kb / "people" / "contacts" / "jane_doe"
        legacy = node_dir / "_meta.json"
        legacy.write_text(json.dumps({"source": "legacy"}))

        result = ops.write_node(kb, "people/contacts/jane_doe", BODY, create=False)

        assert not legacy.exists(), "no-op fast path must still delete legacy _meta.json"
        assert "removed" in _codes(result)

    def test_legacy_node_without_frontmatter_never_takes_the_noop_path(self, tmp_path):
        kb = _make_kb(tmp_path)
        node_dir = kb / "people" / "contacts" / "old_timer"
        node_dir.mkdir()
        (node_dir / "_summary.md").write_text("# Old Timer\n\nLegacy body.\n")
        (node_dir / "_meta.json").write_text(
            json.dumps({"source": "legacy", "aliases": ["Old Timer"]})
        )

        result = ops.write_node(
            kb, "people/contacts/old_timer", "# Old Timer\n\nLegacy body.\n", create=False
        )

        # The comparison could match, but taking the fast path would skip the
        # frontmatter migration AND delete _meta.json — destroying metadata.
        assert result["changed"] is True
        raw = (node_dir / "_summary.md").read_text()
        assert raw.startswith("---"), "legacy node must be migrated to frontmatter"
        assert not (node_dir / "_meta.json").exists()

    def test_changed_write_reports_no_unchanged_note(self, tmp_path):
        kb = _make_kb(tmp_path)
        ops.write_node(kb, "people/contacts/jane_doe", BODY, create=True)
        result = ops.write_node(
            kb, "people/contacts/jane_doe", BODY + "\nNew fact.\n", create=False
        )
        assert result["changed"] is True
        assert "unchanged" not in _codes(result)

    def test_failed_event_promotion_is_a_partial_note(self, tmp_path, monkeypatch):
        kb = _make_kb(tmp_path)
        from kvault.core import events as ev

        captured = ev.capture_event(kb, body="Jane fact", source="test")
        event_id = captured["event_id"]

        # Stage the real TOCTOU deterministically: the admission check passes,
        # then promotion fails (as when another process resolves the event
        # between the two).
        monkeypatch.setattr(
            ev,
            "promote_events",
            lambda *a, **k: {
                "success": False,
                "error_code": "workflow_error",
                "error": f"Event {event_id} was already resolved as duplicate",
            },
        )

        result = ops.write_node(
            kb, "people/contacts/jane_doe", BODY, create=True, event_ids=[event_id]
        )

        # The node write itself succeeded — and the half-failure is loud.
        assert result["success"] is True
        assert result["partial"] is True
        assert "event promotion failed" in result["did"]
        partial = next(n for n in result["notes"] if n["code"] == "partial")
        assert partial["detail"]["event_ids"] == [event_id]
        assert result["next"].startswith("kvault events show")
        assert result["events_warning"]
        assert (kb / "people" / "contacts" / "jane_doe" / "_summary.md").exists()

    def test_key_order_puts_decisions_before_ancestors(self, tmp_path):
        kb = _make_kb(tmp_path)
        result = ops.write_node(kb, "people/contacts/jane_doe", BODY, create=True)
        keys = list(result.keys())
        assert keys.index("did") < keys.index("ancestors")
        assert keys.index("notes") < keys.index("ancestors")
        assert keys.index("propagation_required") < keys.index("ancestors")
        assert keys.index("ancestor_paths") < keys.index("ancestors")
        assert result["ancestor_paths"] == [t["path"] for t in result["ancestors"]]


class TestWriteSummaryNarration:
    def test_creating_a_new_path_reports_created(self, tmp_path):
        kb = _make_kb(tmp_path)
        result = ops.write_summary(kb, "projects", "# Projects\n\nNew branch.\n")
        assert result["created"] is True
        assert result["did"] == "created summary projects"
        assert "created" in _codes(result)

    def test_updating_existing_path_is_quiet(self, tmp_path):
        kb = _make_kb(tmp_path)
        result = ops.write_summary(kb, "people", "# People\n\nUpdated rollup.\n")
        assert result["created"] is False
        assert "created" not in _codes(result)

    def test_meta_replacement_reports_dropped_keys(self, tmp_path):
        kb = _make_kb(tmp_path)
        ops.write_node(kb, "people/contacts/jane_doe", BODY, create=True)
        result = ops.write_summary(
            kb,
            "people/contacts/jane_doe",
            "# Jane\n\nReplaced.\n",
            meta={"source": "manual", "aliases": []},
        )
        removed = next(n for n in result["notes"] if n["code"] == "removed")
        assert "name" in removed["detail"]["dropped_keys"]

    def test_meta_none_preserves_frontmatter_and_stays_silent(self, tmp_path):
        kb = _make_kb(tmp_path)
        ops.write_node(kb, "people/contacts/jane_doe", BODY, create=True)
        result = ops.write_summary(kb, "people/contacts/jane_doe", "# Jane\n\nNew body.\n")
        assert "removed" not in _codes(result)
        raw = (kb / "people" / "contacts" / "jane_doe" / "_summary.md").read_text()
        assert "source: auto:cli" in raw


class TestBatchCollapse:
    def test_update_summaries_collapses_per_item_notes_and_counts(self, tmp_path):
        kb = _make_kb(tmp_path)
        updates = [{"path": f"projects/p{i}", "content": f"# P{i}\n\nBody.\n"} for i in range(5)]
        updates.append({"path": "people", "content": "# People\n\nRollup.\n"})
        result = ops.update_summaries(kb, updates)
        assert result["success"] is True
        assert result["attempted"] == 6
        assert result["failed"] == 0
        assert "partial" not in result
        created = next(n for n in result["notes"] if n["code"] == "created")
        assert created["count"] == 5
        assert len(created["examples"]) == 3

    def test_partial_batch_reports_partial_and_failed(self, tmp_path):
        kb = _make_kb(tmp_path)
        updates = [
            {"path": "people", "content": "# People\n\nGood.\n"},
            {"path": "people", "content": None},  # missing content
            {"path": None, "content": "orphan"},  # missing path
        ]
        result = ops.update_summaries(kb, updates)
        assert result["success"] is True  # unchanged batch semantics
        assert result["partial"] is True
        assert result["failed"] == 2
        assert "partial" in _codes(result)


class TestDeleteAndMoveNarration:
    def test_delete_reports_subtree_counts_and_stale_ancestors(self, tmp_path):
        kb = _make_kb(tmp_path)
        for name in ("a", "b"):
            ops.write_node(kb, f"people/contacts/{name}", f"# {name}\n\nx.\n", create=True)
        result = ops.delete_entity(kb, "people/contacts")
        assert result["nodes_deleted"] == 3  # contacts + a + b
        assert result["files_deleted"] >= 3
        assert result["propagation_required"] is True
        assert result["ancestor_paths"] == ["people", "."]
        assert "removed" in _codes(result)
        assert "propagate" in _codes(result)

    def test_move_reports_both_stale_chains(self, tmp_path):
        kb = _make_kb(tmp_path)
        ops.write_node(kb, "people/contacts/jane_doe", BODY, create=True)
        (kb / "projects").mkdir()
        (kb / "projects" / "_summary.md").write_text("# Projects\n\nProjects.\n")
        result = ops.move_entity(kb, "people/contacts/jane_doe", "projects/jane_doe")
        assert result["nodes_moved"] == 1
        assert result["ancestors_source"] == ["people/contacts", "people", "."]
        assert result["ancestors_target"] == ["projects", "."]
        # Deduped union, source order first.
        assert result["ancestor_paths"] == ["people/contacts", "people", ".", "projects"]
        assert "propagate" in _codes(result)


class TestJournalGuessedDate:
    def test_bad_date_reports_guessed(self, tmp_path):
        kb = _make_kb(tmp_path)
        result = ops.write_journal(
            kb, actions=[{"action_type": "update", "path": "people"}], source="t", date="not-a-date"
        )
        assert result["success"] is True
        guessed = next(n for n in result["notes"] if n["code"] == "guessed")
        assert guessed["detail"]["input"] == "not-a-date"

    def test_good_date_is_silent(self, tmp_path):
        kb = _make_kb(tmp_path)
        result = ops.write_journal(
            kb, actions=[{"action_type": "update", "path": "people"}], source="t", date="2026-01-05"
        )
        assert "notes" not in result


class TestSearchNarration:
    def test_limit_truncation_reports_total_matched(self, tmp_path):
        kb = _make_kb(tmp_path)
        for i in range(4):
            ops.write_node(
                kb, f"people/contacts/pers{i}", f"# Pers{i}\n\nwidget fact.\n", create=True
            )
        result = ops.search_nodes(kb, "widget", limit=2)
        assert result["count"] == 2
        assert result["total_matched"] == 4
        truncated = next(n for n in result["notes"] if n["code"] == "truncated")
        assert truncated["detail"]["total_matched"] == 4

    def test_budget_exhaustion_reports_reason_per_result(self, tmp_path):
        kb = _make_kb(tmp_path)
        long_body = "# Long\n\n" + ("widget " * 400)
        for i in range(3):
            ops.write_node(kb, f"people/contacts/lg{i}", long_body, create=True)
        result = ops.search_nodes(
            kb,
            "widget",
            limit=3,
            include_content=True,
            content_max_chars=2000,
            total_max_chars=2500,
        )
        reasons = [r.get("content_omitted_reason") for r in result["results"]]
        assert "total_budget_exhausted" in reasons
        assert result["budget"]["exhausted"] is True
        assert any(n["code"] == "truncated" and "budget" in n["text"] for n in result["notes"])

    def test_per_result_cap_is_distinguished_from_budget(self, tmp_path):
        kb = _make_kb(tmp_path)
        long_body = "# Long\n\n" + ("widget " * 400)
        ops.write_node(kb, "people/contacts/lg", long_body, create=True)
        result = ops.search_nodes(
            kb,
            "widget",
            limit=1,
            include_content=True,
            content_max_chars=100,
            total_max_chars=20000,
        )
        assert result["results"][0]["content_omitted_reason"] == "content_max_chars"
        assert result["budget"]["exhausted"] is False

    def test_undecodable_summary_is_skipped_not_fatal(self, tmp_path):
        kb = _make_kb(tmp_path)
        ops.write_node(kb, "people/contacts/ok", "# Ok\n\nwidget.\n", create=True)
        bad = kb / "people" / "contacts" / "bad"
        bad.mkdir()
        (bad / "_summary.md").write_bytes("# Café\n\nwidget.\n".encode("latin-1"))

        result = ops.search_nodes(kb, "widget", limit=10)  # must not raise

        assert any(r["path"] == "people/contacts/ok" for r in result["results"])
        skipped = next(n for n in result["notes"] if n["code"] == "skipped")
        assert skipped["detail"]["files"][0]["error"] == "UnicodeDecodeError"


class TestLockSignals:
    def test_lock_records_wait_and_stale_break_fields(self, tmp_path):
        from kvault.core.locks import KBWriteLock

        kb = _make_kb(tmp_path)
        with KBWriteLock(kb) as lock:
            assert lock.waited_ms >= 0.0
            assert lock.broke_stale is False

    def test_stale_break_is_reported(self, tmp_path):
        import os

        from kvault.core.locks import KBWriteLock

        kb = _make_kb(tmp_path)
        # Fabricate a stale lock: owner file names a dead PID, mtime old
        # enough to pass the grace period.
        lock_dir = kb / ".kvault" / "lock"
        lock_dir.mkdir(parents=True)
        (lock_dir / "owner.json").write_text(json.dumps({"pid": 99999999}))
        old = time.time() - 30
        os.utime(lock_dir, (old, old))

        with KBWriteLock(kb, timeout=5.0) as lock:
            assert lock.broke_stale is True

        result = ops.write_node(kb, "people/contacts/x", "# X\n\nx.\n", create=True)
        assert result["success"] is True


class TestReviewRegressions:
    """Pins for the 2026-08-11 pre-release review findings."""

    def test_legacy_node_with_full_meta_json_never_takes_the_noop_path(self, tmp_path):
        """B1 (blocker): has_frontmatter is True even when metadata came from
        the _meta.json fallback, so the guard must also check the file itself.
        A same-content rewrite of a legacy node whose _meta.json includes
        'name' used to skip migration AND delete _meta.json — destroying the
        node's metadata permanently."""
        kb = _make_kb(tmp_path)
        node_dir = kb / "people" / "contacts" / "old_timer"
        node_dir.mkdir()
        (node_dir / "_summary.md").write_text("# Old Timer\n\nLegacy body.\n")
        (node_dir / "_meta.json").write_text(
            json.dumps({"source": "legacy", "aliases": ["Old Timer"], "name": "Old Timer"})
        )

        result = ops.write_node(
            kb, "people/contacts/old_timer", "# Old Timer\n\nLegacy body.\n", create=False
        )

        assert result["changed"] is True  # migrating write, never the fast path
        raw = (node_dir / "_summary.md").read_text()
        assert raw.startswith("---")
        assert not (node_dir / "_meta.json").exists()
        node = ops.read_node(kb, "people/contacts/old_timer", parents="none")
        assert node["meta"]["source"] == "legacy"
        assert node["meta"]["name"] == "Old Timer"

    def test_lost_stale_break_race_is_not_reported_as_a_break(self, tmp_path):
        """M1: a process that loses the stale-break rename race must not claim
        the break (false NORMAL note + spurious --strict exit 3)."""
        import os

        from kvault.core.locks import KBWriteLock

        kb = _make_kb(tmp_path)
        lock_dir = kb / ".kvault" / "lock"
        lock_dir.mkdir(parents=True)
        (lock_dir / "owner.json").write_text(json.dumps({"pid": 99999999}))
        old = time.time() - 30
        os.utime(lock_dir, (old, old))

        lock = KBWriteLock(kb, timeout=5.0)
        # Simulate losing the race: the stale lock vanishes between
        # _is_stale() and the rename.
        assert lock._is_stale() is True
        import shutil as _shutil

        _shutil.rmtree(lock_dir)
        assert lock._break_stale() is False  # rename fails → not our break
        lock.acquire()
        try:
            assert lock.broke_stale is False
        finally:
            lock.release()

    def test_write_parent_summary_carries_nested_notes_and_did(self, tmp_path):
        """M4: the recommended parent path must not discard the narration the
        nested write_summary produced (dropped meta keys, created)."""
        kb = _make_kb(tmp_path)
        ops.write_summary(
            kb, "people", "# People\n\nRollup.\n", meta={"source": "manual", "owner": "eddie"}
        )
        prepared = ops.prepare_summary_update(kb, "people")
        result = ops.write_parent_summary(
            kb,
            "people",
            "# People\n\nRewritten rollup.\n",
            prepared["children_digest"],
            meta={"source": "manual"},
        )
        assert result["success"] is True
        assert result["did"] == "updated summary people"
        removed = next(n for n in result["notes"] if n["code"] == "removed")
        assert "owner" in removed["detail"]["dropped_keys"]

    def test_write_summary_reports_dropped_created_updated(self, tmp_path):
        """M5: write_summary never re-stamps dates, so dropping created/updated
        must be reported like any other dropped key."""
        kb = _make_kb(tmp_path)
        ops.write_node(kb, "people/contacts/jane_doe", BODY, create=True)
        result = ops.write_summary(
            kb,
            "people/contacts/jane_doe",
            "# Jane\n\nReplaced.\n",
            meta={"source": "manual", "aliases": []},
        )
        removed = next(n for n in result["notes"] if n["code"] == "removed")
        assert "created" in removed["detail"]["dropped_keys"]
        assert "updated" in removed["detail"]["dropped_keys"]

    def test_attach_note_keeps_notes_before_ancestors(self):
        """M7: a late-attached note must not land after the bulk payload."""
        from kvault.core import notes as nt

        result = {"success": True, "path": "p", "ancestors": [{"path": "."}]}
        nt.attach_note(result, nt.note("skipped", "late note"))
        keys = list(result.keys())
        assert keys.index("notes") < keys.index("ancestors")
