"""Decisions recorded in frontmatter stick: distinct_from, max_children, series_ok, and
`kvault mark`. Plus the batch root invariant. (0.15.1)

Premise: nobody reviews a KB; the human corrects the agent in use. A correction
must have a home the deterministic rules read, or it is re-proposed next week.
"""

import json

from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import decisions as dc
from kvault.core import operations as ops
from kvault.core.check import run_checks
from kvault.core.plan import build_plan

BODY = "# Node\n\nA node with enough words to describe itself briefly.\n"
META = {"source": "manual", "aliases": ["Node"]}


def _n(kb, path, **kw):
    r = ops.write_node(kb, path, BODY, META, create=True, **kw)
    assert r["success"], r
    return r


# ── distinct_from ──────────────────────────────────────────────────────


def test_mark_distinct_silences_siblings_finding_and_guard(empty_kb):
    _n(empty_kb, "people/code_reviews")
    _n(empty_kb, "people/reviews")
    before = [f for f in run_checks(empty_kb)["findings"] if f["code"] == "SIBLINGS"]
    assert before
    result = ops.mark_node(empty_kb, "people/code_reviews", distinct_from=["reviews"])
    assert result["success"], result
    assert result["decisions"]["distinct_from"] == ["people/reviews"]
    assert "distinct_from" in result["did"]
    assert dc.are_distinct(empty_kb, "people/code_reviews", "people/reviews")
    assert dc.are_distinct(empty_kb, "people/reviews", "people/code_reviews")  # either side
    after = [f for f in run_checks(empty_kb)["findings"] if f["code"] == "SIBLINGS"]
    assert after == []
    # the frontmatter carries it, the body is untouched, the op is logged as a write
    text = (empty_kb / "people" / "code_reviews" / "_summary.md").read_text()
    assert "distinct_from:" in text and "A node with enough words" in text


def test_distinct_from_lets_a_same_words_twin_be_created(empty_kb):
    _n(empty_kb, "projects/ai_overview")
    refused = ops.write_node(empty_kb, "projects/ai_overviews", BODY, META, create=True)
    assert refused["success"] is False
    allowed = ops.write_node(
        empty_kb,
        "projects/ai_overviews",
        BODY,
        {**META, "distinct_from": ["ai_overview"]},
        create=True,
    )
    assert allowed["success"], allowed
    assert not [n for n in allowed.get("notes", []) if n["code"] == "structure"]
    # and recorded on the existing side works too
    _n(empty_kb, "people/standup")
    ops.mark_node(empty_kb, "people/standup", distinct_from=["people/standups"])
    twin = ops.write_node(empty_kb, "people/standups", BODY, META, create=True)
    assert twin["success"], twin


def test_distinct_from_silences_same_name_elsewhere(empty_kb):
    # different depths: not a facet layout, so it is reported until recorded
    _n(empty_kb, "people/family")
    _n(empty_kb, "people/friends/family")
    assert [
        f
        for f in run_checks(empty_kb)["findings"]
        if f["detail"].get("kind") == "same_name_elsewhere"
    ]
    ops.mark_node(empty_kb, "people/family", distinct_from=["people/friends/family"])
    assert not [
        f
        for f in run_checks(empty_kb)["findings"]
        if f["detail"].get("kind") == "same_name_elsewhere"
    ]


# ── max_children ───────────────────────────────────────────────────────


def test_max_children_override_is_honored_everywhere(empty_kb):
    for i in range(12):
        _n(empty_kb, f"projects/aio_{i:02d}", allow_similar=True)
    assert [f for f in run_checks(empty_kb)["findings"] if f["code"] == "BRANCH"]
    assert [i for i in build_plan(empty_kb, limit=0)["items"] if i["kind"] == "cluster"]
    result = ops.mark_node(empty_kb, "projects", max_children=30)
    assert result["success"] and result["decisions"]["max_children"] == 30
    assert not [f for f in run_checks(empty_kb)["findings"] if f["code"] == "BRANCH"]
    assert not [i for i in build_plan(empty_kb, limit=0)["items"] if i["kind"] == "cluster"]
    thirteenth = ops.write_node(empty_kb, "projects/zebra", BODY, META, create=True)
    assert not [
        n
        for n in thirteenth.get("notes", [])
        if (n.get("detail") or {}).get("kind") == "over_fanout"
    ]
    prepared = ops.prepare_summary_update(empty_kb, "projects")
    assert prepared["children_mode"] == "content" and prepared["hierarchy_hint"] is None
    assert prepared["max_direct_children"] == 30


