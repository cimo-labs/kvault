"""DANGLING: references in summaries that resolve to nothing (0.16).

A parent that still listed children that had been deleted or moved stayed
green forever: PROPAGATE compares dates, and a child that no longer exists
can never be newer than its parent. These tests pin what counts as a
reference (and, as importantly, what does not: prose that looks like a path
was all noise on real KBs), and the reports move/delete now make.
"""

from pathlib import Path

from kvault.core import operations as ops
from kvault.core import references as rf
from kvault.core.check import run_checks
from kvault.core.plan import build_plan

META = {"source": "manual", "aliases": []}


def _node(kb: Path, rel: str, body: str = "# Node\n\nA node.\n") -> None:
    d = kb if rel == "." else kb / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "_summary.md").write_text(
        "---\nupdated: '2026-09-01'\nsource: manual\naliases: []\n---\n" + body
    )


def _kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".kvault").mkdir()
    _node(kb, ".", "# Root\n\nRoot.\n")
    for rel in (
        "projects",
        "projects/hub",
        "projects/hub/weekly_reporting",
        "projects/causal",
        "projects/causal/uplift_routing",
        "tech",
        "tech/models",
        "tech/models/bayes_routing",
        "people",
        "people/family",
    ):
        _node(kb, rel)
    return kb


def _dangling(kb: Path):
    return {(r.node, r.kind, r.target) for r in rf.dangling_references(kb)}


def test_extract_refs_kinds_and_prose_exclusions():
    body = (
        "# Hub\n\n"
        "## Children\n\n"
        "- `weekly_reporting/` — weekly pipeline\n"
        "- **power_analysis** — a plan\n"
        "- [guided_shopping](guided_shopping/) — a project\n\n"
        "See `tech/models/bayes_routing` and projects/causal/uplift_router.\n"
        "Pros and cons: and/or, family/startup reconnects, A/B tests.\n"
        "Repo `cimo-labs/kvault`, file `notes/plan.md`.\n\n"
        "```\nprojects/inside/a_fence\n```\n"
    )
    refs = rf.extract_refs(body)
    assert refs["link"] == ["guided_shopping/"]
    assert "tech/models/bayes_routing" in refs["code"]
    assert "cimo-labs/kvault" in refs["code"]  # extracted; resolution drops it later
    assert refs["path"] == ["projects/causal/uplift_router"]
    assert {"weekly_reporting", "power_analysis", "guided_shopping"} <= set(refs["list"])
    assert "projects/inside/a_fence" not in refs["path"]


def test_child_list_entries_that_are_not_on_disk(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb,
        "projects/hub",
        "# Hub\n\n## Children\n\n"
        "- `weekly_reporting/` — weekly pipeline\n"
        "- **power_analysis** — a plan that was never created\n"
        "- [guided_shopping](guided_shopping/) — moved away\n",
    )
    found = _dangling(kb)
    assert ("projects/hub", "list", "projects/hub/power_analysis") in found
    assert ("projects/hub", "link", "projects/hub/guided_shopping") in found
    assert not any(t.endswith("weekly_reporting") for _, _, t in found)


def test_list_entries_count_only_where_the_summary_lists_children(tmp_path):
    kb = _kb(tmp_path)
    # No entry names an existing child, so these are prose bullets, not a child list.
    _node(
        kb,
        "projects/causal",
        "# Causal\n\n- memory_search: flaky this week\n- session_status — fine\n"
        "Covers uplift_routing.\n",
    )
    assert not [r for r in rf.dangling_references(kb) if r.node == "projects/causal"]


def test_rollups_may_name_grandchildren_and_their_own_name(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb,
        "tech",
        "# Tech\n\n## tech\n\n- `models/` — model notes\n- **bayes_routing** — lives in models\n",
    )
    assert not [r for r in rf.dangling_references(kb) if r.node == "tech"]


def test_paths_resolve_from_the_node_then_the_root(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb,
        "people/family",
        "# Family\n\n"
        "Routing lives at `tech/models/bayes_routing`; the old home was "
        "`tech/bayes_routing`, and projects/causal/uplift_router never existed. "
        "Same-folder link: [up](../../projects/hub/). Escapes: [x](../../../../etc/).\n",
    )
    found = {
        (r.kind, r.target, r.moved_to)
        for r in rf.dangling_references(kb)
        if r.node == "people/family"
    }
    assert ("code", "tech/bayes_routing", ("tech/models/bayes_routing",)) in found
    assert ("path", "projects/causal/uplift_router", ()) in found
    assert len(found) == 2  # the existing path, the good link and the escape are not reported


