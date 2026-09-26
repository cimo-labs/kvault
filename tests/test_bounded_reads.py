"""Bounded reads (0.16): gist parents, batch reads, compact search.

An MCP agent on a ~1,000-node KB reported that a default 10-hit search
(~1.2 KB per hit) overflowed its client's inline output limit, that it read
nodes one call at a time, and that its rules' parents="all" could flood the
context. Measured on copies of two real KBs: parents="immediate" added
100-270 KB to a 10-hit search and parents="all" 510-640 KB.
"""

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import operations as ops

LONG = " ".join(f"word{i}" for i in range(600))  # a ~4 KB rollup body


def _node(kb: Path, rel: str, title: str, body: str, updated: str = "2026-01-02") -> None:
    d = kb if rel == "." else kb / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "_summary.md").write_text(
        f"---\nupdated: '{updated}'\nsource: manual\naliases: []\n---\n# {title}\n\n{body}\n"
    )


@pytest.fixture()
def kb(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    (root / ".kvault").mkdir()
    _node(root, ".", "Root", "Root rollup. " + LONG)
    _node(root, "projects", "Projects", "All projects. " + LONG)
    _node(root, "projects/routing", "Routing", "Routing work. " + LONG)
    for i in range(10):
        _node(
            root,
            f"projects/routing/model_{i:02d}",
            f"Routing model {i}",
            f"Uplift routing model number {i} feeds the explorer dashboard. " + LONG,
        )
    return root


def test_read_node_gist_parents_are_one_line_each(kb):
    node = ops.read_node(kb, "projects/routing/model_03", parents="gist")
    assert [p["path"] for p in node["parents"]] == ["projects/routing", "projects", "."]
    assert node["parents"][0] == {
        "path": "projects/routing",
        "title": "Routing",
        "gist": node["parents"][0]["gist"],
    }
    assert len(node["parents"][0]["gist"]) <= 80
    assert node["parent"] is None
    full = ops.read_node(kb, "projects/routing/model_03", parents="all")
    assert len(json.dumps(node)) * 3 < len(json.dumps(full, default=str))
    entity = ops.read_entity(kb, "projects/routing/model_03", parents="gist")
    assert entity["parent_path"] == "projects/routing" and len(entity["parents"]) == 3


def test_read_nodes_reads_several_under_one_budget(kb):
    result = ops.read_nodes(
        kb,
        ["projects/routing/model_01", "projects/routing/model_02", "nope/missing", "projects"],
        total_max_chars=6000,
    )
    assert result["success"] is True
    assert [n["path"] for n in result["nodes"]] == [
        "projects/routing/model_01",
        "projects/routing/model_02",
    ]
    assert result["missing"] == ["nope/missing"]
    assert result["omitted"] == ["projects"]  # its metadata alone did not fit what was left
    assert result["nodes"][0].get("content_truncated") is None
    assert result["nodes"][1]["content_truncated"] is True
    assert result["notes"][0]["code"] == "truncated"
    # the budget counts whole nodes, not just content
    assert len(json.dumps(result["nodes"])) <= 6000 + 50
    assert list(result)[-1] == "nodes"  # the bulk payload is read last
    wide = ops.read_nodes(kb, ["projects/routing"], total_max_chars=10**6)
    assert len(wide["nodes"][0]["children"]) == 10  # paths, not handles
    assert "children_count" not in wide["nodes"][0]


def test_read_nodes_limits(kb):
    too_many = ops.read_nodes(kb, [f"p{i}" for i in range(26)])
    assert too_many["success"] is False and "at most 25" in too_many["error"]
    full = ops.read_nodes(kb, ["projects"], parents="all")
    assert full["success"] is False and "gist" in full["error"]
    gist = ops.read_nodes(kb, ["projects/routing/model_01"], parents="gist")
    assert [p["path"] for p in gist["nodes"][0]["parents"]] == ["projects/routing", "projects", "."]


def test_cli_read_accepts_several_paths(kb):
    runner = CliRunner()
    single = json.loads(
        runner.invoke(cli, ["--kb-root", str(kb), "--json", "read", "projects"]).output
    )
    assert single["path"] == "projects"  # one path keeps the 0.15 shape
    several = runner.invoke(
        cli,
        [
            "--kb-root",
            str(kb),
            "--json",
            "read",
            "projects",
            "projects/routing",
            "--parents",
            "gist",
        ],
    )
    doc = json.loads(several.output)
    assert several.exit_code == 0 and doc["count"] == 2
    human = runner.invoke(
        cli, ["--kb-root", str(kb), "read", "projects", "projects/routing", "gone"]
    )
    assert "== projects  (Projects, category)" in human.output
    assert "Not found: gone" in human.output


def test_compact_search_is_a_fraction_of_the_full_result(kb):
    full = ops.search_nodes(kb, "uplift routing", limit=10)
    compact = ops.search_nodes(kb, "uplift routing", limit=10, compact=True)
    assert compact["compact"] is True
    assert set(compact["results"][0]) == {"path", "title", "kind", "last_updated", "snippet"}
    assert all(len(r["snippet"]) <= 170 for r in compact["results"])
    assert len(json.dumps(compact)) * 2 < len(json.dumps(full))
    none = ops.search_nodes(kb, "uplift routing", limit=3, compact=True, snippet_chars=0)
    assert all(r["snippet"] == "" for r in none["results"])


def test_search_dates_come_from_frontmatter_not_the_clone(kb):
    later = time.time() + 86400 * 30
    target = kb / "projects" / "routing" / "model_05" / "_summary.md"
    os.utime(target, (later, later))
    result = ops.search_nodes(kb, "number 5", limit=1, compact=True)
    assert result["results"][0]["last_updated"] == "2026-01-02"


def test_gist_parents_on_search_are_shared_and_small(kb):
    result = ops.search_nodes(kb, "uplift routing", limit=10, compact=True, parents="gist")
    hit = result["results"][0]
    assert ops.ancestor_paths(hit["path"]) == ["projects/routing", "projects", "."]
    assert set(result["parents"]) == {"projects/routing", "projects", "."}
    assert result["parents"]["projects"]["title"] == "Projects"
    assert list(result)[-1] == "results"
    assert len(json.dumps(result["parents"])) < 1000
    full = ops.search_nodes(kb, "uplift routing", limit=10, parents="all", total_max_chars=10**9)
    assert len(json.dumps(result)) * 20 < len(json.dumps(full, default=str))


def test_full_parents_on_search_are_bounded(kb):
    result = ops.search_nodes(kb, "uplift routing", limit=10, parents="all", total_max_chars=40000)
    with_node = [r for r in result["results"] if "node" in r]
    without = [r for r in result["results"] if r.get("node_omitted_reason")]
    assert len(with_node) >= 1 and without
    attached = sum(len(json.dumps(r["node"], default=str)) for r in with_node)
    assert attached <= 40000
    assert any(n["code"] == "truncated" and "gist" in n["next"] for n in result["notes"])
    # the budget binds from the first hit: a root-heavy chain can mean none fit
    tiny = ops.search_nodes(kb, "uplift routing", limit=3, parents="all", total_max_chars=100)
    assert not [r for r in tiny["results"] if "node" in r]


def test_mcp_surface(kb):
    pytest.importorskip("mcp.server.fastmcp")
    from kvault.mcp.server import create_server

    server = create_server(kb)

    def call(name, args):
        result = asyncio.run(server.call_tool(name, args))
        if isinstance(result, tuple):
            return result[1].get("result", result[1])
        return json.loads(result[0].text)

    tools = {t.name for t in asyncio.run(server.list_tools())}
    assert "kvault_read_nodes" in tools
    hits = call("kvault_search", {"query": "uplift routing"})
    assert hits["compact"] is True and "score" not in hits["results"][0]
    full = call("kvault_search", {"query": "uplift routing", "compact": False, "limit": 2})
    assert "score" in full["results"][0]
    gist = call("kvault_search", {"query": "uplift routing", "parents": "gist", "limit": 3})
    assert "projects/routing" in gist["parents"]
    many = call("kvault_read_nodes", {"paths": ["projects", "projects/routing/model_09"]})
    assert many["count"] == 2
    node = call("kvault_read_node", {"path": "projects/routing", "parents": "gist"})
    assert [p["path"] for p in node["parents"]] == ["projects", "."]
    # the schema lists the allowed values, so a bad one never reaches the tool
    with pytest.raises(Exception, match="gist"):
        call("kvault_read_node", {"path": "projects", "parents": "everything"})
    tools_by_name = {t.name: t for t in asyncio.run(server.list_tools())}
    schema = tools_by_name["kvault_search"].inputSchema["properties"]["parents"]
    assert "gist" in json.dumps(schema)


# ── review round (2026-09-26) ─────────────────────────────────────────────


def test_symlink_loops_never_hang_or_crash_search(kb):
    (kb / "projects" / "routing" / "model_00" / "loop").symlink_to(kb, target_is_directory=True)
    (kb / "projects" / "a").symlink_to(kb, target_is_directory=True)
    (kb / "projects" / "b").symlink_to(kb / "projects", target_is_directory=True)
    (kb / "memo.md").write_text("routing memo\n")
    started = time.time()
    result = ops.search_nodes(kb, "routing", limit=3)
    assert time.time() - started < 5
    (note,) = [n for n in result["notes"] if n["detail"].get("kind") == "not_indexed"]
    assert note["detail"]["loose_markdown"] == 1
    from kvault.core.check import run_checks

    run_checks(kb, codes=["GHOST", "LOOSE", "DUPLICATE", "DANGLING", "SIBLINGS"])  # terminates


def test_one_undecodable_summary_does_not_break_reads(kb):
    (kb / "projects" / "routing" / "_summary.md").write_bytes(
        b"---\nsource: manual\naliases: []\n---\n# Routing\n\nCaf\xe9 notes.\n"
    )
    gist = ops.search_nodes(kb, "uplift routing", limit=3, compact=True, parents="gist")
    assert "projects" in gist["parents"] and "projects/routing" not in gist["parents"]
    batch = ops.read_nodes(kb, ["projects", "projects/routing", "projects/routing/model_01"])
    assert batch["unreadable"] == ["projects/routing"]
    assert [n["path"] for n in batch["nodes"]] == ["projects", "projects/routing/model_01"]
    assert any(n["code"] == "skipped" for n in batch["notes"])
    runner = CliRunner()
    out = runner.invoke(cli, ["--kb-root", str(kb), "--json", "read", "projects/routing"])
    assert out.exit_code == 1 and "UTF-8" in json.loads(out.output)["error"]


def test_content_and_parents_share_one_search_budget(kb):
    result = ops.search_nodes(
        kb,
        "uplift routing",
        limit=10,
        include_content=True,
        parents="immediate",
        total_max_chars=20000,
    )
    content = result["budget"]["content_chars_returned"]
    parents = result["budget"]["parent_chars_returned"]
    assert content + parents <= 20000


def test_read_nodes_caps_child_lists(kb):
    for i in range(60):
        (kb / "projects" / "routing" / f"extra_{i:02d}").mkdir()
        (kb / "projects" / "routing" / f"extra_{i:02d}" / "_summary.md").write_text("# E\n\nE.\n")
    node = ops.read_nodes(kb, ["projects/routing"], total_max_chars=10**6)["nodes"][0]
    assert len(node["children"]) == 50 and node["children_count"] == 70


def test_summaries_symlinked_out_of_the_kb_are_not_nodes(kb, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("---\nsource: x\n---\n# Secret\n\nhunter2 uplift routing\n")
    (kb / "projects" / "leak").mkdir()
    (kb / "projects" / "leak" / "_summary.md").symlink_to(secret)
    assert ops.read_node(kb, "projects/leak") is None
    result = ops.search_nodes(kb, "hunter2", limit=5)
    assert result["results"] == []
    assert any("outside_kb" in json.dumps(n) for n in result.get("notes", []))


def test_snippets_respect_their_length_and_parents_are_validated(kb):
    for width in (8, 50, 120):
        hits = ops.search_nodes(kb, "uplift", limit=5, compact=True, snippet_chars=width)
        assert all(len(r["snippet"]) <= width for r in hits["results"])
    with pytest.raises(ValueError, match="gist"):
        ops.search_nodes(kb, "uplift", parents="Gist")
