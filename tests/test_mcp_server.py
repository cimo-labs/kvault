"""Tests for the thin MCP compatibility server."""

from __future__ import annotations

import asyncio
import json

import click
import pytest
from click.testing import CliRunner

from kvault.mcp.server import KVAULT_KB_ROOT_ENV, create_server, main, resolve_bound_root

pytest.importorskip("mcp.server.fastmcp")


def _tool_json(result):
    if isinstance(result, tuple):
        return result[1].get("result", result[1])
    assert len(result) == 1
    return json.loads(result[0].text)


def _run_tool(server, name, arguments):
    return _tool_json(asyncio.run(server.call_tool(name, arguments)))


def _make_kb(tmp_path, name="kb"):
    kb = tmp_path / name
    kb.mkdir()
    (kb / ".kvault").mkdir()
    (kb / "_summary.md").write_text("# Test KB\n\nMCP test knowledge base.\n")
    (kb / "people").mkdir()
    (kb / "people" / "_summary.md").write_text("# People\n\nAll contacts.\n")
    (kb / "people" / "contacts" / "professional" / "education").mkdir(parents=True)
    (kb / "people" / "contacts" / "_summary.md").write_text("# Contacts\n\nAll contacts.\n")
    (kb / "people" / "contacts" / "professional" / "_summary.md").write_text(
        "# Professional\n\nProfessional contacts.\n"
    )
    (kb / "people" / "contacts" / "professional" / "education" / "_summary.md").write_text(
        "# Education\n\nEducation contacts.\n"
    )
    return kb


def test_resolve_bound_root_from_env(tmp_path, monkeypatch):
    kb = _make_kb(tmp_path)
    monkeypatch.setenv(KVAULT_KB_ROOT_ENV, str(kb))

    assert resolve_bound_root() == kb.resolve()


def test_mcp_cli_requires_bound_root(monkeypatch):
    monkeypatch.delenv(KVAULT_KB_ROOT_ENV, raising=False)

    result = CliRunner().invoke(main, [])

    assert result.exit_code != 0
    assert "requires --kb-root" in result.output


def test_mcp_server_exposes_compatible_tools_and_calls(tmp_path):
    kb = _make_kb(tmp_path)
    server = create_server(kb)

    tool_names = {tool.name for tool in asyncio.run(server.list_tools())}

    assert {
        "kvault_init",
        "kvault_status",
        "kvault_search",
        "kvault_read_node",
        "kvault_write_node",
        "kvault_list_nodes",
        "kvault_read_entity",
        "kvault_write_entity",
        "kvault_list_entities",
        "kvault_delete_entity",
        "kvault_move_entity",
        "kvault_read_summary",
        "kvault_write_summary",
        "kvault_prepare_summary_update",
        "kvault_write_parent_summary",
        "kvault_update_summaries",
        "kvault_get_parent_summaries",
        "kvault_get_ancestors",
        "kvault_propagate_all",
        "kvault_write_journal",
        "kvault_generate_daily_artifact",
        "kvault_validate_kb",
        "kvault_log_phase",
    }.issubset(tool_names)

    status = _run_tool(server, "kvault_status", {})
    assert status["success"] is True
    assert status["kg_root"] == str(kb.resolve())

    write = _run_tool(
        server,
        "kvault_write_entity",
        {
            "path": "people/contacts/professional/education/person",
            "content": "# Person\n\nDeep path from MCP.\n",
            "create": True,
        },
    )
    assert write["success"] is True

    read = _run_tool(
        server,
        "kvault_read_entity",
        {"path": "people/contacts/professional/education/person"},
    )
    assert read["success"] is True
    assert read["meta"]["source"] == "auto:mcp"

    node = _run_tool(
        server,
        "kvault_read_node",
        {"path": "people/contacts/professional/education/person"},
    )
    assert node["success"] is True
    assert node["parent"]["path"] == "people/contacts/professional/education"

    nodes = _run_tool(server, "kvault_list_nodes", {"path": "people", "recursive": True})
    assert nodes["success"] is True
    assert any(
        item["path"] == "people/contacts/professional/education/person" for item in nodes["nodes"]
    )

    search = _run_tool(server, "kvault_search", {"query": "deep path", "limit": 3})
    assert search["success"] is True
    assert any(
        item["path"] == "people/contacts/professional/education/person"
        for item in search["results"]
    )


def test_mcp_strict_summary_update_tools_prepare_and_write(tmp_path):
    kb = _make_kb(tmp_path)
    server = create_server(kb)

    prepared = _run_tool(server, "kvault_prepare_summary_update", {"path": "people/contacts"})
    assert prepared["success"] is True
    assert prepared["parent"]["path"] == "people/contacts"
    assert [child["path"] for child in prepared["children"]] == ["people/contacts/professional"]
    assert prepared["children_digest"].startswith("sha256:")

    write = _run_tool(
        server,
        "kvault_write_parent_summary",
        {
            "path": "people/contacts",
            "content": "# Contacts\n\nProfessional contacts are summarized here.\n",
            "children_digest": prepared["children_digest"],
        },
    )
    assert write["success"] is True
    assert write["path"] == "people/contacts"
    assert (
        "Professional contacts are summarized here"
        in (kb / "people" / "contacts" / "_summary.md").read_text()
    )


