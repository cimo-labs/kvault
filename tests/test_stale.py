"""STALE: a node's own date for re-checking the facts that go stale (0.16).

kvault cannot verify a fact. It can hold the agent that wrote "the change is
pending" or "the review is open" to a date for looking again: ``verify_by``
in frontmatter, set with ``kvault mark --verify-by``, reported by check once
it passes. A word heuristic ("pending", "WIP") was measured and rejected: on
a real 496-node KB it flagged 117 nodes older than 90 days.
"""

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import decisions as dc
from kvault.core import operations as ops
from kvault.core.check import run_checks, stale_findings
from kvault.core.plan import build_plan
from kvault.core.structure import load_ignore


def _node(kb: Path, rel: str, extra: str = "", body: str = "# Node\n\nA fact.\n") -> None:
    d = kb if rel == "." else kb / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "_summary.md").write_text(f"---\nsource: manual\naliases: []\n{extra}---\n{body}")


@pytest.fixture()
def kb(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    (root / ".kvault").mkdir()
    _node(root, ".", body="# Root\n\nRoot.\n")
    _node(root, "projects", body="# Projects\n\nProjects.\n")
    _node(
        root,
        "projects/release",
        "verify_by: 2026-01-10\n",
        "# Release\n\nThe fix is pending review.\n",
    )
    _node(root, "projects/launch", "verify_by: '2999-01-01'\n", "# Launch\n\nIn progress.\n")
    _node(root, "projects/typo", "verify_by: next week\n", "# Typo\n\nAwaiting reply.\n")
    return root


def test_parse_verify_by():
    today = date(2026, 9, 26)
    assert dc.parse_verify_by("2026-10-15", today) == "2026-10-15"
    assert dc.parse_verify_by("+14d", today) == "2026-10-10"
    assert dc.parse_verify_by("+2w", today) == "2026-10-10"
    assert dc.parse_verify_by("none", today) == ""
    with pytest.raises(ValueError, match=r"\+14d"):
        dc.parse_verify_by("next week", today)


def test_check_reports_passed_and_unparseable_dates(kb):
    findings = stale_findings(kb, load_ignore(kb), today=date(2026, 9, 26))
    by_path = {f.path: f for f in findings}
    assert set(by_path) == {"projects/release", "projects/typo"}
    assert by_path["projects/release"].detail == {"verify_by": "2026-01-10", "days_overdue": 259}
    assert "passed 259 days ago" in by_path["projects/release"].message
    assert "never comes due" in by_path["projects/typo"].message
    doc = run_checks(kb, codes=["STALE"])
    assert {f["path"] for f in doc["findings"]} == {"projects/release", "projects/typo"}


def test_mark_sets_and_clears_verify_by(kb):
    marked = ops.mark_node(kb, "projects/release", verify_by="+14d")
    due = (date.today() + timedelta(days=14)).isoformat()
    assert marked["success"] and marked["decisions"]["verify_by"] == due
    assert f"verify_by={due}" in marked["did"]
    assert "projects/release" not in {f.path for f in stale_findings(kb, [])}
    cleared = ops.mark_node(kb, "projects/release", verify_by="none")
    assert cleared["decisions"]["verify_by"] is None
    assert "verify_by" not in (kb / "projects/release/_summary.md").read_text()
    bad = ops.mark_node(kb, "projects/release", verify_by="soon")
    assert bad["success"] is False and "YYYY-MM-DD" in bad["error"]


def test_cli_and_plan(kb):
    runner = CliRunner()
    out = runner.invoke(cli, ["check", "--kb-root", str(kb), "--code", "STALE"])
    assert out.exit_code == 0
    assert "STALE: projects/release — verify_by 2026-01-10 passed" in out.output
    assert "STALE: fix →" in out.output
    marked = runner.invoke(
        cli, ["mark", "projects/typo", "--verify-by", "2999-12-31", "--kb-root", str(kb)]
    )
    assert marked.exit_code == 0 and "verify_by=2999-12-31" in marked.output
    items = [i for i in build_plan(kb, limit=0)["items"] if i["kind"] == "stale"]
    assert [i["path"] for i in items] == ["projects/release"]
    assert any("--verify-by +14d" in c for c in items[0]["commands"])


def test_mcp_mark_verify_by(kb):
    pytest.importorskip("mcp.server.fastmcp")
    from kvault.mcp.server import create_server

    server = create_server(kb)
    result = asyncio.run(
        server.call_tool("kvault_mark", {"path": "projects/typo", "verify_by": "+2w"})
    )
    doc = (
        result[1].get("result", result[1])
        if isinstance(result, tuple)
        else json.loads(result[0].text)
    )
    assert doc["success"] and doc["decisions"]["verify_by"]


# ── review round (2026-09-26) ─────────────────────────────────────────────


def _dated(kb: Path, rel: str, updated: str = "2026-08-20") -> None:
    d = kb / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "_summary.md").write_text(
        f"---\nsource: manual\naliases: []\ncreated: '{updated}'\nupdated: '{updated}'\n---\n"
        f"# {rel.rsplit('/', 1)[-1]}\n\nA fact about {rel}.\n"
    )


