"""The 0.15 write guards: similar-sibling refusal, --new-root, ghost stubs, fan-out notes.

Every guard exists because a legitimate `write --create` produced it on the
2026-09 audit KB with nothing looking.
"""

import json

from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import operations as ops

BODY = "# Node\n\nA node.\n"
META = {"source": "manual", "aliases": ["Node"]}


def _notes(result, code=None, kind=None):
    notes = result.get("notes") or []
    if code:
        notes = [n for n in notes if n["code"] == code]
    if kind:
        notes = [n for n in notes if (n.get("detail") or {}).get("kind") == kind]
    return notes


# ── similar siblings ───────────────────────────────────────────────────


def test_same_words_sibling_is_refused(empty_kb):
    assert ops.write_node(empty_kb, "projects/ai_overview", BODY, META, create=True)["success"]
    result = ops.write_node(empty_kb, "projects/ai_overviews", BODY, META, create=True)
    assert result["success"] is False
    assert result["error_code"] == "validation_error"
    assert result["details"]["reason"] == "similar"
    assert result["details"]["existing"] == "projects/ai_overview"
    assert "allow-similar" in result["hint"]
    assert not (empty_kb / "projects" / "ai_overviews").exists()


def test_allow_similar_creates_and_notes(empty_kb):
    ops.write_node(empty_kb, "projects/ai_overview", BODY, META, create=True)
    result = ops.write_node(
        empty_kb, "projects/ai_overviews", BODY, META, create=True, allow_similar=True
    )
    assert result["success"]
    similar = _notes(result, "structure", "similar")
    assert len(similar) == 1
    assert similar[0]["detail"]["siblings"][0]["kind"] == "same_words"
    assert "projects/ai_overview" in similar[0]["next"]


def test_prefix_sibling_warns_but_creates(empty_kb):
    ops.write_node(empty_kb, "people/alice", BODY, META, create=True)
    result = ops.write_node(empty_kb, "people/alice_smith", BODY, META, create=True)
    assert result["success"]
    similar = _notes(result, "structure", "similar")
    assert similar and similar[0]["detail"]["siblings"][0]["kind"] == "prefix"


def test_same_basename_elsewhere_is_noted(empty_kb):
    ops.write_node(empty_kb, "people/team", BODY, META, create=True)
    result = ops.write_node(empty_kb, "projects/team", BODY, META, create=True)
    assert result["success"]
    similar = _notes(result, "structure", "similar")
    assert similar and similar[0]["detail"]["elsewhere"] == ["people/team"]


def test_distinct_sibling_is_silent(empty_kb):
    ops.write_node(empty_kb, "people/sarah_chen", BODY, META, create=True)
    result = ops.write_node(empty_kb, "people/sarah_cohen", BODY, META, create=True)
    assert result["success"]
    assert _notes(result, "structure") == []


# ── new roots ──────────────────────────────────────────────────────────


def test_new_root_is_refused_without_flag(empty_kb):
    result = ops.write_node(empty_kb, "topics/causal", BODY, META, create=True)
    assert result["success"] is False
    assert result["details"]["reason"] == "new_root"
    assert result["details"]["proposed_root"] == "topics"
    assert set(result["details"]["existing_roots"]) == {"people", "projects"}
    assert not (empty_kb / "topics").exists()


def test_new_root_flag_creates_and_notes(empty_kb):
    result = ops.write_node(empty_kb, "topics/causal", BODY, META, create=True, new_root=True)
    assert result["success"]
    note = _notes(result, "structure", "new_root")
    assert note and note[0]["detail"]["root"] == "topics"
    # the root itself got a stub summary, so it is visible
    assert (empty_kb / "topics" / "_summary.md").exists()


def test_first_roots_of_a_bare_kb_are_allowed(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "_summary.md").write_text("# Root\n")
    result = ops.write_node(kb, "people/alice", BODY, META, create=True)
    assert result["success"]
    assert _notes(result, "structure", "new_root")


def test_existing_root_directory_without_summary_is_adoptable(empty_kb):
    (empty_kb / "sources").mkdir()
    result = ops.write_node(empty_kb, "sources", "# Sources\n\nImports.\n", META, create=True)
    assert result["success"]
    assert _notes(result, "structure", "new_root") == []


# ── ghost stubs ────────────────────────────────────────────────────────


def test_missing_intermediate_parents_get_stubs(empty_kb):
    result = ops.write_node(empty_kb, "people/contacts/work/jane", BODY, META, create=True)
    assert result["success"]
    created = _notes(result, "created")
    assert len(created) == 1
    assert created[0]["detail"]["paths"] == ["people/contacts", "people/contacts/work"]
    for rel in ("people/contacts", "people/contacts/work"):
        text = (empty_kb / rel / "_summary.md").read_text()
        assert "Placeholder" in text
        assert "source: kvault-stub" in text
    assert result["ancestor_paths"] == ["people/contacts/work", "people/contacts", "people", "."]


