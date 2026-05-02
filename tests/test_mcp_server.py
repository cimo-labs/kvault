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
        "kvault_read_entity",
        "kvault_write_entity",
        "kvault_list_entities",
        "kvault_delete_entity",
        "kvault_move_entity",
        "kvault_read_summary",
        "kvault_write_summary",
        "kvault_update_summaries",
        "kvault_get_parent_summaries",
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
