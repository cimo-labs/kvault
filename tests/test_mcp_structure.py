"""MCP surface for the 0.15 structure work: check, plan, batch move, write guards, gists.

The audited KB was driven over MCP, where the only health tool was
`kvault_validate_kb`. These tests pin that every new signal is reachable
from that surface too.
"""

import asyncio

import pytest

pytest.importorskip("mcp.server.fastmcp")

from kvault.mcp.server import create_server  # noqa: E402
from tests.test_mcp_server import _make_kb, _run_tool  # noqa: E402

BODY = "# Node\n\nA node with enough words to describe itself briefly.\n"
META = {"source": "manual", "aliases": ["Node"]}


def test_new_tools_are_listed(tmp_path):
    server = create_server(_make_kb(tmp_path))
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert {"kvault_check", "kvault_plan", "kvault_move_entities"} <= names


def test_write_node_guards_over_mcp(tmp_path):
    kb = _make_kb(tmp_path)
    server = create_server(kb)
    refused = _run_tool(
        server,
        "kvault_write_node",
        {"path": "projects/x", "content": BODY, "meta": META, "create": True},
    )
    assert refused["success"] is False and refused["details"]["reason"] == "new_root"
    allowed = _run_tool(
        server,
        "kvault_write_node",
        {"path": "projects/x", "content": BODY, "meta": META, "create": True, "new_root": True},
    )
    assert allowed["success"]
    assert any(n["code"] == "structure" for n in allowed["notes"])
    twin = _run_tool(
        server,
        "kvault_write_node",
        {"path": "projects/xs_and_more", "content": BODY, "meta": META, "create": True},
    )
    assert twin["success"]  # distinct enough; the guard is for same-words twins
    same = _run_tool(
        server,
        "kvault_write_node",
        {"path": "people/contact", "content": BODY, "meta": META, "create": True},
    )
    assert same["success"] is False and same["details"]["reason"] == "similar"


def test_check_over_mcp_names_ghosts_and_is_bounded(tmp_path):
    kb = _make_kb(tmp_path)
    (kb / "infra").mkdir()
    (kb / "infrastructure").mkdir()
    (kb / "loose.txt").write_text("x")
    server = create_server(kb)
    doc = _run_tool(server, "kvault_check", {"max_findings": 1})
    codes = {f["code"] for f in doc["findings"]}
    assert {"GHOST", "SIBLINGS", "LOOSE"} <= codes
    assert doc["truncated"].get("GHOST") == 1
    assert doc["did"].startswith("checked:")
    validate = _run_tool(server, "kvault_validate_kb", {})
    assert [i for i in validate["issues"] if i["type"] == "ghost_directory"]


def test_prepare_summary_update_children_modes(tmp_path):
    kb = _make_kb(tmp_path)
    server = create_server(kb)
    gist = _run_tool(
        server, "kvault_prepare_summary_update", {"path": "people/contacts", "children": "gist"}
    )
    assert gist["children_mode"] == "gist"
    assert "gist" in gist["children"][0] and "content" not in gist["children"][0]
    content = _run_tool(server, "kvault_prepare_summary_update", {"path": "people/contacts"})
    assert content["children_mode"] == "content" and "content" in content["children"][0]
    assert content["children_digest"] == gist["children_digest"]
    bad = _run_tool(
        server, "kvault_prepare_summary_update", {"path": "people/contacts", "children": "nope"}
    )
    assert bad["success"] is False


def test_plan_then_move_entities_over_mcp(tmp_path):
    kb = _make_kb(tmp_path)
    server = create_server(kb)
    names = [
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
    ]
    for name in names:
        r = _run_tool(
            server,
            "kvault_write_node",
            {"path": f"people/contacts/{name}", "content": BODY, "meta": META, "create": True},
        )
        assert r["success"], r
    plan = _run_tool(server, "kvault_plan", {"path": "people/contacts", "limit": 0})
    clusters = [i for i in plan["items"] if i["kind"] == "cluster"]
    assert clusters and clusters[0]["new_parent"] == "people/contacts/aio"
    dry = _run_tool(
        server, "kvault_move_entities", {"moves": clusters[0]["moves"], "dry_run": True}
    )
    assert dry["dry_run"] and dry["stubs"] == ["people/contacts/aio"]
    moved = _run_tool(server, "kvault_move_entities", {"moves": clusters[0]["moves"]})
    assert moved["success"] and moved["count"] == 3 and not moved.get("partial")
    assert "people/contacts/aio" in moved["ancestor_paths"]
    assert (kb / "people" / "contacts" / "aio" / "aio_c" / "_summary.md").exists()
