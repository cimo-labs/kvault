"""kvault plan: deterministic clustering and the ordered worklist, executed end to end."""

import json

from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import operations as ops
from kvault.core.check import run_checks
from kvault.core.plan import build_plan

BODY = "# Node\n\nA node with enough words to describe itself briefly.\n"
META = {"source": "manual", "aliases": ["Node"]}

FLAT = [
    "aio",
    "aio_architecture",
    "aio_reporting",
    "aio_scaling",
    "pdp_prompts",
    "pdp_prompt_ranking",
    "pdp_assortment",
    "orchid",
    "quartz",
    "tundra",
    "velvet",
    "willow",
]


def _flat_projects(kb):
    for name in FLAT:
        r = ops.write_node(kb, f"projects/{name}", BODY, META, create=True, allow_similar=True)
        assert r["success"], r


def test_plan_clusters_over_fanout_parent(empty_kb):
    _flat_projects(empty_kb)
    plan = build_plan(empty_kb, limit=0)
    assert plan["success"]
    clusters = [i for i in plan["items"] if i["kind"] == "cluster"]
    assert [c["new_parent"] for c in clusters] == ["projects/aio", "projects/pdp"]
    aio = clusters[0]
    assert aio["new_parent_exists"] is True
    assert {m["from"] for m in aio["moves"]} == {
        "projects/aio_architecture",
        "projects/aio_reporting",
        "projects/aio_scaling",
    }
    assert all(m["to"].startswith("projects/aio/") for m in aio["moves"])
    assert "kvault move --batch --confirm" in aio["commands"][0]
    assert aio["hub_name_is_placeholder"] is False  # projects/aio already exists
    pdp = clusters[1]
    assert pdp["hub_name_is_placeholder"] is True and "rename the hub" in pdp["then"]
    assert pdp["members_total"] == 3
    assert {m["name"] for m in pdp["members"]} == {
        "pdp_prompts",
        "pdp_prompt_ranking",
        "pdp_assortment",
    }
    assert all(
        m["gist"] and m["gist"].startswith("A node with enough words") for m in pdp["members"]
    )
    assert any("stay in place" in q for q in plan["questions"])
    assert plan["items"][0]["kind"] == "cluster"


def test_plan_is_bounded_and_scoped(empty_kb):
    _flat_projects(empty_kb)
    (empty_kb / "ghostly").mkdir()
    full = build_plan(empty_kb, limit=0)
    assert full["total"] >= 3
    two = build_plan(empty_kb, limit=2)
    assert two["count"] == 2 and two["total"] == full["total"]
    assert two["notes"][0]["code"] == "truncated"
    scoped = build_plan(empty_kb, path="projects", limit=0)
    assert all(i["path"].startswith("projects") for i in scoped["items"])
    assert not [i for i in scoped["items"] if i["kind"] == "ghost"]
    missing = build_plan(empty_kb, path="nope")
    assert missing["success"] is False


def test_plan_executes_end_to_end(empty_kb):
    _flat_projects(empty_kb)
    before = run_checks(empty_kb)
    assert [f for f in before["findings"] if f["code"] == "BRANCH" and f["path"] == "projects"]
    plan = build_plan(empty_kb, limit=0)
    for item in plan["items"]:
        if item["kind"] != "cluster":
            continue
        result = ops.move_entities(empty_kb, item["moves"])
        assert result["success"] and not result.get("partial"), result
    after = run_checks(empty_kb)
    assert not [f for f in after["findings"] if f["code"] == "BRANCH"]
    assert (empty_kb / "projects" / "pdp" / "_summary.md").exists()  # stubbed hub
    assert (empty_kb / "projects" / "aio" / "aio_scaling" / "_summary.md").exists()
    outline = ops.build_outline(empty_kb, "projects")
    assert outline["children_count"] == 7  # aio, pdp + 5 leftovers
    assert outline["ghost_count"] == 0


def test_cli_plan_human_and_json(empty_kb):
    _flat_projects(empty_kb)
    runner = CliRunner()
    human = runner.invoke(cli, ["plan", "--kb-root", str(empty_kb)])
    assert human.exit_code == 0, human.output
    assert human.output.startswith("Plan for .:")
    assert "1. cluster  projects → projects/aio" in human.output
    assert "Questions (answer from evidence" in human.output
    as_json = runner.invoke(cli, ["plan", "--json", "--limit", "1", "--kb-root", str(empty_kb)])
    doc = json.loads(as_json.output)
    assert doc["count"] == 1 and doc["items"][0]["kind"] == "cluster"
    clean = runner.invoke(cli, ["plan", "people", "--kb-root", str(empty_kb)])
    assert "nothing to do" in clean.output


