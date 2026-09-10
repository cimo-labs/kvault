"""The 0.15 check findings: BRANCH at the root, GHOST, SIBLINGS, LOOSE, JOURNAL,
bounded coverage lines, the unified findings list, and .kvaultignore.

The fixture reproduces the 2026-09 audit KB in miniature. validate said
"KB is valid" on the real one; check said BRANCH for one parent and nothing
else. Every symptom below must now be named.
"""

import json
from pathlib import Path

from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import operations as ops
from kvault.core.check import run_checks
from kvault.core.structure import IGNORE_FILE

BODY = "# Node\n\nA node with enough words to describe itself briefly.\n"
META = {"source": "manual", "aliases": ["Node"]}


def _node(kb: Path, rel: str, body: str = BODY) -> None:
    d = kb / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "_summary.md").write_text(
        "---\ncreated: '2026-09-01'\nupdated: '2026-09-01'\nsource: manual\naliases: []\n---\n"
        + body
    )


def _sprawl_kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".kvault").mkdir()
    (kb / "_summary.md").write_text(
        "---\nupdated: '2026-09-01'\nsource: manual\naliases: []\n---\n# Root\n\nRoot rollup.\n"
    )
    # 12 root categories: over the ceiling, and three split-brain pairs
    for name in (
        "people",
        "team",
        "projects",
        "infra",
        "infrastructure",
        "code_reviews",
        "reviews",
        "customers",
        "partners",
        "org",
        "tech",
        "models",
    ):
        _node(kb, name)
    _node(kb, "org/people")
    _node(kb, "tech/models")
    # ghosts: directories with nothing inside that any surface can see
    (kb / "pipelines").mkdir()
    (kb / "tech" / "infrastructure").mkdir()
    (kb / "scripts").mkdir()
    # loose files
    (kb / "diagram.png").write_bytes(b"x")
    (kb / "todo.md").write_text("x")
    (kb / "projects" / "notes.txt").write_text("x")
    # journal drift
    (kb / "journal" / "2026-09").mkdir(parents=True)
    (kb / "journal" / "2026-09" / "log.md").write_text("# log\n")
    (kb / "journal" / "y2026" / "q3").mkdir(parents=True)
    (kb / "journal" / "y2026" / "q3" / "log_1.md").write_text("# log\n")
    (kb / "archive" / "journal").mkdir(parents=True)
    return kb


def _codes(doc):
    return {f["code"] for f in doc["findings"]}


def test_root_fanout_is_a_branch_finding(tmp_path):
    kb = _sprawl_kb(tmp_path)
    doc = run_checks(kb)
    branch = [f for f in doc["findings"] if f["code"] == "BRANCH"]
    assert branch and branch[0]["path"] == "."
    # ghosts count as fan-out; ignored and reserved dirs do not (journal, archive
    # are not counted: archive is a plain dir with no summary → it IS a ghost)
    assert branch[0]["detail"]["child_count"] >= 15
    assert doc["success"] is False
    assert "BRANCH: . has" in doc["warnings"][0]


def test_ghosts_are_found(tmp_path):
    kb = _sprawl_kb(tmp_path)
    ghosts = {f["path"] for f in run_checks(kb)["findings"] if f["code"] == "GHOST"}
    assert {"pipelines", "tech/infrastructure", "scripts", "archive"} <= ghosts
    assert "journal" not in ghosts


def test_sibling_collisions_and_same_name_elsewhere(tmp_path):
    kb = _sprawl_kb(tmp_path)
    sib = [f for f in run_checks(kb)["findings"] if f["code"] == "SIBLINGS"]
    pairs = {(f["detail"].get("a"), f["detail"].get("b")) for f in sib if f["path"] == "."}
    assert ("infra", "infrastructure") in pairs
    assert ("code_reviews", "reviews") in pairs
    elsewhere = {
        f["path"]: f["detail"]["paths"]
        for f in sib
        if f["detail"].get("kind") == "same_name_elsewhere"
    }
    assert set(elsewhere["people"]) == {"people", "org/people"}
    assert set(elsewhere["models"]) == {"models", "tech/models"}
    assert set(elsewhere["infrastructure"]) == {"infrastructure", "tech/infrastructure"}
    # semantic pairs are out of scope by design
    assert ("people", "team") not in pairs and ("customers", "partners") not in pairs


