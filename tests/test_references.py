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
        "- [onboarding_flow](onboarding_flow/) — a project\n\n"
        "See `tech/models/bayes_routing` and projects/causal/uplift_router.\n"
        "Pros and cons: and/or, family/startup reconnects, A/B tests.\n"
        "Repo `cimo-labs/kvault`, file `notes/plan.md`.\n\n"
        "```\nprojects/inside/a_fence\n```\n"
    )
    refs = rf.extract_refs(body)
    assert refs["link"] == ["onboarding_flow/"]
    assert "tech/models/bayes_routing" in refs["code"]
    assert "cimo-labs/kvault" in refs["code"]  # extracted; resolution drops it later
    assert refs["path"] == ["projects/causal/uplift_router"]
    assert {"weekly_reporting", "power_analysis"} <= set(refs["list"])
    assert "onboarding_flow" not in refs["list"]  # an entry written as a link is its link
    assert "projects/inside/a_fence" not in refs["path"]


def test_child_list_entries_that_are_not_on_disk(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb,
        "projects/hub",
        "# Hub\n\n## Children\n\n"
        "- `weekly_reporting/` — weekly pipeline\n"
        "- **power_analysis** — a plan that was never created\n"
        "- [onboarding_flow](onboarding_flow/) — moved away\n",
    )
    found = _dangling(kb)
    assert ("projects/hub", "list", "projects/hub/power_analysis") in found
    assert ("projects/hub", "link", "projects/hub/onboarding_flow") in found
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


# ── 0.16.1: from the first run on a ~1,000-node KB ───────────────────────


def test_root_categories_are_named_but_siblings_are_findings(tmp_path):
    """Boilerplate that mentions the top-level tech/ folder was 22 of 24
    false DANGLING findings on a 1,000-node KB. A sibling is different: a
    child that moved up a level is what its old parent must stop listing."""
    kb = _kb(tmp_path)
    _node(kb, "projects/moved_up")
    _node(
        kb,
        "projects/hub",
        "# Hub\n\n- `weekly_reporting/` — the pipeline\n- `tech/` — architecture lives there\n"
        "- `moved_up/` — moved up a level\n- `gone_child/` — removed\n",
    )
    refs = {r.target: r for r in rf.dangling_references(kb) if r.node == "projects/hub"}
    assert set(refs) == {"projects/hub/moved_up", "projects/hub/gone_child"}
    assert refs["projects/hub/moved_up"].moved_to == ("projects/moved_up",)


def test_child_tables_are_child_lists(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb,
        "projects/hub",
        "# Hub\n\n| Child | What |\n|---|---|\n| `weekly_reporting/` | the pipeline |\n"
        "| `gone_child/` | removed |\n| **old_report/** | moved |\n"
        "| max_batch_size | 64 |\n"  # a bare first cell is a column or a flag
        "| [prod_dashboard](https://grafana.example.com/d/abc) | external |\n"
        "| rate_limit(req/s) | 100 |\n",
    )
    found = {(r.kind, r.target) for r in rf.dangling_references(kb) if r.node == "projects/hub"}
    assert found == {("list", "projects/hub/gone_child"), ("list", "projects/hub/old_report")}


def test_nested_child_lists_are_read(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb,
        "projects/hub",
        "# Hub\n\n- Children:\n    - `weekly_reporting/` — the pipeline\n"
        "    - `gone_child/` — removed\n        - max_batch_size: 64\n",  # a config key
    )
    found = {r.target for r in rf.dangling_references(kb) if r.node == "projects/hub"}
    assert found == {"projects/hub/gone_child"}


def test_entries_written_as_links_are_judged_by_their_link(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "projects/support_agents")
    _node(kb, "projects/atlas/atlas_core")
    _node(
        kb,
        "projects/atlas",
        "# Atlas\n\n- `atlas_core/` — the core\n\n## Related\n\n"
        "- [support_agents](../support_agents/) — the sibling project\n"
        "- [training_runs](https://runs.example.com/atlas) — the dashboard\n",
    )
    assert not [r for r in rf.dangling_references(kb) if r.node == "projects/atlas"]


