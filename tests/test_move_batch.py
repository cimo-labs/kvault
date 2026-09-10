"""move --batch: one lock, one confirm, one propagation list, all-or-nothing validation."""

import json

from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import operations as ops

BODY = "# Node\n\nA node.\n"
META = {"source": "manual", "aliases": ["Node"]}


def _seed(kb):
    for name in ("alpha", "bravo", "charlie"):
        assert ops.write_node(kb, f"projects/{name}", BODY, META, create=True)["success"]


def test_batch_moves_under_one_result(empty_kb):
    _seed(empty_kb)
    result = ops.move_entities(
        empty_kb,
        [
            {"from": "projects/alpha", "to": "projects/greek/alpha"},
            {"from": "projects/bravo", "to": "projects/greek/bravo"},
        ],
    )
    assert result["success"] and result["count"] == 2
    assert (empty_kb / "projects" / "greek" / "alpha" / "_summary.md").exists()
    assert not (empty_kb / "projects" / "alpha").exists()
    codes = [n["code"] for n in result["notes"]]
    assert "created" in codes and "propagate" in codes
    assert result["ancestor_paths"][:1] == ["projects"] or "projects" in result["ancestor_paths"]
    assert "projects/greek" in result["ancestor_paths"]
    assert "." in result["ancestor_paths"]
    # the hub was stubbed once, not twice
    created = [n for n in result["notes"] if n["code"] == "created"][0]
    assert created["detail"]["paths"] == ["projects/greek"]


def test_invalid_entry_moves_nothing(empty_kb):
    _seed(empty_kb)
    result = ops.move_entities(
        empty_kb,
        [
            {"from": "projects/alpha", "to": "projects/greek/alpha"},
            {"from": "projects/missing", "to": "projects/greek/missing"},
            {"from": "projects/bravo", "to": "projects/alpha"},
            {"bad": "entry"},
        ],
    )
    assert result["success"] is False
    errors = result["details"]["errors"]
    assert [e["index"] for e in errors] == [1, 2, 3]
    assert (empty_kb / "projects" / "alpha").exists()
    assert not (empty_kb / "projects" / "greek").exists()


def test_batch_rejects_nested_sources_and_reused_targets(empty_kb):
    _seed(empty_kb)
    ops.move_entity(empty_kb, "projects/charlie", "projects/alpha/charlie")
    nested = ops.move_entities(
        empty_kb,
        [
            {"from": "projects/alpha", "to": "projects/x"},
            {"from": "projects/alpha/charlie", "to": "projects/y"},
        ],
    )
    assert nested["success"] is False
    assert "overlaps another move's source" in nested["details"]["errors"][0]["error"]
    twice = ops.move_entities(
        empty_kb,
        [
            {"from": "projects/alpha", "to": "projects/x"},
            {"from": "projects/bravo", "to": "projects/x"},
        ],
    )
    assert twice["success"] is False and "twice" in twice["details"]["errors"][0]["error"]


def test_dry_run_changes_nothing(empty_kb):
    _seed(empty_kb)
    result = ops.move_entities(
        empty_kb, [{"from": "projects/alpha", "to": "projects/greek/alpha"}], dry_run=True
    )
    assert result["success"] and result["dry_run"]
    assert result["stubs"] == ["projects/greek"]
    assert (empty_kb / "projects" / "alpha").exists()
    assert not (empty_kb / "projects" / "greek").exists()


def test_batch_into_new_root_needs_flag(empty_kb):
    _seed(empty_kb)
    refused = ops.move_entities(empty_kb, [{"from": "projects/alpha", "to": "archive/alpha"}])
    assert refused["success"] is False and refused["details"]["reason"] == "new_root"
    # even with the flag a batch may not ADD a root (0.15.1 invariant); the
    # deliberate path is a single move or a write --new-root
    still = ops.move_entities(
        empty_kb, [{"from": "projects/alpha", "to": "archive/alpha"}], new_root=True
    )
    assert still["success"] is False and "increase" in still["error"]
    allowed = ops.move_entity(empty_kb, "projects/alpha", "archive/alpha", new_root=True)
    assert allowed["success"]