def test_loose_and_journal_findings(tmp_path):
    kb = _sprawl_kb(tmp_path)
    doc = run_checks(kb)
    loose = {f["path"] for f in doc["findings"] if f["code"] == "LOOSE"}
    assert loose == {"diagram.png", "todo.md", "projects/notes.txt"}
    journal = {f["path"] for f in doc["findings"] if f["code"] == "JOURNAL"}
    assert "journal/y2026" in journal
    assert "archive/journal" in journal
    assert "journal/2026-09/log.md" not in journal


def test_ignore_file_silences_tooling(tmp_path):
    kb = _sprawl_kb(tmp_path)
    (kb / IGNORE_FILE).write_text("scripts\n*.png\ntodo.md\narchive\n")
    doc = run_checks(kb)
    ghosts = {f["path"] for f in doc["findings"] if f["code"] == "GHOST"}
    assert "scripts" not in ghosts and "archive" not in ghosts
    loose = {f["path"] for f in doc["findings"] if f["code"] == "LOOSE"}
    assert loose == {"projects/notes.txt"}
    assert doc["ignore_patterns"] == ["scripts", "*.png", "todo.md", "archive"]


def test_findings_are_hard_first_and_bounded(tmp_path):
    kb = _sprawl_kb(tmp_path)
    doc = run_checks(kb, max_findings=2)
    levels = [f["level"] for f in doc["findings"]]
    assert levels == sorted(levels, key=lambda lv: 0 if lv == "hard" else 1)
    per_code = {}
    for f in doc["findings"]:
        if f["level"] == "warn":
            per_code[f["code"]] = per_code.get(f["code"], 0) + 1
    assert all(n <= 2 for n in per_code.values())
    assert doc["truncated"].get("GHOST", 0) >= 1
    assert doc["finding_count"] > len(doc["findings"])


def test_missing_child_coverage_message_is_bounded(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "_summary.md").write_text("# Root\n\nRoot.\n")
    _node(kb, "projects", "# Projects\n\nNothing named here.\n")
    for i in range(12):
        _node(kb, f"projects/thing{i:02d}")
    doc = run_checks(kb)
    cov = [
        i
        for i in doc["summary_warnings"]
        if i["code"] == "missing_child_coverage" and i["path"] == "projects/_summary.md"
    ]
    assert cov and "(+4 more)" in cov[0]["message"]
    assert cov[0]["details"]["missing_count"] == 12
    assert len(cov[0]["details"]["missing_children"]) == 12
    assert len(cov[0]["message"]) < 300


def test_clean_kb_has_no_structure_findings(empty_kb):
    ops.write_node(empty_kb, "people/alice", BODY, META, create=True)
    doc = run_checks(empty_kb)
    assert not (_codes(doc) & {"GHOST", "SIBLINGS", "LOOSE", "JOURNAL", "BRANCH"})


def test_validate_reports_ghosts_as_warnings(tmp_path):
    kb = _sprawl_kb(tmp_path)
    result = ops.validate_kb(kb)
    ghosts = [i for i in result["issues"] if i["type"] == "ghost_directory"]
    assert {g["path"] for g in ghosts} >= {"pipelines", "tech/infrastructure", "scripts"}
    assert all(g["severity"] == "warning" for g in ghosts)
    assert result["valid"] is False
    (kb / IGNORE_FILE).write_text("pipelines\ntech/infrastructure\nscripts\narchive\n")
    assert [i for i in ops.validate_kb(kb)["issues"] if i["type"] == "ghost_directory"] == []


def test_cli_check_prints_structure_groups_and_json_document(tmp_path):
    kb = _sprawl_kb(tmp_path)
    runner = CliRunner()
    human = runner.invoke(cli, ["check", "--kb-root", str(kb)])
    assert human.exit_code == 1
    assert human.output.startswith("[KB]")
    for prefix in ("GHOST:", "SIBLINGS:", "LOOSE:", "JOURNAL:"):
        assert prefix in human.output, prefix
    assert "GHOST: fix →" in human.output
    as_json = runner.invoke(cli, ["check", "--kb-root", str(kb), "--json"])
    doc = json.loads(as_json.output)
    assert as_json.exit_code == 1
    assert {"findings", "structure_warnings", "truncated", "version", "did"} <= set(doc)
    assert doc["warnings"] and isinstance(doc["warnings"][0], str)