def test_linked_entries_show_that_a_summary_lists_its_children(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "projects/repos/alpha_svc")
    _node(
        kb,
        "projects/repos",
        "# Repos\n\n- [alpha_svc](https://git.example.com/alpha_svc) — the service\n"
        "- `gone_svc/` — retired\n",
    )
    found = {(r.kind, r.target) for r in rf.dangling_references(kb) if r.node == "projects/repos"}
    assert found == {("list", "projects/repos/gone_svc")}


def test_issue_links_and_code_directories_in_prose_are_not_kb_paths(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb,
        "tech",
        "# Tech\n\n- `models/` — model notes\n\nFixed in [issue/4821](issue/4821) and "
        "`issue/4821`. Built from `render_service/` in the service repo. "
        "Old child: [gone](gone_child/).\n",
    )
    found = {(r.kind, r.target) for r in rf.dangling_references(kb) if r.node == "tech"}
    assert found == {("link", "tech/gone_child")}


def test_a_tracker_link_ending_in_a_journal_month_is_not_a_kb_path(tmp_path):
    kb = _kb(tmp_path)
    (kb / "journal" / "2026-09").mkdir(parents=True)
    (kb / "journal" / "2026-09" / "log.md").write_text("# September\n")
    _node(kb, "tech", "# Tech\n\nShipped in [September](releases/2026-09).\n")
    assert not [r for r in rf.dangling_references(kb) if r.node == "tech"]


def test_links_into_a_gone_child_or_a_deep_context_are_still_checked(tmp_path):
    """Only plain words at both ends mark a tracker link."""
    kb = _kb(tmp_path)
    _node(kb, "projects/hub/weekly_reporting/deep_context/kickoff_notes")
    _node(
        kb,
        "projects/hub",
        "# Hub\n\n- `weekly_reporting/` — the pipeline\n\n[s](gone_child/spec/), "
        "[t](gone_child/sub/_summary.md), [k](deep_context/kickoff_notes/), "
        "[i](issue/4821).\n",
    )
    found = {(r.kind, r.target) for r in rf.dangling_references(kb) if r.node == "projects/hub"}
    assert found == {
        ("link", "projects/hub/gone_child/spec"),
        ("link", "projects/hub/gone_child/sub"),
        ("link", "projects/hub/deep_context/kickoff_notes"),
    }


def test_links_into_a_moved_or_removed_plain_word_child(tmp_path):
    """Plain-word children (models/, reports/) are the common case: a link
    into one is checked even after the child's directory is gone."""
    import shutil

    kb = _kb(tmp_path)
    _node(
        kb,
        "tech",
        "# Tech\n\nSee [Bayes routing](models/bayes_routing/), [Q3](reports/q3_review/) "
        "and [the bug](issue/4821).\n",
    )
    _node(kb, "tech/ml")
    shutil.move(str(kb / "tech" / "models"), str(kb / "tech" / "ml" / "models"))
    refs = {r.raw: r for r in rf.dangling_references(kb) if r.node == "tech"}
    assert set(refs) == {"models/bayes_routing/", "reports/q3_review/"}
    assert refs["models/bayes_routing/"].moved_to == ("tech/ml/models/bayes_routing",)


def test_a_node_citing_its_own_old_path_is_hinted_at_itself(tmp_path):
    """41 of 109 findings on a real KB were nodes citing their own path from
    before a move; the hint names the node itself."""
    kb = _kb(tmp_path)
    _node(
        kb,
        "projects/causal/uplift_routing",
        "# Routing\n\nCanonical path: `projects/uplift_routing`.\n",
    )
    (finding,) = run_checks(kb, codes=["DANGLING"])["findings"]
    assert "(same name at projects/causal/uplift_routing)" in finding["message"]


def test_hints_never_say_which_namesake_to_write(tmp_path):
    """projects/roadmap was renamed; the name's remaining holder is a
    namesake that nothing in the tree tells apart from a moved node, so the
    finding lists it as one of the same-name nodes and does not claim more."""
    kb = _kb(tmp_path)
    _node(kb, "projects/atlas/roadmap")
    _node(kb, "projects/roadmap_2026")
    _node(kb, "people", "# People\n\nPlanning: `projects/roadmap`.\n")
    (finding,) = run_checks(kb, codes=["DANGLING"])["findings"]
    assert "(same name at projects/atlas/roadmap)" in finding["message"]
    assert "(one of projects/atlas/roadmap)" in finding["fix"]
    assert set(finding["detail"]) == {"kind", "raw", "target", "moved_to"}
