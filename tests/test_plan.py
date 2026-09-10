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
    assert sum("git mv" in c and "_summary.md" in c for c in loose[0]["commands"]) == 2
    siblings = [i for i in plan["items"] if i["kind"] == "siblings"]
    assert len(siblings) == 1 and siblings[0]["path"] == "people"
    assert len(siblings[0]["pairs"]) == 2