# ── series_ok ──────────────────────────────────────────────────────────


def test_series_ok_silences_series_and_fold(empty_kb):
    for d in ("15", "16", "17"):
        _n(empty_kb, f"projects/standup_2026_06_{d}")
    assert [f for f in run_checks(empty_kb)["findings"] if f["code"] == "SERIES"]
    result = ops.mark_node(empty_kb, "projects", series_ok=True)
    assert result["success"] and result["decisions"]["series_ok"] is True
    assert not [f for f in run_checks(empty_kb)["findings"] if f["code"] == "SERIES"]
    assert not [i for i in build_plan(empty_kb, limit=0)["items"] if i["kind"] == "series"]


# ── mark: idempotent, clearable, refuses nonsense ──────────────────────


def test_mark_is_idempotent_and_clearable(empty_kb):
    _n(empty_kb, "people/a")
    first = ops.mark_node(empty_kb, "people/a", distinct_from=["b"])
    second = ops.mark_node(empty_kb, "people/a", distinct_from=["b"])
    assert first["changed"] and not second["changed"]
    assert any(n["code"] == "unchanged" for n in second["notes"])
    cleared = ops.mark_node(empty_kb, "people/a", clear=True)
    assert cleared["decisions"] == {"distinct_from": [], "max_children": None, "series_ok": False}
    assert ops.mark_node(empty_kb, "people/nope", series_ok=True)["success"] is False
    assert ops.mark_node(empty_kb, "people/a")["success"] is False  # nothing to record


def test_plan_items_say_how_to_record_the_other_decision(empty_kb):
    _n(empty_kb, "people/code_reviews")
    _n(empty_kb, "people/reviews")
    for d in ("15", "16", "17"):
        _n(empty_kb, f"projects/standup_2026_06_{d}")
    plan = build_plan(empty_kb, limit=0)
    sib = [i for i in plan["items"] if i["kind"] == "siblings"][0]
    assert any("kvault mark" in c and "--distinct-from" in c for c in sib["commands"])
    ser = [i for i in plan["items"] if i["kind"] == "series"][0]
    assert any("kvault mark" in c and "--series-ok" in c for c in ser["commands"])
    assert not plan["questions"] or all("default" in q for q in plan["questions"])


# ── CLI + MCP ──────────────────────────────────────────────────────────


def test_cli_mark(empty_kb):
    _n(empty_kb, "people/a")
    _n(empty_kb, "people/b")
    runner = CliRunner()
    r = runner.invoke(
        cli, ["mark", "people/a", "--distinct-from", "b", "--json", "--kb-root", str(empty_kb)]
    )
    assert r.exit_code == 0, r.output
    doc = json.loads(r.output)
    assert doc["success"] and doc["decisions"]["distinct_from"] == ["people/b"]
    human = runner.invoke(
        cli, ["mark", "people", "--max-children", "25", "--kb-root", str(empty_kb)]
    )
    assert human.exit_code == 0 and "max_children=25" in human.output
    nothing = runner.invoke(cli, ["mark", "people/a", "--kb-root", str(empty_kb)])
    assert nothing.exit_code != 0


# ── batch root invariant ───────────────────────────────────────────────


def _roots(kb):
    from kvault.core import structure as st

    return sorted(d.name for d in st.child_dirs(kb, kb, []))


def test_batch_new_root_only_when_roots_do_not_increase(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".kvault").mkdir()
    (kb / "_summary.md").write_text("# Root\n\nRoot.\n")
    for name in ("aio_a", "aio_b", "aio_c", "people"):
        _n(kb, f"{name}/item", new_root=True)
    assert len(_roots(kb)) == 4
    # consolidation: 3 roots into 1 new hub → 2 roots after; allowed
    fold = ops.move_entities(
        kb, [{"from": f"aio_{c}", "to": f"aio/aio_{c}"} for c in "abc"], new_root=True
    )
    assert fold["success"], fold
    assert _roots(kb) == ["aio", "people"]
    # a batch that adds a root (deep node → brand-new root) is refused even with the flag
    add = ops.move_entities(kb, [{"from": "people/item", "to": "extra/item"}], new_root=True)
    assert add["success"] is False and "increase" in add["error"]
    assert _roots(kb) == ["aio", "people"]
    # a rename of a root is net zero; allowed
    rename = ops.move_entities(kb, [{"from": "people", "to": "humans"}], new_root=True)
    assert rename["success"], rename