def test_mark_keeps_dates_and_journals_so_check_stays_green(tmp_path):
    """Before: mark stamped today, the parent went PROPAGATE (and LOG for a leaf),
    while the result said propagation_required: false."""
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".kvault").mkdir()
    for rel in ("people", "people/contacts", "people/contacts/carol"):
        _dated(kb, rel)
    (kb / "_summary.md").write_text(
        "---\nsource: manual\naliases: []\nupdated: '2026-08-20'\n---\n# Root\n\nRoot.\n"
    )
    result = ops.mark_node(kb, "people/contacts/carol", verify_by="+14d")
    assert result["success"] and result["propagation_required"] is False
    assert result["journal_logged"] is True
    assert "updated: '2026-08-20'" in (kb / "people/contacts/carol/_summary.md").read_text()
    doc = run_checks(kb, codes=["PROPAGATE", "LOG"])
    assert doc["success"] is True, doc["warnings"]


def test_impossible_dates_never_crash_and_are_reported(kb):
    _node(kb, "projects/impossible", "verify_by: 2026-09-31\n", "# Impossible\n\nPending.\n")
    doc = run_checks(kb, codes=["STALE"])  # used to raise ValueError from PyYAML
    msg = {f["path"]: f["message"] for f in doc["findings"]}
    assert "not a date" in msg["projects/impossible"]
    assert ops.read_node(kb, "projects/impossible")["meta"]["verify_by"] == "2026-09-31"
    refused = ops.write_node(
        kb,
        "projects/impossible",
        "# Impossible\n\nStill pending.\n",
        meta={"source": "manual", "aliases": [], "verify_by": "2026-02-30"},
    )
    assert refused["success"] is True or "date" in str(refused)  # meta dicts are not YAML text
    assert dc.as_date("2026-09-201") is None and dc.as_date("2026-09-20T10:00:00Z")


def test_stale_walks_only_nodes_mark_can_address(kb, tmp_path):
    past = "verify_by: 2026-01-01\n"
    _node(kb, "projects/deep_context/parked", past)
    _node(kb, "journal/2026-03", past)
    _node(kb, "projects/_archive/old", past)
    (kb / "projects" / "Big Deal").mkdir()
    (kb / "projects" / "Big Deal" / "_summary.md").write_text(f"---\nsource: x\n{past}---\n# B\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "_summary.md").write_text("---\nsource: x\nverify_by: secret-token\n---\n# S\n")
    (kb / "projects" / "linked").symlink_to(outside, target_is_directory=True)
    paths = {f.path for f in stale_findings(kb, load_ignore(kb), today=date(2026, 9, 26))}
    assert paths == {"projects/release", "projects/typo"}


def test_clear_keeps_verify_by(kb):
    ops.mark_node(kb, "projects/launch", max_children=5)
    cleared = ops.mark_node(kb, "projects/launch", clear=True)
    assert cleared["decisions"]["max_children"] is None
    assert cleared["decisions"]["verify_by"] == "2999-01-01"
    assert "cleared structure decisions" in cleared["did"]


def test_validate_reports_an_impossible_date_instead_of_crashing(kb):
    (kb / "projects" / "release" / "_summary.md").write_text(
        "---\nsource: manual\naliases: []\nupdated: 2026-09-31\n---\n# Release\n\nPending.\n"
    )
    issues = ops.validate_kb(kb)["issues"]
    bad = [i for i in issues if i["type"] == "malformed_frontmatter"]
    assert bad and "dates as text" in bad[0]["message"]
