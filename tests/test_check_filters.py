"""check --code / --max-findings / --max-lines (0.16).

A maintenance agent working one finding code at a time needs that code's
complete list. Before 0.16 the CLI printed five lines per prefix, the JSON
kept fifty per code with no way to raise it from the CLI, and there was no
filter: on a KB with 132 sibling findings the cross-folder twins sat in
"+127 more" where no agent looked.
"""

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core.check import normalize_codes, run_checks


def _node(kb: Path, rel: str, body: str = "# Node\n\nA node.\n") -> None:
    d = kb if rel == "." else kb / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "_summary.md").write_text(
        "---\nupdated: '2026-09-01'\nsource: manual\naliases: []\n---\n" + body
    )


def _kb_with_ghosts(tmp_path: Path, n: int = 60) -> Path:
    """One parent with *n* summary-less children: a BRANCH (hard) and n GHOSTs."""
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".kvault").mkdir()
    _node(kb, ".", "# Root\n\nRoot.\n")
    _node(kb, "area")
    for i in range(n):
        (kb / "area" / f"ghost{i:02d}").mkdir()
    return kb


def test_normalize_codes_accepts_lists_commas_case_and_prefix_colons():
    assert normalize_codes(None) is None
    assert normalize_codes([]) is None
    assert normalize_codes(["siblings, ghost:", "GHOST"]) == ["SIBLINGS", "GHOST"]
    assert normalize_codes("loose") == ["LOOSE"]
    with pytest.raises(ValueError, match="valid codes"):
        normalize_codes(["SIBLING"])


def test_codes_limit_what_runs_and_what_success_means(tmp_path):
    kb = _kb_with_ghosts(tmp_path)
    full = run_checks(kb)
    assert full["success"] is False and "codes" not in full
    ghosts = run_checks(kb, codes=["GHOST"])
    assert {f["code"] for f in ghosts["findings"]} == {"GHOST"}
    assert ghosts["success"] is True  # BRANCH was not selected
    assert ghosts["codes"] == ["GHOST"]
    assert ghosts["warnings"] == [] and ghosts["summary_warnings"] == []
    branch = run_checks(kb, codes=["BRANCH"])
    assert branch["success"] is False
    assert {f["code"] for f in branch["findings"]} == {"BRANCH"}


def test_max_findings_zero_returns_every_finding(tmp_path):
    kb = _kb_with_ghosts(tmp_path)
    capped = run_checks(kb, codes=["GHOST"])
    assert len(capped["findings"]) == 50 and capped["truncated"] == {"GHOST": 10}
    everything = run_checks(kb, codes=["GHOST"], max_findings=0)
    assert len(everything["findings"]) == 60 and everything["truncated"] == {}


def test_cli_code_filter_and_line_limits(tmp_path):
    kb = _kb_with_ghosts(tmp_path)
    runner = CliRunner()
    base = ["check", "--kb-root", str(kb), "--code", "GHOST"]

    default = runner.invoke(cli, base)
    assert default.exit_code == 0, default.output
    assert not default.output.startswith("[KB]")
    lines = default.output.splitlines()
    assert sum(1 for ln in lines if ln.startswith("GHOST: area/")) == 5
    assert "GHOST: (+55 more)" in lines  # 45 hidden by the line cap + 10 by the finding cap

    every = runner.invoke(cli, base + ["--max-lines", "0", "--max-findings", "0"])
    lines = every.output.splitlines()
    assert sum(1 for ln in lines if ln.startswith("GHOST: area/")) == 60
    assert not any("more)" in ln for ln in lines)

    old_name = runner.invoke(cli, base + ["--summary-max-warnings", "1"])
    assert sum(1 for ln in old_name.output.splitlines() if ln.startswith("GHOST: area/")) == 1

    hard = runner.invoke(cli, ["check", "--kb-root", str(kb), "--code", "BRANCH"])
    assert hard.exit_code == 1 and hard.output.startswith("[KB]")


def test_cli_json_code_filter_is_one_document(tmp_path):
    kb = _kb_with_ghosts(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["check", "--kb-root", str(kb), "--json", "--code", "ghost", "--max-findings", "0"],
    )
    assert result.exit_code == 0
    doc = json.loads(result.output)
    assert doc["codes"] == ["GHOST"] and len(doc["findings"]) == 60


def test_cli_unknown_code_is_a_usage_error_in_both_modes(tmp_path):
    kb = _kb_with_ghosts(tmp_path)
    runner = CliRunner()
    human = runner.invoke(cli, ["check", "--kb-root", str(kb), "--code", "SIBLING"])
    assert human.exit_code == 2 and "unknown check code" in human.output
    as_json = runner.invoke(cli, ["check", "--kb-root", str(kb), "--json", "--code", "SIBLING"])
    assert as_json.exit_code == 2
    doc = json.loads(as_json.output)
    assert doc["success"] is False and "valid codes" in doc["error"]


def test_mcp_check_codes(tmp_path):
    pytest.importorskip("mcp.server.fastmcp")
    from kvault.mcp.server import create_server

    kb = _kb_with_ghosts(tmp_path)
    server = create_server(kb)

    def call(args):
        result = asyncio.run(server.call_tool("kvault_check", args))
        if isinstance(result, tuple):
            return result[1].get("result", result[1])
        return json.loads(result[0].text)

    doc = call({"codes": ["GHOST"], "max_findings": 0})
    assert doc["codes"] == ["GHOST"] and len(doc["findings"]) == 60
    bad = call({"codes": ["NOPE"]})
    assert bad["success"] is False and "valid codes" in bad["error"]