def test_plan_collapses_loose_files_per_directory_and_siblings_per_parent(empty_kb):
    (empty_kb / "projects" / "a.md").write_text("---\nsource: x\n---\n# A\n")
    (empty_kb / "projects" / "b.md").write_text("---\nsource: x\n---\n# B\n")
    (empty_kb / "projects" / "c.xlsx").write_bytes(b"x")
    for name in ("code_reviews", "reviews", "infra", "infrastructure"):
        ops.write_node(empty_kb, f"people/{name}", BODY, META, create=True)
    plan = build_plan(empty_kb, limit=0)
    loose = [i for i in plan["items"] if i["kind"] == "loose"]
    assert len(loose) == 1 and loose[0]["path"] == "projects"
    assert "2 legacy node file(s)" in loose[0]["why"]
    assert sum(c.startswith("kvault write") and "--create" in c for c in loose[0]["commands"]) == 2
    siblings = [i for i in plan["items"] if i["kind"] == "siblings"]
    assert len(siblings) == 1 and siblings[0]["path"] == "people"
    assert len(siblings[0]["pairs"]) == 2


def test_series_item_emits_the_fold_batch(empty_kb):
    for day, name in ((15, "monday"), (16, "tuesday"), (17, "wednesday")):
        r = ops.write_node(
            empty_kb,
            f"projects/critical_june{day}_{name}_source_boundary_2026_06_{day}",
            BODY,
            META,
            create=True,
        )
        assert r["success"], r
    plan = build_plan(empty_kb, limit=0)
    series = [i for i in plan["items"] if i["kind"] == "series"]
    assert len(series) == 1
    item = series[0]
    assert item["new_parent"] == "projects/critical_source_boundary"
    assert len(item["moves"]) == 3
    assert all(
        m["to"].startswith("projects/critical_source_boundary/deep_context/") for m in item["moves"]
    )
    result = ops.move_entities(empty_kb, item["moves"])
    assert result["success"] and result["count"] == 3
    after = run_checks(empty_kb)
    assert not [f for f in after["findings"] if f["code"] == "SERIES"]
    assert ops.search_nodes(empty_kb, "critical june16")["results"]


def test_adopt_commands_go_through_kvault_write(empty_kb):
    import subprocess

    (empty_kb / "projects" / "landscape.md").write_text(
        "---\nsource: manual\ntopic: Landscape\n---\n# Landscape\n\nThe field.\n"
    )
    plan = build_plan(empty_kb, limit=0)
    loose = [i for i in plan["items"] if i["kind"] == "loose"][0]
    adopt = [c for c in loose["commands"] if c.startswith("kvault write")]
    assert len(adopt) == 1
    assert "projects/landscape --create" in adopt[0] and "< " in adopt[0]
    assert adopt[0].endswith("rm " + str(empty_kb / "projects" / "landscape.md"))
    # and the command actually works end to end through the CLI
    subprocess.run(adopt[0], shell=True, check=True)
    assert (empty_kb / "projects" / "landscape" / "_summary.md").exists()
    assert not (empty_kb / "projects" / "landscape.md").exists()
    assert not [f for f in run_checks(empty_kb)["findings"] if f["code"] in ("LOOSE", "WRITE")]


def test_nested_series_emit_only_the_outermost_fold(empty_kb):
    # month buckets that each hold day cards: a chronology at two levels
    for month in ("june", "july", "august"):
        for day in (1, 2, 3):
            r = ops.write_node(
                empty_kb,
                f"projects/{month}_2026_boundaries/card_{month}{day}_boundary_2026_0{day}",
                BODY,
                META,
                create=True,
            )
            assert r["success"], r
    plan = build_plan(empty_kb, limit=0)
    series = [i for i in plan["items"] if i["kind"] == "series"]
    assert [i["path"] for i in series] == ["projects"]
    assert series[0]["members_total"] == 3
    result = ops.move_entities(empty_kb, series[0]["moves"])
    assert result["success"] and result["count"] == 3
    assert not [f for f in run_checks(empty_kb)["findings"] if f["code"] == "SERIES"]


def test_plan_quotes_the_root_and_flags_root_clusters(tmp_path):
    kb = tmp_path / "my kb"  # a space in the path
    kb.mkdir()
    (kb / ".kvault").mkdir()
    (kb / "_summary.md").write_text("# Root\n\nRoot.\n")
    for name in [f"aio_{c}" for c in "abc"] + [f"pdp_{c}" for c in "abc"] + list("uvwxyz"):
        r = ops.write_node(kb, f"{name}/item", BODY, META, create=True, new_root=True)
        assert r["success"], r
    plan = build_plan(kb, limit=0)
    clusters = [i for i in plan["items"] if i["kind"] == "cluster" and i["path"] == "."]
    assert clusters, plan["items"][:3]
    cmd = clusters[0]["commands"][0]
    assert "--new-root" in cmd and "'" in cmd and "my kb" in cmd
    assert clusters[0]["moves"][0]["from"].count("/") == 0  # a root category as source
    # and the emitted batch actually runs
    result = ops.move_entities(kb, clusters[0]["moves"], new_root=True)
    assert result["success"] and result["count"] == 3


# ── second review round (2026-09-10) ───────────────────────────────────


def _series(kb, parent, key, days=("15", "16", "17")):
    for d in days:
        r = ops.write_node(
            kb,
            f"{parent}/{key}_2026_06_{d}" if parent != "." else f"{key}_2026_06_{d}",
            BODY,
            META,
            create=True,
            new_root=True,
        )
        assert r["success"], r


