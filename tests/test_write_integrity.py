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


# ── 0.17 review: what the split must never touch ───────────────────────

STACKED = (
    "---\nsource: imessage:thread-42\naliases:\n- Bob\n- Robert Smith\ncreated: '2026-01-01'\n"
    "updated: '2026-01-01'\n---\n---\nsource: manual\naliases:\n- Bob\n---\n# Bob\n\nLikes tea.\n"
)


def test_a_patch_on_a_stacked_node_keeps_the_real_frontmatter(tmp_path):
    """The lower block of a stacked file holds stale keys; content read back
    from the file is never split, so they cannot land over the real ones."""
    kb = _kb(tmp_path)
    for via in ("write_node", "update_summaries"):
        node = kb / "accounts" / "bob"
        node.mkdir(parents=True, exist_ok=True)
        (node / "_summary.md").write_text(STACKED)
        patch = [{"old_str": "Likes tea.", "new_str": "Likes coffee."}]
        if via == "write_node":
            assert ops.write_node(kb, "accounts/bob", patches=patch)["success"]
        else:
            assert ops.update_summaries(kb, [{"path": "accounts/bob", "patches": patch}])["updated"]
        meta, body = parse_frontmatter((node / "_summary.md").read_text())
        assert meta["source"] == "imessage:thread-42", via
        assert meta["aliases"] == ["Bob", "Robert Smith"] and meta["created"] == "2026-01-01"
        assert "Likes coffee." in body and body.lstrip().startswith("---")  # left for validate


def test_a_body_framed_by_rules_is_body_text(tmp_path):
    """A ---framed heading parses as YAML comments, an empty block: it was dropped."""
    kb = _kb(tmp_path)
    framed = "---\n# Weekly Review\n## Week 39\n---\n\nShipped the parser.\n"
    assert ops.write_node(kb, "accounts/review", framed, meta=dict(META), create=True)["success"]
    summary = kb / "accounts" / "review" / "_summary.md"
    assert "# Weekly Review" in summary.read_text()
    ops.write_node(
        kb, "accounts/review", patches=[{"old_str": "parser.", "new_str": "parser and CLI."}]
    )
    ops.mark_node(kb, "accounts/review", verify_by="2027-01-01")
    assert "# Weekly Review" in summary.read_text() and "parser and CLI." in summary.read_text()
    assert not [i for i in ops.validate_kb(kb)["issues"] if i["type"] == "stacked_frontmatter"]


def test_a_block_with_date_keys_is_body_text(tmp_path):
    kb = _kb(tmp_path)
    result = ops.write_node(
        kb,
        "accounts/sam",
        "---\n2026-09-30: Met Sam\n---\n# Sam\n",
        meta=dict(META),
        create=True,
        reasoning="met Sam",
    )
    assert result["success"] and result["journal_logged"], result
    assert "2026-09-30: Met Sam" in (kb / "accounts" / "sam" / "_summary.md").read_text()


def test_validate_does_not_mistake_a_rule_for_a_block(tmp_path):
    kb = _kb(tmp_path)
    (kb / "accounts" / "_summary.md").write_text(
        "---\nsource: manual\naliases: []\n---\n \n---\n\nJust a horizontal rule.\n"
    )
    assert not [i for i in ops.validate_kb(kb)["issues"] if i["type"] == "stacked_frontmatter"]


def test_summary_warnings_are_capped(tmp_path):
    kb = _kb(tmp_path)
    for i in range(15):
        ops.write_node(
            kb, f"accounts/a{i:02d}", "# A\n\nAn account.\n", meta=dict(META), create=True
        )
    result = ops.update_summaries(kb, [{"path": "accounts", "content": "# Accounts\n\nAll.\n"}])
    for warning in result.get("summary_warnings", []):
        for key, value in warning["details"].items():
            assert not isinstance(value, list) or len(value) <= ops.SUMMARY_WARNING_LIST_MAX, key


def test_cli_update_summaries_says_why_every_item_failed(tmp_path):
    from click.testing import CliRunner

    from kvault.cli.main import cli

    kb = _kb(tmp_path)
    out = CliRunner().invoke(
        cli,
        ["update-summaries", "--kb-root", str(kb)],
        input=json.dumps([{"path": "acounts", "content": "# A\n"}]),
    )
    assert out.exit_code != 0 and "acounts: No summary at this path" in out.output