def test_stubs_are_flagged_by_check_until_rewritten(empty_kb):
    from kvault.core.check import run_checks

    ops.write_node(empty_kb, "people/contacts/jane", BODY, META, create=True)
    doc = run_checks(empty_kb)
    codes = {
        (f["path"], f["detail"].get("summary_code"))
        for f in doc["findings"]
        if f["code"] == "SUMMARY"
    }
    assert ("people/contacts/_summary.md", "placeholder_language") in codes
    assert not [f for f in doc["findings"] if f["code"] == "GHOST"]


def test_tree_counts_ghosts(empty_kb):
    (empty_kb / "infra").mkdir()
    (empty_kb / "infrastructure").mkdir()
    outline = ops.build_outline(empty_kb)
    assert outline["ghost_count"] == 2
    assert "+2 ghost" in ops.render_outline_text(outline)


# ── fan-out ────────────────────────────────────────────────────────────


def test_over_fanout_note_past_ceiling(empty_kb):
    names = [f"node{i:02d}" for i in range(1, 12)]
    last = None
    for name in names:
        last = ops.write_node(empty_kb, f"projects/{name}", BODY, META, create=True)
        assert last["success"], last
    fanout = _notes(last, "structure", "over_fanout")
    assert len(fanout) == 1
    assert fanout[0]["detail"]["child_count"] == 11
    assert fanout[0]["next"] == "kvault plan projects"
    tenth = ops.write_node(empty_kb, "people/node10", BODY, META, create=True)
    assert _notes(tenth, "structure", "over_fanout") == []


# ── move ───────────────────────────────────────────────────────────────


def test_move_stubs_missing_target_parents(empty_kb):
    ops.write_node(empty_kb, "people/alice", BODY, META, create=True)
    result = ops.move_entity(empty_kb, "people/alice", "people/friends/close/alice")
    assert result["success"]
    created = _notes(result, "created")
    assert created and created[0]["detail"]["paths"] == ["people/friends", "people/friends/close"]
    assert (empty_kb / "people" / "friends" / "_summary.md").exists()
    assert "people/friends/close" in result["ancestor_paths"]


def test_move_into_new_root_requires_flag(empty_kb):
    ops.write_node(empty_kb, "people/alice", BODY, META, create=True)
    refused = ops.move_entity(empty_kb, "people/alice", "contacts/alice")
    assert refused["success"] is False and refused["details"]["reason"] == "new_root"
    assert (empty_kb / "people" / "alice").exists()
    allowed = ops.move_entity(empty_kb, "people/alice", "contacts/alice", new_root=True)
    assert allowed["success"]
    assert _notes(allowed, "structure", "new_root")


# ── CLI flags ──────────────────────────────────────────────────────────


def _write(kb, path, *flags):
    runner = CliRunner()
    stdin = "---\nsource: manual\naliases: [X]\n---\n# X\n\nbody\n"
    return runner.invoke(
        cli, ["write", path, "--create", "--json", "--kb-root", str(kb), *flags], input=stdin
    )


def test_cli_new_root_and_allow_similar_flags(empty_kb):
    refused = json.loads(_write(empty_kb, "topics/x").output)
    assert refused["success"] is False and refused["details"]["reason"] == "new_root"
    assert "--new-root" in refused["hint"]
    ok = _write(empty_kb, "topics/x", "--new-root")
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.output)["success"]
    ok1 = _write(empty_kb, "topics/ai_overview")
    assert json.loads(ok1.output)["success"], ok1.output
    twin = json.loads(_write(empty_kb, "topics/ai_overviews").output)
    assert twin["success"] is False and twin["details"]["reason"] == "similar"
    assert "--allow-similar" in twin["hint"]
    ok2 = _write(empty_kb, "topics/ai_overviews", "--allow-similar")
    assert ok2.exit_code == 0, ok2.output


def test_cli_human_output_renders_structure_notes(empty_kb):
    runner = CliRunner()
    stdin = "---\nsource: manual\naliases: [Alice]\n---\n# Alice\n\nbody\n"
    runner.invoke(
        cli, ["write", "people/alice", "--create", "--kb-root", str(empty_kb)], input=stdin
    )
    result = runner.invoke(
        cli, ["write", "people/alice_smith", "--create", "--kb-root", str(empty_kb)], input=stdin
    )
    assert result.exit_code == 0, result.output
    assert "structure" in result.output and "alice (prefix" in result.output


def test_adopting_a_ghost_does_not_count_itself_toward_fanout(empty_kb):
    for i in range(1, 10):
        assert ops.write_node(empty_kb, f"projects/node{i:02d}", BODY, META, create=True)["success"]
    (empty_kb / "projects" / "ghost_dir").mkdir()  # 10 managed children, one a ghost
    result = ops.write_node(empty_kb, "projects/ghost_dir", BODY, META, create=True)
    assert result["success"]
    assert _notes(result, "structure", "over_fanout") == []  # 10, not 11