def test_cli_batch_requires_confirm_and_reads_stdin(empty_kb):
    _seed(empty_kb)
    runner = CliRunner()
    payload = json.dumps([{"from": "projects/alpha", "to": "projects/greek/alpha"}])
    refused = runner.invoke(
        cli, ["move", "--batch", "--json", "--kb-root", str(empty_kb)], input=payload
    )
    assert refused.exit_code == 1
    assert json.loads(refused.output)["error_code"] == "confirmation_required"
    dry = runner.invoke(
        cli, ["move", "--batch", "--dry-run", "--kb-root", str(empty_kb)], input=payload
    )
    assert dry.exit_code == 0, dry.output
    assert "Dry run: 1 moves valid" in dry.output
    assert (empty_kb / "projects" / "alpha").exists()
    done = runner.invoke(
        cli, ["move", "--batch", "--confirm", "--kb-root", str(empty_kb)], input=payload
    )
    assert done.exit_code == 0, done.output
    assert "Moved: 1 nodes" in done.output
    assert "Ancestors to update:" in done.output
    assert (empty_kb / "projects" / "greek" / "alpha" / "_summary.md").exists()


def test_cli_move_positional_still_required_without_batch(empty_kb):
    runner = CliRunner()
    result = runner.invoke(cli, ["move", "--confirm", "--kb-root", str(empty_kb)])
    assert result.exit_code != 0
    assert "SOURCE and TARGET are required" in result.output


# ── review findings (ultrareview, 2026-09-10) ──────────────────────────


def test_batch_moves_root_categories(empty_kb):
    """A root category is one path component; consolidating roots is the point."""
    _seed(empty_kb)
    result = ops.move_entities(
        empty_kb, [{"from": "projects", "to": "work/projects"}], new_root=True
    )
    assert result["success"] and result["count"] == 1, result
    assert (empty_kb / "work" / "projects" / "alpha" / "_summary.md").exists()
    assert (empty_kb / "work" / "_summary.md").exists()  # stubbed hub
    assert not (empty_kb / "projects").exists()
    refused = ops.move_entities(empty_kb, [{"from": ".", "to": "x/root"}])
    assert refused["success"] is False and "root" in refused["details"]["errors"][0]["error"]


def test_single_move_accepts_a_root_category(empty_kb):
    _seed(empty_kb)
    result = ops.move_entity(empty_kb, "projects", "people/projects")
    assert result["success"], result
    assert (empty_kb / "people" / "projects" / "bravo").exists()


def test_batch_rejects_target_that_is_an_ancestor_of_another_target(empty_kb):
    """Stubbing the ancestor first would make shutil.move nest the source inside it."""
    _seed(empty_kb)
    result = ops.move_entities(
        empty_kb,
        [
            {"from": "projects/alpha", "to": "projects/hub"},
            {"from": "projects/bravo", "to": "projects/hub/bravo"},
        ],
    )
    assert result["success"] is False
    assert "overlaps another move's target" in result["details"]["errors"][0]["error"]
    assert (empty_kb / "projects" / "alpha").exists() and not (
        empty_kb / "projects" / "hub"
    ).exists()


def test_batch_rejects_duplicate_and_overlapping_sources_in_either_order(empty_kb):
    _seed(empty_kb)
    dup = ops.move_entities(
        empty_kb,
        [
            {"from": "projects/alpha", "to": "projects/x"},
            {"from": "projects/alpha", "to": "projects/y"},
        ],
    )
    assert dup["success"] is False and "listed twice" in dup["details"]["errors"][0]["error"]
    ops.move_entity(empty_kb, "projects/charlie", "projects/alpha/charlie")
    parent_last = ops.move_entities(
        empty_kb,
        [
            {"from": "projects/alpha/charlie", "to": "projects/y"},
            {"from": "projects/alpha", "to": "projects/x"},
        ],
    )
    assert parent_last["success"] is False
    assert "overlaps another move's source" in parent_last["details"]["errors"][0]["error"]
    assert (empty_kb / "projects" / "alpha" / "charlie").exists()


def test_batch_never_nests_into_a_target_that_appeared_mid_batch(empty_kb, monkeypatch):
    _seed(empty_kb)
    real_move = ops.shutil.move

    def sneaky(src, dst):
        # simulate another process creating the second target while the batch runs
        (empty_kb / "projects" / "greek" / "bravo").mkdir(parents=True, exist_ok=True)
        return real_move(src, dst)

    monkeypatch.setattr(ops.shutil, "move", sneaky)
    result = ops.move_entities(
        empty_kb,
        [
            {"from": "projects/alpha", "to": "projects/greek/alpha"},
            {"from": "projects/bravo", "to": "projects/greek/bravo"},
        ],
    )
    assert result["success"] and result.get("partial") is True
    assert result["count"] == 1 and "appeared before this move ran" in result["failed"]["error"]
    assert (empty_kb / "projects" / "bravo" / "_summary.md").exists()  # not nested, not lost