def test_mcp_strict_summary_update_rejects_stale_digest(tmp_path):
    kb = _make_kb(tmp_path)
    server = create_server(kb)

    prepared = _run_tool(server, "kvault_prepare_summary_update", {"path": "people/contacts"})
    (kb / "people" / "contacts" / "vendors").mkdir()
    (kb / "people" / "contacts" / "vendors" / "_summary.md").write_text(
        "# Vendors\n\nVendor contacts.\n"
    )

    result = _run_tool(
        server,
        "kvault_write_parent_summary",
        {
            "path": "people/contacts",
            "content": "# Contacts\n\nStale write.\n",
            "children_digest": prepared["children_digest"],
        },
    )

    assert result["success"] is False
    assert result["error_code"] == "workflow_error"
    assert result["details"]["expected_digest"].startswith("sha256:")


def test_mcp_strict_summary_update_returns_hierarchy_hint(tmp_path):
    kb = _make_kb(tmp_path)
    server = create_server(kb)
    parent = kb / "projects"
    parent.mkdir()
    (parent / "_summary.md").write_text("# Projects\n\nProject rollup.\n")
    for index in range(11):
        child = parent / f"child_{index}"
        child.mkdir()
        (child / "_summary.md").write_text(f"# Child {index}\n\nProject child.\n")

    prepared = _run_tool(server, "kvault_prepare_summary_update", {"path": "projects"})

    assert prepared["success"] is True
    assert prepared["child_count"] == 11
    assert prepared["hierarchy_hint"]["code"] == "too_many_direct_children"


def test_mcp_root_bound_mismatch_rejected(tmp_path):
    bound = _make_kb(tmp_path, "bound")
    other = _make_kb(tmp_path, "other")
    server = create_server(bound)

    result = _run_tool(
        server,
        "kvault_prepare_summary_update",
        {"path": ".", "kg_root": str(other)},
    )

    assert result["success"] is False
    assert result["error_code"] == "validation_error"
    assert "different KB root" in result["error"]


def test_mcp_legacy_summary_tools_remain_callable(tmp_path):
    kb = _make_kb(tmp_path)
    server = create_server(kb)

    write = _run_tool(
        server,
        "kvault_write_summary",
        {"path": "people", "content": "# People\n\nLegacy summary write.\n"},
    )
    assert write["success"] is True

    read = _run_tool(server, "kvault_read_summary", {"path": "people"})
    assert read["success"] is True
    assert "Legacy summary write" in read["content"]

    ancestors = _run_tool(
        server,
        "kvault_get_ancestors",
        {"path": "people/contacts/professional/education"},
    )
    assert ancestors["success"] is True
    assert any(item["path"] == "people/contacts" for item in ancestors["ancestors"])

    propagated = _run_tool(
        server,
        "kvault_propagate_all",
        {"path": "people/contacts/professional/education"},
    )
    assert propagated["success"] is True

    batch = _run_tool(
        server,
        "kvault_update_summaries",
        {"updates": [{"path": "people", "content": "# People\n\nBatch compatibility write.\n"}]},
    )
    assert batch["success"] is True


def test_mcp_strict_parent_workflow_for_written_deep_node(tmp_path):
    kb = _make_kb(tmp_path)
    server = create_server(kb)

    write = _run_tool(
        server,
        "kvault_write_node",
        {
            "path": "people/contacts/professional/education/person",
            "content": "# Person\n\nDeep node from MCP workflow.\n",
            "create": True,
        },
    )
    assert write["success"] is True

    stale_contacts = _run_tool(
        server,
        "kvault_prepare_summary_update",
        {"path": "people/contacts"},
    )

    for path in [
        "people/contacts/professional/education",
        "people/contacts/professional",
        "people/contacts",
        "people",
        ".",
    ]:
        prepared = _run_tool(server, "kvault_prepare_summary_update", {"path": path})
        result = _run_tool(
            server,
            "kvault_write_parent_summary",
            {
                "path": path,
                "content": prepared["parent"]["content"].rstrip()
                + f"\n\nStrict rollup updated for {path}.\n",
                "children_digest": prepared["children_digest"],
            },
        )
        assert result["success"] is True

    stale = _run_tool(
        server,
        "kvault_write_parent_summary",
        {
            "path": "people/contacts",
            "content": "# Contacts\n\nOut-of-order stale update.\n",
            "children_digest": stale_contacts["children_digest"],
        },
    )
    assert stale["success"] is False
    assert stale["error_code"] == "workflow_error"


def test_mcp_allowed_roots_blocks_disallowed_root(tmp_path, monkeypatch):
    allowed = _make_kb(tmp_path, "allowed")
    other = _make_kb(tmp_path, "other")
    monkeypatch.setenv("KVAULT_ALLOWED_ROOTS", str(allowed))

    with pytest.raises(click.ClickException):
        resolve_bound_root(other)

    server = create_server(allowed)
    result = _run_tool(server, "kvault_init", {"kg_root": str(other)})

    assert result["success"] is False
    assert result["error_code"] == "validation_error"
