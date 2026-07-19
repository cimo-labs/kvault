"""Tests for MCP parity with the journal-first CLI workflow."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

import click
import pytest
from click.testing import CliRunner

from kvault.core import operations as ops
from kvault.core.frontmatter import build_frontmatter
from kvault.core.migration import migrate
from kvault.mcp.server import KVAULT_KB_ROOT_ENV, create_server, main, resolve_bound_root

pytest.importorskip("mcp.server.fastmcp")


def _tool_json(result):
    if isinstance(result, tuple):
        return result[1].get("result", result[1])
    assert len(result) == 1
    return json.loads(result[0].text)


def _run_tool(server, name, arguments):
    return _tool_json(asyncio.run(server.call_tool(name, arguments)))


def _summary(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_frontmatter(
            {
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "source": "mcp-test",
                "aliases": [],
            }
        )
        + body,
        encoding="utf-8",
    )


def _make_kb(tmp_path: Path, name: str = "kb", *, migrated: bool = True) -> Path:
    kb = tmp_path / name
    kb.mkdir()
    _summary(kb / "_summary.md", "# Test KB\n\nMCP test knowledge base.\n")
    _summary(kb / "people" / "_summary.md", "# People\n\nAll contacts.\n")
    _summary(kb / "people" / "contacts" / "_summary.md", "# Contacts\n\nAll contacts.\n")
    _summary(
        kb / "people" / "contacts" / "professional" / "_summary.md",
        "# Professional\n\nProfessional contacts.\n",
    )
    _summary(
        kb / "people" / "contacts" / "professional" / "education" / "_summary.md",
        "# Education\n\nEducation contacts.\n",
    )
    if migrated:
        result = migrate(kb, dry_run=False)
        assert result.success, result.errors
    return kb


def _summary_mutations(
    kb: Path,
    revisions: Dict[str, str],
    paths: List[str],
    note: str,
) -> List[Dict[str, Any]]:
    mutations: List[Dict[str, Any]] = []
    for path in paths:
        node = ops.read_node(kb, path, parents="none")
        assert node is not None
        mutations.append(
            {
                "operation": "summary",
                "path": path,
                "content": node["content"].rstrip() + f"\n\n{note}\n",
                "meta": {},
                "expected_revision": revisions[path],
            }
        )
    return mutations


def _create_plan(kb: Path, event_id: str, prepared: Dict[str, Any], name: str) -> Dict[str, Any]:
    target = f"people/contacts/professional/education/{name.lower()}"
    ancestors = [
        "people/contacts/professional/education",
        "people/contacts/professional",
        "people/contacts",
        "people",
        ".",
    ]
    return {
        "schema_version": 1,
        "event_ids": [event_id],
        "decisions": [
            {
                "event_id": event_id,
                "outcome": "apply",
                "reasoning": "This is a durable professional-contact fact.",
                "target_paths": [target],
            }
        ],
        "mutations": [
            {
                "operation": "create",
                "path": target,
                "content": f"# {name}\n\nDeep path from MCP.\n",
                "meta": {"aliases": [name]},
            },
            *_summary_mutations(
                kb,
                prepared["revisions"],
                ancestors,
                f"- {name}: education contact.",
            ),
        ],
        "reasoning": "Create the contact and refresh all ancestor summaries.",
        "requested_by": "mcp-test",
    }


def test_resolve_bound_root_from_env(tmp_path, monkeypatch):
    kb = _make_kb(tmp_path)
    monkeypatch.setenv(KVAULT_KB_ROOT_ENV, str(kb))
    assert resolve_bound_root() == kb.resolve()


def test_mcp_cli_requires_bound_root(monkeypatch):
    monkeypatch.delenv(KVAULT_KB_ROOT_ENV, raising=False)
    result = CliRunner().invoke(main, [])
    assert result.exit_code != 0
    assert "requires --kb-root" in result.output


def test_mcp_exposes_journal_first_surface_without_direct_mutations(tmp_path):
    server = create_server(_make_kb(tmp_path))
    tool_names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert {
        "kvault_init",
        "kvault_status",
        "kvault_capture",
        "kvault_events_list",
        "kvault_events_show",
        "kvault_events_import",
        "kvault_migrate",
        "kvault_tree",
        "kvault_search",
        "kvault_read_node",
        "kvault_list_nodes",
        "kvault_read_entity",
        "kvault_list_entities",
        "kvault_read_summary",
        "kvault_get_parent_summaries",
        "kvault_get_ancestors",
        "kvault_propagate_all",
        "kvault_reconcile_prepare",
        "kvault_reconcile_apply",
        "kvault_reconcile_approve",
        "kvault_reconcile_status",
        "kvault_reconcile_recover",
        "kvault_generate_daily_artifact",
        "kvault_validate_kb",
        "kvault_log_phase",
    }.issubset(tool_names)
    assert not {
        "kvault_write_node",
        "kvault_write_entity",
        "kvault_delete_entity",
        "kvault_move_entity",
        "kvault_write_summary",
        "kvault_update_summaries",
        "kvault_write_journal",
    }.intersection(tool_names)


def test_mcp_capture_prepare_apply_read_and_status(tmp_path):
    kb = _make_kb(tmp_path)
    server = create_server(kb)
    captured = _run_tool(
        server,
        "kvault_capture",
        {"content": "Ada is an education contact.", "source": "test", "source_ref": "m1"},
    )
    assert captured["success"] is True
    event_id = captured["event_id"]
    paths = [
        "people/contacts/professional/education",
        "people/contacts/professional",
        "people/contacts",
        "people",
        ".",
    ]
    prepared = _run_tool(
        server,
        "kvault_reconcile_prepare",
        {"event_ids": [event_id], "paths": paths},
    )
    assert prepared["success"] is True
    assert prepared["revisions"]["."].startswith("sha256:")

    applied = _run_tool(
        server,
        "kvault_reconcile_apply",
        {"plan": _create_plan(kb, event_id, prepared, "Ada")},
    )
    assert applied["success"] is True
    reconciliation_id = applied["reconciliation_id"]

    event = _run_tool(server, "kvault_events_show", {"event_id": event_id})
    assert event["event"]["state"]["state"] == "resolved"
    read = _run_tool(
        server,
        "kvault_read_entity",
        {"path": "people/contacts/professional/education/ada"},
    )
    assert read["success"] is True
    assert read["revision"].startswith("sha256:")
    search = _run_tool(server, "kvault_search", {"query": "deep path", "limit": 3})
    assert search["results"][0]["path"].endswith("/ada")
    status = _run_tool(
        server,
        "kvault_reconcile_status",
        {"reconciliation_id": reconciliation_id},
    )
    assert status["result"]["result"]["success"] is True
    assert _run_tool(server, "kvault_validate_kb", {})["success"] is True


def test_mcp_capture_conflicts_and_event_errors_are_structured(tmp_path):
    kb = _make_kb(tmp_path)
    server = create_server(kb)
    first = _run_tool(
        server,
        "kvault_capture",
        {"content": "Original candidate.", "source": "test", "source_ref": "message:1"},
    )
    assert first["success"] is True

    conflict = _run_tool(
        server,
        "kvault_capture",
        {"content": "Changed candidate.", "source": "test", "source_ref": "message:1"},
    )
    assert conflict["success"] is False
    assert conflict["error_code"] == "source_ref_conflict"

    invalid_status = _run_tool(server, "kvault_events_list", {"status": "unknown"})
    assert invalid_status["success"] is False
    assert invalid_status["error_code"] == "workflow_error"

    invalid_id = _run_tool(server, "kvault_events_show", {"event_id": "../escape"})
    assert invalid_id["success"] is False
    assert invalid_id["error_code"] == "workflow_error"


def test_mcp_sensitive_plan_needs_review_then_approval(tmp_path):
    kb = _make_kb(tmp_path)
    server = create_server(kb)
    captured = _run_tool(
        server,
        "kvault_capture",
        {"content": "Bob is an education contact.", "source": "test", "sensitivity": "sensitive"},
    )
    event_id = captured["event_id"]
    paths = [
        "people/contacts/professional/education",
        "people/contacts/professional",
        "people/contacts",
        "people",
        ".",
    ]
    prepared = _run_tool(
        server,
        "kvault_reconcile_prepare",
        {"event_ids": [event_id], "paths": paths},
    )
    review = _run_tool(
        server,
        "kvault_reconcile_apply",
        {"plan": _create_plan(kb, event_id, prepared, "Bob")},
    )
    assert review["status"] == "needs_review"
    assert not (kb / "people/contacts/professional/education/bob").exists()
    approved = _run_tool(
        server,
        "kvault_reconcile_approve",
        {"reconciliation_id": review["reconciliation_id"], "actor": "owner"},
    )
    assert approved["success"] is True
    assert (kb / "people/contacts/professional/education/bob/_summary.md").is_file()


def test_mcp_migration_and_moss_import_are_explicit(tmp_path):
    kb = _make_kb(tmp_path, migrated=False)
    server = create_server(kb)
    preview = _run_tool(server, "kvault_migrate", {"dry_run": True})
    assert preview["dry_run"] is True
    assert not (kb / ".kvault/schema.json").exists()
    applied = _run_tool(server, "kvault_migrate", {"dry_run": False})
    assert applied["success"] is True

    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(json.dumps({"id": "legacy-1", "content": "Imported candidate."}) + "\n")
    imported = _run_tool(
        server,
        "kvault_events_import",
        {"input_path": str(inbox), "dry_run": False},
    )
    assert imported["success"] is True
    assert imported["pending"] == 1
    events = _run_tool(server, "kvault_events_list", {"status": "pending"})
    assert any(event["source_ref"] == "legacy-1" for event in events["events"])


def test_mcp_root_bound_mismatch_rejected(tmp_path):
    bound = _make_kb(tmp_path, "bound")
    other = _make_kb(tmp_path, "other")
    server = create_server(bound)
    result = _run_tool(
        server,
        "kvault_reconcile_prepare",
        {"event_ids": ["missing"], "kg_root": str(other)},
    )
    assert result["success"] is False
    assert result["error_code"] == "validation_error"
    assert "different KB root" in result["error"]


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


def test_mcp_tree_outline_and_status(tmp_path):
    kb = _make_kb(tmp_path)
    server = create_server(kb)
    status = _run_tool(server, "kvault_status", {})
    assert status["success"] is True
    assert status["schema_version"] == 1
    assert status["version"] == "0.12.0"

    text_result = _run_tool(server, "kvault_tree", {})
    assert text_result["success"] is True
    assert text_result["total_nodes"] == 5
    assert isinstance(text_result["outline"], str)
    depth_result = _run_tool(server, "kvault_tree", {"depth": 1})
    assert "…3 nodes below" in depth_result["outline"]
    json_result = _run_tool(server, "kvault_tree", {"format": "json"})
    assert json_result["outline"]["descendants_count"] == 4
    assert _run_tool(server, "kvault_tree", {"format": "xml"})["success"] is False
    assert _run_tool(server, "kvault_tree", {"path": "nope"})["error_code"] == "not_found"