def test_cli_batch_without_confirm_says_why_instead_of_prompting(empty_kb):
    _seed(empty_kb)
    runner = CliRunner()
    payload = json.dumps([{"from": "projects/alpha", "to": "projects/greek/alpha"}])
    result = runner.invoke(cli, ["move", "--batch", "--kb-root", str(empty_kb)], input=payload)
    assert result.exit_code != 0
    assert "cannot prompt" in result.output and "--confirm" in result.output
    assert "Aborted" not in result.output
    assert (empty_kb / "projects" / "alpha").exists()


# ── second review round (2026-09-10) ───────────────────────────────────


def test_batch_rejects_target_inside_another_moves_source_either_order(empty_kb):
    """Once `projects` moves away, mkdir(parents=True) would recreate it as a ghost root."""
    _seed(empty_kb)
    assert ops.write_node(empty_kb, "people/alice", BODY, META, create=True)["success"]
    for moves in (
        [
            {"from": "projects", "to": "people/projects"},
            {"from": "people/alice", "to": "projects/alice"},
        ],
        [
            {"from": "people/alice", "to": "projects/alice"},
            {"from": "projects", "to": "people/projects"},
        ],
    ):
        result = ops.move_entities(empty_kb, moves, new_root=True)
        assert result["success"] is False, result
        assert "inside another move's source" in result["details"]["errors"][0]["error"]
        assert (empty_kb / "projects" / "alpha").exists() and (
            empty_kb / "people" / "alice"
        ).exists()


def test_single_move_rechecks_target_inside_the_lock(empty_kb, monkeypatch):
    _seed(empty_kb)
    real_stubs = ops._write_stub_summaries

    def racing(kg_root, paths, trigger):
        (empty_kb / "projects" / "greek" / "alpha").mkdir(parents=True, exist_ok=True)
        return real_stubs(kg_root, paths, trigger)

    monkeypatch.setattr(ops, "_write_stub_summaries", racing)
    result = ops.move_entity(empty_kb, "projects/alpha", "projects/greek/alpha")
    assert result["success"] is False and result["error_code"] == "already_exists"
    assert (empty_kb / "projects" / "alpha" / "_summary.md").exists()
    assert not (empty_kb / "projects" / "greek" / "alpha" / "alpha").exists()


def test_reserved_directories_cannot_be_moved_or_targeted(empty_kb):
    _seed(empty_kb)
    (empty_kb / "journal" / "2026-09").mkdir(parents=True)
    (empty_kb / "journal" / "2026-09" / "log.md").write_text("# log\n")
    assert ops.move_entity(empty_kb, "journal", "people/journal")["success"] is False
    assert ops.move_entity(empty_kb, "projects/alpha", "journal/alpha")["success"] is False
    assert ops.move_entity(empty_kb, "projects/alpha", "people/deep_context")["success"] is False
    ok = ops.move_entity(empty_kb, "projects/alpha", "projects/bravo/deep_context/alpha")
    assert ok["success"], ok  # folding INTO deep_context/ is the documented operation
    assert (
        ops.move_entity(empty_kb, "projects/bravo/deep_context", "projects/dc")["success"] is False
    )
    batch = ops.move_entities(empty_kb, [{"from": "journal", "to": "people/journal"}])
    assert batch["success"] is False and "journal" in batch["details"]["errors"][0]["error"]


def test_fold_does_not_stub_a_summary_inside_deep_context(empty_kb):
    _seed(empty_kb)
    result = ops.move_entities(
        empty_kb,
        [
            {"from": "projects/alpha", "to": "projects/hub/deep_context/alpha"},
            {"from": "projects/bravo", "to": "projects/hub/deep_context/bravo"},
        ],
    )
    assert result["success"], result
    assert (empty_kb / "projects" / "hub" / "_summary.md").exists()
    assert not (empty_kb / "projects" / "hub" / "deep_context" / "_summary.md").exists()
    created = [n for n in result["notes"] if n["code"] == "created"][0]
    assert created["detail"]["paths"] == ["projects/hub"]
    assert ops.build_outline(empty_kb, "projects/hub")["children_count"] == 0
    assert ops.search_nodes(empty_kb, "alpha")["results"]