def test_root_level_series_batch_carries_new_root(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".kvault").mkdir()
    (kb / "_summary.md").write_text("# Root\n\nRoot.\n")
    _series(kb, ".", "standup")
    item = [i for i in build_plan(kb, limit=0)["items"] if i["kind"] == "series"][0]
    assert "--new-root" in item["commands"][0]
    result = ops.move_entities(kb, item["moves"], new_root=True)
    assert result["success"] and result["count"] == 3


def test_series_with_empty_key_becomes_a_question(empty_kb):
    for d in ("2026_01", "2026_02", "2026_03"):
        assert ops.write_node(empty_kb, f"projects/{d}", BODY, META, create=True)["success"]
    plan = build_plan(empty_kb, limit=0)
    assert not [i for i in plan["items"] if i["kind"] == "series"]
    assert any("named by date alone" in q for q in plan["questions"])


def test_series_hub_that_exists_is_updated_not_recreated(empty_kb):
    assert ops.write_node(empty_kb, "projects/standup", BODY, META, create=True)["success"]
    _series(empty_kb, "projects", "standup")
    item = [i for i in build_plan(empty_kb, limit=0)["items"] if i["kind"] == "series"][0]
    assert item["new_parent"] == "projects/standup" and item["new_parent_exists"] is True
    assert "do not recreate" in item["then"]
    result = ops.move_entities(empty_kb, item["moves"])
    assert result["success"]
    text = (empty_kb / "projects" / "standup" / "_summary.md").read_text()
    assert "A node with enough words" in text and "Placeholder" not in text


def test_series_under_a_parent_named_like_the_key_folds_into_the_parent(empty_kb):
    assert ops.write_node(empty_kb, "projects/standup", BODY, META, create=True)["success"]
    _series(empty_kb, "projects/standup", "standup")
    item = [i for i in build_plan(empty_kb, limit=0)["items"] if i["kind"] == "series"][0]
    assert item["new_parent"] == "projects/standup"
    assert all(m["to"].startswith("projects/standup/deep_context/") for m in item["moves"])
    result = ops.move_entities(empty_kb, item["moves"])
    assert result["success"]
    assert not (empty_kb / "projects" / "standup" / "standup").exists()


def test_series_twin_sibling_becomes_the_hub(empty_kb):
    assert ops.write_node(empty_kb, "projects/standups", BODY, META, create=True)["success"]
    _series(empty_kb, "projects", "standup")
    item = [i for i in build_plan(empty_kb, limit=0)["items"] if i["kind"] == "series"][0]
    assert item["new_parent"] == "projects/standups" and item["new_parent_exists"]


def test_reserved_series_key_is_a_question(empty_kb):
    _series(empty_kb, "projects", "journal")
    plan = build_plan(empty_kb, limit=0)
    assert not [i for i in plan["items"] if i["kind"] == "series"]
    assert any("reserved name" in q for q in plan["questions"])


def test_cluster_batch_drops_series_items_under_its_sources(empty_kb):
    for name in (
        "aio_a",
        "aio_b",
        "aio_c",
        "orchid",
        "quartz",
        "tundra",
        "velvet",
        "willow",
        "xenon",
        "yarrow",
        "zephyr",
    ):
        assert ops.write_node(empty_kb, f"projects/{name}", BODY, META, create=True)["success"]
    _series(empty_kb, "projects/aio_a", "standup")
    plan = build_plan(empty_kb, limit=0)
    kinds = [(i["kind"], i["path"]) for i in plan["items"] if i["kind"] in ("cluster", "series")]
    assert ("cluster", "projects") in kinds
    assert ("series", "projects/aio_a") not in kinds  # the cluster moves projects/aio_a


def test_dated_parent_question_only_for_surviving_series(empty_kb):
    for month in ("june", "july", "august"):
        _series(empty_kb, f"projects/{month}_2026_boundaries", "card")
    plan = build_plan(empty_kb, limit=0)
    assert [i["path"] for i in plan["items"] if i["kind"] == "series"] == ["projects"]
    assert not any("is itself a dated name" in q for q in plan["questions"])


def test_loose_commands_quote_every_path(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".kvault").mkdir()
    (kb / "_summary.md").write_text("# Root\n\nRoot.\n")
    assert ops.write_node(kb, "projects/x", BODY, META, create=True, new_root=True)["success"]
    (kb / "projects" / "x y.pdf").write_bytes(b"x")
    (kb / "projects" / "it's.md").write_text("---\nsource: manual\n---\n# It\n")
    loose = [i for i in build_plan(kb, limit=0)["items"] if i["kind"] == "loose"][0]
    joined = "\n".join(loose["commands"])
    assert "x y.pdf'" in joined and "it'\"'\"'s" in joined
    assert " x y.pdf" not in joined  # never a bare space-separated path


def test_plan_scope_accepts_dot_slash(empty_kb):
    assert ops.write_node(empty_kb, "projects/a", BODY, META, create=True)["success"]
    (empty_kb / "projects" / "ghostly").mkdir()
    plan = build_plan(empty_kb, path="./projects", limit=0)
    assert plan["path"] == "projects" and plan["total"] >= 1
