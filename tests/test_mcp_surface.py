"""MCP surface (0.17), from a field report by an MCP-only agent.

- Capture and the event queue had no MCP tools, so an MCP-only agent could
  not record evidence before writing from it, or close a queue entry.
- Several results overflowed clients that inline about 4 KB of tool output
  (the tree, move/delete ancestor documents, the daily artifact).
- Unknown arguments were dropped without a word, so ``budget=2000`` on
  kvault_read_nodes and ``old_path`` on kvault_move_entity silently ran with
  defaults.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from kvault.core import operations as ops

pytest.importorskip("mcp.server.fastmcp")

from kvault.mcp.server import create_server  # noqa: E402

META = {"source": "manual", "aliases": []}


def _run(server, name, arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple):
        return result[1].get("result", result[1])
    return json.loads(result[0].text)


def _kb(tmp_path):
    kb = tmp_path / "kb"
    (kb / ".kvault").mkdir(parents=True)
    ops.write_summary(kb, ".", "# Root\n\nRoot.\n", meta=dict(META))
    ops.write_node(
        kb, "people", "# People\n\nPeople.\n", meta=dict(META), create=True, new_root=True
    )
    return kb


def test_capture_promote_resolve_and_retract_over_mcp(tmp_path):
    kb = _kb(tmp_path)
    server = create_server(kb)
    captured = _run(
        server,
        "kvault_capture",
        {"content": "Ada Park joined as the new lead.", "source": "email", "source_ref": "m-1"},
    )
    event_id = captured["event_id"]
    pending = _run(server, "kvault_events", {})
    assert [e["id"] for e in pending["events"]] == [event_id]

    written = _run(
        server,
        "kvault_write_node",
        {
            "path": "people/ada_park",
            "content": "# Ada Park\n\nThe new lead.\n",
            "create": True,
            "event_ids": [event_id],
        },
    )
    assert written["success"], written
    node = ops.read_node(kb, "people/ada_park")
    assert f"journal:{event_id}" in node["meta"]["source_refs"]
    assert _run(server, "kvault_events", {})["count"] == 0

    second = _run(server, "kvault_capture", {"content": "A routine notice.", "source": "email"})
    resolved = _run(
        server,
        "kvault_events",
        {
            "action": "resolve",
            "event_id": second["event_id"],
            "outcome": "journal_only",
            "note": "nothing durable",
        },
    )
    assert resolved["success"], resolved
    assert (
        "needs outcome"
        in _run(server, "kvault_events", {"action": "resolve", "event_id": second["event_id"]})[
            "error"
        ]
    )

    retracted = _run(
        server,
        "kvault_events",
        {"action": "retract", "event_id": event_id, "reason": "wrong person"},
    )
    assert retracted["success"], retracted
    shown = _run(server, "kvault_events", {"action": "show", "event_id": event_id})
    assert shown["event"]["resolution"]["outcome"] == "retracted"

    ops_rows = _run(server, "kvault_log_tail", {"limit": 10})["ops"]
    assert {"capture", "events-resolve", "events-retract"} <= {r["op"] for r in ops_rows}
    assert {r["surface"] for r in ops_rows} == {"mcp"}


def test_unknown_arguments_are_refused(tmp_path):
    server = create_server(_kb(tmp_path))
    with pytest.raises(Exception, match="bogus"):
        asyncio.run(server.call_tool("kvault_search", {"query": "people", "bogus": 1}))


def test_read_nodes_budget_and_its_aliases(tmp_path):
    kb = _kb(tmp_path)
    for i in range(6):
        ops.write_node(
            kb, f"people/p{i}", f"# P{i}\n\n" + "word " * 400, meta=dict(META), create=True
        )
    server = create_server(kb)
    paths = [f"people/p{i}" for i in range(6)]
    default = _run(server, "kvault_read_nodes", {"paths": paths})
    assert len(json.dumps(default)) < 4096
    for alias in ("total_max_chars", "max_total_chars", "budget", "max_chars"):
        small = _run(server, "kvault_read_nodes", {"paths": paths, alias: 900})
        assert len(json.dumps(small)) < len(json.dumps(default)), alias


def test_tree_fits_its_budget_and_says_what_it_cut(tmp_path):
    kb = _kb(tmp_path)
    for i in range(30):
        ops.write_node(
            kb,
            f"people/person_{i:02d}",
            f"# Person number {i:02d} with a long descriptive title\n\nx.\n",
            meta=dict(META),
            create=True,
        )
        ops.write_node(
            kb,
            f"people/person_{i:02d}/notes",
            "# Notes\n\nx.\n",
            meta=dict(META),
            create=True,
        )
    server = create_server(kb)
    tree = _run(server, "kvault_tree", {"max_chars": 1500, "max_children": 50})
    assert len(tree["outline"]) <= 1500
    note = next(n for n in tree["notes"] if n["code"] == "truncated")
    assert note["detail"]["max_chars"] == 1500 and note["detail"]["full_chars"] > 1500
    full = _run(server, "kvault_tree", {"max_chars": 0, "max_children": 50})
    assert "truncated" not in {n["code"] for n in full.get("notes", [])}
    # Past depth 1 the outline is cut at a line, marker included in the budget.
    cut = _run(
        server, "kvault_tree", {"path": "people", "depth": 1, "max_chars": 600, "max_children": 50}
    )
    assert len(cut["outline"]) <= 600 and cut["outline"].endswith("more lines)")


def test_move_and_delete_return_paths_not_documents(tmp_path):
    kb = _kb(tmp_path)
    ops.write_node(kb, "people/ada", "# Ada\n\nAda.\n", meta=dict(META), create=True)
    ops.write_node(kb, "teams", "# Teams\n\nTeams.\n", meta=dict(META), create=True, new_root=True)
    server = create_server(kb)
    moved = _run(server, "kvault_move_entity", {"old_path": "people/ada", "new_path": "teams/ada"})
    assert moved["success"], moved
    assert "ancestors" not in moved and moved["ancestor_paths"]
    assert (kb / "teams" / "ada" / "_summary.md").is_file()
    deleted = _run(server, "kvault_delete_entity", {"path": "teams/ada"})
    assert deleted["success"] and "ancestors" not in deleted


def test_daily_artifact_content_only_on_request(tmp_path):
    kb = _kb(tmp_path)
    server = create_server(kb)
    lean = _run(server, "kvault_generate_daily_artifact", {"artifact_date": "2026-01-05"})
    assert lean["success"] and "content" not in lean and lean["content_chars"] > 0
    full = _run(
        server,
        "kvault_generate_daily_artifact",
        {"artifact_date": "2026-01-05", "force": True, "include_content": True},
    )
    assert len(full["content"]) == full["content_chars"]


def test_results_are_one_compact_json_text_block(tmp_path):
    """FastMCP indented every result and sent a structured copy beside it."""
    server = create_server(_kb(tmp_path))
    result = asyncio.run(server.call_tool("kvault_search", {"query": "people"}))
    assert not isinstance(result, tuple)  # no structured copy
    (block,) = result
    assert "\n" not in block.text and json.loads(block.text)["success"] is True
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert tools["kvault_search"].outputSchema is None


def test_compact_search_notes_carry_code_text_and_next(tmp_path):
    kb = _kb(tmp_path)
    for i in range(12):
        ops.write_node(
            kb,
            f"people/p{i:02d}",
            f"# Person {i}\n\nA zebra keeper.\n",
            meta=dict(META),
            create=True,
        )
    server = create_server(kb)
    result = _run(server, "kvault_search", {"query": "zebra"})
    assert result["notes"] and all(set(n) <= {"code", "text", "next"} for n in result["notes"])
    full = _run(server, "kvault_search", {"query": "zebra", "compact": False})
    assert any("detail" in n for n in full["notes"])
