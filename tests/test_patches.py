"""Patch mode (0.17): edit a body by exact replacements instead of resending it.

A field report measured 40-90 KB of output per propagation chain when every
hub summary (15-19 KB each) had to be rewritten whole to change a line, and
agents abandoned chains midway. A patch is ``{old_str, new_str}``: each
``old_str`` must occur exactly once in the body as patched so far, and any
miss writes nothing.
"""

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import operations as ops
from kvault.core.frontmatter import parse_frontmatter

META = {"source": "manual", "aliases": []}
BODY = "# Hub\n\nAlpha line.\nBeta line.\nGamma line.\n"


def _kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / ".kvault").mkdir(parents=True)
    ops.write_summary(kb, ".", "# Root\n\nRoot.\n", meta=dict(META))
    ops.write_node(kb, "hub", BODY, meta=dict(META, verify_by="2027-01-01"), create=True)
    return kb


def _summary(kb: Path, rel: str = "hub") -> Path:
    return kb / rel / "_summary.md"


def _patch(old: str, new: str) -> dict:
    return {"old_str": old, "new_str": new}


def test_a_patch_edits_only_its_text_and_keeps_the_frontmatter(tmp_path):
    kb = _kb(tmp_path)
    result = ops.write_node(kb, "hub", patches=[_patch("Beta line.", "Beta, revised.")])
    assert result["success"] and result["changed"], result
    assert result["propagation_required"] and result["ancestor_paths"] == ["."]
    meta, body = parse_frontmatter(_summary(kb).read_text())
    assert body.strip() == BODY.replace("Beta line.", "Beta, revised.").strip()
    assert meta["verify_by"] == "2027-01-01" and meta["source"] == "manual"


def test_patches_apply_in_order(tmp_path):
    kb = _kb(tmp_path)
    result = ops.write_node(
        kb, "hub", patches=[_patch("Alpha line.", "Alpha two."), _patch("Alpha two.", "Alpha 2.")]
    )
    assert result["success"], result
    assert "Alpha 2." in _summary(kb).read_text()


@pytest.mark.parametrize(
    "patches, fragment",
    [
        ([_patch("Delta line.", "x")], "not found"),
        ([_patch("line.", "x")], "matches 3 places"),
        ([_patch("", "x")], "is empty"),
        ([{"old_str": "Alpha line."}], "exactly old_str and new_str"),
        ([], "non-empty list"),
        # The first patch is valid; the second misses, so neither is written.
        ([_patch("Alpha line.", "A."), _patch("Delta line.", "x")], "patch 2"),
    ],
)
def test_a_miss_writes_nothing(tmp_path, patches, fragment):
    kb = _kb(tmp_path)
    before = _summary(kb).read_bytes()
    result = ops.write_node(kb, "hub", patches=patches)
    assert not result["success"] and fragment in result["error"], result
    assert _summary(kb).read_bytes() == before


def test_patches_need_an_existing_node_and_no_content(tmp_path):
    kb = _kb(tmp_path)
    p = [_patch("Alpha line.", "A.")]
    assert "not both" in ops.write_node(kb, "hub", "# New\n", patches=p)["error"]
    assert "create needs content" in ops.write_node(kb, "new", create=True, patches=p)["error"]
    assert ops.write_node(kb, "missing", patches=p)["error_code"] == "not_found"
    assert "content is required" in ops.write_node(kb, "hub")["error"]


def test_a_patch_that_changes_nothing_keeps_the_dates(tmp_path):
    kb = _kb(tmp_path)
    path = _summary(kb)
    today = parse_frontmatter(path.read_text())[0]["updated"]
    path.write_text(path.read_text().replace(f"updated: '{today}'", "updated: '2001-01-01'"))
    result = ops.write_node(kb, "hub", patches=[_patch("Alpha line.", "Alpha line.")])
    assert result["success"] and result["changed"] is False
    assert parse_frontmatter(path.read_text())[0]["updated"] == "2001-01-01"


def test_update_summaries_items_take_patches(tmp_path):
    kb = _kb(tmp_path)
    result = ops.update_summaries(
        kb,
        [
            {"path": "hub", "patches": [_patch("Gamma line.", "Gamma, revised.")]},
            {"path": ".", "patches": [_patch("No such text.", "x")]},
            {"path": ".", "content": "# Root\n", "patches": [_patch("Root.", "R.")]},
        ],
    )
    assert result["updated"] == ["hub"] and result["partial"] is True
    errors = {e["error"] for e in result["errors"]}
    assert any("not found" in e for e in errors) and any("not both" in e for e in errors)
    assert "Gamma, revised." in _summary(kb).read_text()
    assert "Root." in (kb / "_summary.md").read_text()


def test_update_summaries_refuses_unknown_item_keys(tmp_path):
    """A typo such as "metadata" for "meta" used to be dropped silently."""
    kb = _kb(tmp_path)
    result = ops.update_summaries(
        kb, [{"path": "hub", "content": "# Hub\n", "metadata": {"title": "Hub"}}]
    )
    assert result["updated"] == [] and "metadata" in result["errors"][0]["error"]
    assert "Alpha line." in _summary(kb).read_text()


def test_cli_write_patches(tmp_path):
    kb = _kb(tmp_path)
    runner = CliRunner()
    out = runner.invoke(
        cli,
        ["write", "hub", "--patches", "--json", "--kb-root", str(kb)],
        input=json.dumps([_patch("Alpha line.", "Alpha, via the CLI.")]),
    )
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["changed"] is True
    assert "Alpha, via the CLI." in _summary(kb).read_text()
    miss = [_patch("Nowhere.", "x")]
    as_json = runner.invoke(
        cli, ["write", "hub", "--patches", "--json", "--kb-root", str(kb)], input=json.dumps(miss)
    )
    assert json.loads(as_json.output)["success"] is False
    as_text = runner.invoke(
        cli, ["write", "hub", "--patches", "--kb-root", str(kb)], input=json.dumps(miss)
    )
    assert as_text.exit_code != 0 and "not found" in as_text.output


def test_mcp_patches_and_typed_updates(tmp_path):
    pytest.importorskip("mcp.server.fastmcp")
    from kvault.mcp.server import create_server

    kb = _kb(tmp_path)
    server = create_server(kb)
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    item = tools["kvault_update_summaries"].inputSchema["$defs"]["SummaryUpdate"]
    assert item["required"] == ["path"] and item["additionalProperties"] is False
    assert tools["kvault_write_node"].inputSchema["required"] == ["path"]

    asyncio.run(
        server.call_tool(
            "kvault_write_node", {"path": "hub", "patches": [_patch("Alpha line.", "A1.")]}
        )
    )
    asyncio.run(
        server.call_tool(
            "kvault_update_summaries",
            {"updates": [{"path": "hub", "patches": [_patch("Beta line.", "B1.")]}]},
        )
    )
    text = _summary(kb).read_text()
    assert "A1." in text and "B1." in text
    with pytest.raises(Exception, match="summary"):
        asyncio.run(
            server.call_tool(
                "kvault_update_summaries", {"updates": [{"path": "hub", "summary": "# Hub\n"}]}
            )
        )