def test_reserved_and_file_targets_and_history_are_not_checked(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb,
        "people",
        "# People\n\n- `family/` — relatives\n"
        "Notes: [the memo](memo.md), [chart](assets/chart.png).\n"
        "**Deep analysis:** [deep_context/](deep_context/)\n",
    )
    # journal and deep_context summaries are history: old paths there are true.
    _node(kb, "journal/2026-03", "# March\n\nCreated `projects/hub/old_name`.\n")
    _node(kb, "tech/deep_context", "# Background\n\nWas at `projects/causal/old_card`.\n")
    found = [r for r in rf.dangling_references(kb)]
    assert [(r.node, r.target, r.moved_to) for r in found] == [
        ("people", "people/deep_context", ())  # a copied child link; no bogus "moved" hint
    ]


def test_check_reports_dangling_with_hints(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "projects", "# Projects\n\nSee [routing](causal/bayes_routing/).\n")
    doc = run_checks(kb, codes=["DANGLING"])
    (finding,) = doc["findings"]
    assert finding["code"] == "DANGLING" and finding["path"] == "projects"
    assert finding["detail"]["target"] == "projects/causal/bayes_routing"
    assert finding["detail"]["moved_to"] == ["tech/models/bayes_routing"]
    assert "same name at tech/models/bayes_routing" in finding["message"]
    assert "kvault write projects" in finding["fix"]


def test_plan_groups_dangling_references_per_node(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb,
        "projects/hub",
        "# Hub\n\n- `weekly_reporting/` — ok\n- `gone_one/` — gone\n- `gone_two/` — gone\n",
    )
    items = [i for i in build_plan(kb, limit=0)["items"] if i["kind"] == "dangling"]
    assert len(items) == 1 and items[0]["path"] == "projects/hub"
    assert {r["target"] for r in items[0]["refs"]} == {
        "projects/hub/gone_one",
        "projects/hub/gone_two",
    }


def test_move_reports_referrers_with_the_new_path(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "people", "# People\n\nRouting notes: `projects/causal/uplift_routing`.\n")
    # The moved node's own relative link breaks when its depth changes.
    _node(kb, "projects/causal/uplift_routing", "# Routing\n\nSibling: [hub](../../hub/).\n")
    result = ops.move_entity(kb, "projects/causal/uplift_routing", "tech/models/uplift_routing")
    assert result["success"] is True
    assert result["referrer_paths"] == ["people", "tech/models/uplift_routing"]
    note = [n for n in result["notes"] if (n.get("detail") or {}).get("kind") == "references"][0]
    assert note["code"] == "propagate"
    refs = {r["node"]: r for r in note["detail"]["references"]}
    assert refs["people"]["now_at"] == "tech/models/uplift_routing"
    assert "now_at" not in refs["tech/models/uplift_routing"]  # its own link, not a pointer to it


def test_delete_and_batch_move_report_referrers(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "people", "# People\n\n- see `projects/hub/weekly_reporting`\n")
    deleted = ops.delete_entity(kb, "projects/hub/weekly_reporting")
    assert deleted["referrer_paths"] == ["people"]
    ref = [n for n in deleted["notes"] if (n.get("detail") or {}).get("kind") == "references"][0]
    assert ref["detail"]["references"][0]["now_at"] is None

    _node(kb, "people", "# People\n\n- see `tech/models/bayes_routing`\n")
    batch = ops.move_entities(
        kb, [{"from": "tech/models/bayes_routing", "to": "projects/causal/bayes_routing"}]
    )
    assert batch["referrer_paths"] == ["people"]


def test_moves_without_references_stay_quiet(tmp_path):
    kb = _kb(tmp_path)
    result = ops.move_entity(kb, "tech/models/bayes_routing", "projects/causal/bayes_routing")
    assert result["referrer_paths"] == []
    assert not [n for n in result["notes"] if (n.get("detail") or {}).get("kind") == "references"]


# ── review round (2026-09-26) ─────────────────────────────────────────────


def test_background_links_do_not_make_a_summary_a_child_list(tmp_path):
    """The usual "[deep_context/](deep_context/)" line (or an ignored tooling dir)
    used to count as listing children, so every snake_case bullet became DANGLING."""
    kb = _kb(tmp_path)
    (kb / "projects" / "hub" / "deep_context").mkdir()
    (kb / "projects" / "hub" / "scripts").mkdir()
    (kb / ".kvaultignore").write_text("projects/hub/scripts\n")
    _node(
        kb,
        "projects/hub",
        "# Hub\n\n**Background:** [deep_context/](deep_context/) and `scripts/`\n\n"
        "- max_children: 12\n- review_cadence — weekly\n",
    )
    assert [r for r in rf.dangling_references(kb) if r.node == "projects/hub"] == []


