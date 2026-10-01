"""Write-path integrity (0.17): what a field report found writes did to frontmatter.

- Content that starts with its own frontmatter block was written below the real
  one (the CLI parsed it out of stdin; the Python and MCP surfaces did not).
- An explicit ``meta`` on a summary rewrite replaced the frontmatter wholesale,
  so a rollup that passed only a title dropped ``source``, ``aliases`` and the
  ``kvault mark`` decisions, and their findings came back.
- ``update-summaries`` created directories for a typo'd path.
"""

import asyncio
import json
from pathlib import Path

import pytest

from kvault.core import operations as ops
from kvault.core.frontmatter import parse_frontmatter

META = {"source": "manual", "aliases": []}
EMBEDDED = "---\nsource: email:thread-1\naliases:\n- Northwind\ntitle: Northwind\n---\n# Northwind\n\nBody.\n"


def _kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / ".kvault").mkdir(parents=True)
    ops.write_summary(kb, ".", "# Root\n\nRoot.\n", meta=dict(META))
    ops.write_summary(kb, "accounts", "# Accounts\n\nAccounts.\n", meta=dict(META))
    return kb


def _blocks(path: Path) -> int:
    text = path.read_text()
    meta, body = parse_frontmatter(text)
    return (1 if meta else 0) + (1 if parse_frontmatter(body.lstrip())[0] else 0)


def _codes(result):
    return [n["code"] for n in result.get("notes", [])]


def test_write_node_merges_a_leading_frontmatter_block(tmp_path):
    kb = _kb(tmp_path)
    result = ops.write_node(
        kb, "accounts/northwind", EMBEDDED, meta={"source": "manual"}, create=True
    )
    assert result["success"], result
    summary = kb / "accounts" / "northwind" / "_summary.md"
    assert _blocks(summary) == 1
    meta, body = parse_frontmatter(summary.read_text())
    assert meta["source"] == "manual"  # explicit meta wins
    assert meta["aliases"] == ["Northwind"] and meta["title"] == "Northwind"  # filled from content
    assert body.lstrip().startswith("# Northwind")
    assert "guessed" in _codes(result)


def test_write_summary_and_update_summaries_merge_it_too(tmp_path):
    kb = _kb(tmp_path)
    ops.write_summary(kb, "accounts/northwind", "# Old\n", meta=dict(META))
    assert ops.write_summary(kb, "accounts/northwind", EMBEDDED)["success"]
    assert _blocks(kb / "accounts" / "northwind" / "_summary.md") == 1
    result = ops.update_summaries(kb, [{"path": "accounts", "content": EMBEDDED}])
    assert result["success"] and _blocks(kb / "accounts" / "_summary.md") == 1
    assert "guessed" in _codes(result)


def test_mcp_update_summaries_with_a_full_document(tmp_path):
    pytest.importorskip("mcp.server.fastmcp")
    from kvault.mcp.server import create_server

    kb = _kb(tmp_path)
    server = create_server(kb)
    asyncio.run(
        server.call_tool(
            "kvault_update_summaries", {"updates": [{"path": "accounts", "content": EMBEDDED}]}
        )
    )
    assert _blocks(kb / "accounts" / "_summary.md") == 1


def test_validate_reports_stacked_frontmatter(tmp_path):
    kb = _kb(tmp_path)
    (kb / "accounts" / "_summary.md").write_text(
        "---\nsource: manual\naliases: []\n---\n" + EMBEDDED
    )
    issues = [i for i in ops.validate_kb(kb)["issues"] if i["type"] == "stacked_frontmatter"]
    assert [i["path"] for i in issues] == ["accounts"]


def test_a_rollup_with_only_a_title_keeps_every_recorded_decision(tmp_path):
    kb = _kb(tmp_path)
    decisions = {
        "distinct_from": ["vendors"],
        "max_children": 14,
        "series_ok": True,
        "verify_by": "2026-12-01",
    }
    ops.write_summary(kb, "accounts", "# Accounts\n", meta={**META, **decisions})
    ops.update_summaries(
        kb,
        [{"path": "accounts", "content": "# Accounts\n\nRollup.\n", "meta": {"title": "Accounts"}}],
    )
    meta, _ = parse_frontmatter((kb / "accounts" / "_summary.md").read_text())
    assert {k: meta.get(k) for k in decisions} == decisions
    assert meta["source"] == "manual" and meta["title"] == "Accounts"


def test_update_summaries_never_creates_a_path(tmp_path):
    kb = _kb(tmp_path)
    result = ops.update_summaries(
        kb,
        [{"path": "acounts/typo", "content": "# Typo\n"}, {"path": "accounts", "content": "# A\n"}],
    )
    assert result["updated"] == ["accounts"] and result["partial"] is True
    assert result["errors"][0]["path"] == "acounts/typo"
    assert not (kb / "acounts").exists()
    assert json.dumps(result)  # one JSON-serializable document


def test_summary_rules_are_reported_by_the_write(tmp_path):
    """0.17: a rollup that misses a child is reported by update-summaries itself,
    not only by the next kvault check."""
    kb = _kb(tmp_path)
    for name in ("northwind", "contoso"):
        ops.write_node(
            kb,
            f"accounts/{name}",
            f"# {name.title()}\n\nAn account.\n",
            meta=dict(META),
            create=True,
        )
    partial = ops.update_summaries(
        kb, [{"path": "accounts", "content": "# Accounts\n\n- `northwind/` — an account\n"}]
    )
    codes = {w["code"] for w in partial.get("summary_warnings", [])}
    assert "missing_child_coverage" in codes
    full = ops.update_summaries(
        kb,
        [
            {
                "path": "accounts",
                "content": "# Accounts\n\nTwo accounts.\n\n- `northwind/` — an account\n"
                "- `contoso/` — an account\n",
            }
        ],
    )
    assert "missing_child_coverage" not in {w["code"] for w in full.get("summary_warnings", [])}