def test_bold_underscore_entries_and_fences(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb,
        "projects/hub",
        "# Hub\n\n- __weekly_reporting__ — the pipeline\n\n"
        "````\n```\nprojects/inside/four_tick_fence\n```\n````\n\n"
        "- step:\n\n      ```bash\n      kvault move projects/old_thing tech/old_thing\n      ```\n\n"
        "Inline ``` x ``` is code, and projects/causal/after_inline is a real path.\n",
    )
    found = {(r.kind, r.target) for r in rf.dangling_references(kb) if r.node == "projects/hub"}
    assert found == {("path", "projects/causal/after_inline")}


def test_existing_files_and_symlinked_dirs_are_not_nothing(tmp_path):
    kb = _kb(tmp_path)
    (kb / "LICENSE").write_text("MIT")
    outside = tmp_path / "shared"
    (outside / "guide").mkdir(parents=True)
    (kb / "tech" / "shared").symlink_to(outside, target_is_directory=True)
    _node(kb, "tech", "# Tech\n\nSee [license](../LICENSE) and [guide](shared/guide/).\n")
    assert [r for r in rf.dangling_references(kb) if r.node == "tech"] == []


def test_summaries_symlinked_out_of_the_kb_are_never_read(tmp_path):
    kb = _kb(tmp_path)
    secret = tmp_path / "secret_summary.md"
    secret.write_text("---\nsource: x\n---\n# S\n\n[k](sk_live_abc123/)\n")
    (kb / "tech" / "leak").mkdir()
    (kb / "tech" / "leak" / "_summary.md").symlink_to(secret)
    assert not [r for r in rf.dangling_references(kb) if "sk_live" in r.raw]


def test_root_moves_still_report_path_references(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "people", "# People\n\nPipeline: `projects/hub/weekly_reporting`.\n")
    _node(kb, "projects", "# Projects\n\nSee `hub/weekly_reporting` for the pipeline.\n")
    moved = ops.move_entity(kb, "projects/hub", "tech/hub")
    assert moved["referrer_paths"] == ["people", "projects"]
    ref = [n for n in moved["notes"] if (n.get("detail") or {}).get("kind") == "references"][0]
    homes = {r["node"]: r["now_at"] for r in ref["detail"]["references"]}
    assert homes == {"people": "tech/hub/weekly_reporting", "projects": "tech/hub/weekly_reporting"}
    rooted = ops.move_entity(kb, "people", "tech/people")
    assert rooted["success"] is True


def test_now_at_is_only_given_when_the_new_path_exists(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb,
        "people",
        "# People\n\nSee `projects/hub/never_existed` and `projects/hub/weekly_reporting`.\n",
    )
    moved = ops.move_entity(kb, "projects/hub", "tech/hub")
    ref = [n for n in moved["notes"] if (n.get("detail") or {}).get("kind") == "references"][0]
    homes = {r["target"]: r["now_at"] for r in ref["detail"]["references"]}
    assert homes["projects/hub/weekly_reporting"] == "tech/hub/weekly_reporting"
    assert homes["projects/hub/never_existed"] is None


def test_an_impossible_date_never_fails_a_move_that_already_happened(tmp_path):
    kb = _kb(tmp_path)
    (kb / "people" / "_summary.md").write_text(
        "---\nsource: manual\naliases: []\nupdated: 2026-02-30\n---\n# People\n\n"
        "See `projects/causal/uplift_routing`.\n"
    )
    moved = ops.move_entity(kb, "projects/causal/uplift_routing", "tech/uplift_routing")
    assert moved["success"] is True and moved["referrer_paths"] == ["people"]
    assert run_checks(kb, codes=["DANGLING", "DUPLICATE"])["success"] is True


def test_a_parent_whose_last_child_moved_away_is_a_referrer(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "projects/causal", "# Causal\n\n## Children\n\n- `uplift_routing/` — routing work\n")
    moved = ops.move_entity(kb, "projects/causal/uplift_routing", "tech/uplift_routing")
    assert "projects/causal" in moved["referrer_paths"]
    ref = [n for n in moved["notes"] if (n.get("detail") or {}).get("kind") == "references"][0]
    homes = {(r["node"], r["now_at"]) for r in ref["detail"]["references"]}
    assert ("projects/causal", "tech/uplift_routing") in homes


def test_absurdly_long_targets_do_not_crash_check(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "tech", "# Tech\n\n[t](" + "x" * 260 + ") and `projects/" + "y" * 260 + "`\n")
    run_checks(kb, codes=["DANGLING"])  # used to raise OSError: File name too long
