"""Tests for parent-summary quality auditing."""

from pathlib import Path

from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core.summary_quality import audit_summary_quality

import json


def _write_summary(root: Path, rel_path: str, body: str, frontmatter: str = "") -> None:
    directory = root if rel_path in {"", "."} else root / rel_path
    directory.mkdir(parents=True, exist_ok=True)
    content = body
    if frontmatter:
        content = f"---\n{frontmatter.rstrip()}\n---\n\n{body}"
    (directory / "_summary.md").write_text(content)


def _basic_kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".kvault").mkdir()
    return kb


def test_summary_quality_warns_on_missing_child_coverage(tmp_path):
    kb = _basic_kb(tmp_path)
    _write_summary(
        kb,
        ".",
        "# Root\n\n"
        + " ".join(["Alpha context is described with enough operational detail."] * 20),
    )
    _write_summary(kb, "alpha", "# Alpha\n\nAlpha content.")
    _write_summary(kb, "beta", "# Beta\n\nBeta content.")

    issues = audit_summary_quality(kb)

    missing = [issue for issue in issues if issue.code == "missing_child_coverage"]
    assert missing
    assert "beta" in missing[0].details["missing_children"]


def test_summary_quality_warns_on_too_short_parent_rollup(tmp_path):
    kb = _basic_kb(tmp_path)
    _write_summary(kb, ".", "# Root\n\nAlpha and Beta.")
    _write_summary(kb, "alpha", "# Alpha\n\nAlpha content.")
    _write_summary(kb, "beta", "# Beta\n\nBeta content.")

    issues = audit_summary_quality(kb)

    assert any(issue.code == "too_short" for issue in issues)


def test_summary_quality_warns_on_placeholder_redirect_language(tmp_path):
    kb = _basic_kb(tmp_path)
    _write_summary(
        kb,
        ".",
        "# Root\n\nSummary pending. See Alpha for details. "
        + " ".join(["Alpha remains the current operational focus."] * 20),
    )
    _write_summary(kb, "alpha", "# Alpha\n\nAlpha content.")

    issues = audit_summary_quality(kb)

    assert any(issue.code == "placeholder_language" for issue in issues)


def test_summary_quality_accepts_comprehensive_parent_summary(tmp_path):
    kb = _basic_kb(tmp_path)
    _write_summary(
        kb,
        ".",
        "# Root\n\n"
        "Alpha captures active research planning, current owner context, important "
        "decisions, open follow ups, and the latest state needed before reading "
        "deeper files. Beta captures implementation work, delivery status, known "
        "risks, dependencies, and near term next actions. "
        + " ".join(
            [
                "Together the children provide a complete map of priorities, status, "
                "relationships, constraints, evidence, and unresolved questions."
            ]
            * 9
        ),
    )
    _write_summary(kb, "alpha", "# Alpha\n\nAlpha content.")
    _write_summary(kb, "beta", "# Beta\n\nBeta content.")

    issues = audit_summary_quality(kb)

    assert issues == []


def test_summary_quality_uses_child_aliases_for_coverage(tmp_path):
    kb = _basic_kb(tmp_path)
    _write_summary(
        kb,
        ".",
        "# Root\n\n"
        "Gamma Project is fully covered here with status, context, next actions, "
        "risks, owner information, and enough detail for navigation. "
        + " ".join(["Gamma Project remains the relevant child summary."] * 12),
    )
    _write_summary(
        kb,
        "internal_slug",
        "# Internal\n\nInternal content.",
        frontmatter="aliases:\n  - Gamma Project\n",
    )

    issues = audit_summary_quality(kb)

    assert not [issue for issue in issues if issue.code == "missing_child_coverage"]


def test_check_summary_only_warnings_exit_zero(tmp_path):
    kb = _basic_kb(tmp_path)
    _write_summary(kb, ".", "# Root\n\nSummary pending.")
    _write_summary(kb, "alpha", "# Alpha\n\nAlpha content.")

    result = CliRunner().invoke(cli, ["check", "--kb-root", str(kb)])

    assert result.exit_code == 0
    assert "SUMMARY:" in result.output


def test_check_summary_quality_can_be_disabled(tmp_path):
    kb = _basic_kb(tmp_path)
    _write_summary(kb, ".", "# Root\n\nSummary pending.")
    _write_summary(kb, "alpha", "# Alpha\n\nAlpha content.")

    result = CliRunner().invoke(cli, ["check", "--kb-root", str(kb), "--no-summary-quality"])

    assert result.exit_code == 0
    assert "SUMMARY:" not in result.output


def test_fresh_init_has_no_summary_quality_warnings(tmp_path):
    kb = tmp_path / "fresh_kb"
    runner = CliRunner()

    init_result = runner.invoke(cli, ["init", str(kb), "--name", "Test"])
    assert init_result.exit_code == 0

    assert audit_summary_quality(kb) == []


# ---------------------------------------------------------------------------
# Ceilings: too_long and stale_history (0.14.0)
# ---------------------------------------------------------------------------


def _bloated_body(sections: int = 10, words_per: int = 120, dated: bool = True) -> str:
    """A parent summary that has accreted dated delta sections."""
    parts = ["# Root", "", "Alpha and Beta are the two branches, covered below."]
    for i in range(sections):
        heading = f"## 2026-08-{i + 1:02d} Nightly Delta" if dated else f"## Section {i + 1}"
        parts.append("")
        parts.append(heading)
        parts.append(" ".join(f"word{j}" for j in range(words_per)))
    return "\n".join(parts) + "\n"


def _codes(issues):
    return sorted(issue.code for issue in issues)


def _two_child_kb(tmp_path, root_body: str):
    kb = _basic_kb(tmp_path)
    _write_summary(kb, ".", root_body)
    _write_summary(kb, "alpha", "# Alpha\n\nAlpha content.")
    _write_summary(kb, "beta", "# Beta\n\nBeta content.")
    return kb


def test_too_long_flags_rollup_over_budget(tmp_path):
    # 2 children / 2 descendants -> cap 1000 + 100 + 10 = 1110 words
    kb = _two_child_kb(tmp_path, _bloated_body(sections=10, words_per=120, dated=False))
    issues = audit_summary_quality(kb, max_dated_sections=0)
    too_long = [i for i in issues if i.code == "too_long"]
    assert len(too_long) == 1
    assert too_long[0].details["maximum_words"] == 1110
    assert too_long[0].details["word_count"] > 1110
    assert "rewrite as a current-state rollup" in too_long[0].message


def test_too_long_budget_scales_with_children(tmp_path):
    # 1 child / 1 descendant -> cap 1055: 950 words pass, 1100 fail.
    kb = _basic_kb(tmp_path)
    _write_summary(kb, "alpha", "# Alpha\n\nAlpha content.")
    _write_summary(kb, ".", _bloated_body(sections=1, words_per=940, dated=False))
    assert "too_long" not in _codes(audit_summary_quality(kb, max_dated_sections=0))
    _write_summary(kb, ".", _bloated_body(sections=1, words_per=1090, dated=False))
    assert "too_long" in _codes(audit_summary_quality(kb, max_dated_sections=0))


def test_too_long_cap_never_exceeds_2000(tmp_path):
    kb = _basic_kb(tmp_path)
    for i in range(6):
        _write_summary(kb, f"cat{i}", f"# Cat{i}\n\nCat {i} content.")
        for j in range(30):
            _write_summary(kb, f"cat{i}/leaf{j}", f"# Leaf {j}\n\nleaf.")
    _write_summary(kb, ".", _bloated_body(sections=1, words_per=2100, dated=False))
    too_long = [i for i in audit_summary_quality(kb, max_dated_sections=0) if i.code == "too_long"]
    assert [i.path for i in too_long] == ["_summary.md"]
    assert too_long[0].details["maximum_words"] == 2000


def test_stale_history_counts_dated_and_delta_headings(tmp_path):
    kb = _two_child_kb(tmp_path, _bloated_body(sections=3, words_per=30))
    assert "stale_history" not in _codes(audit_summary_quality(kb))
    _write_summary(kb, ".", _bloated_body(sections=4, words_per=30))
    stale = [i for i in audit_summary_quality(kb) if i.code == "stale_history"]
    assert len(stale) == 1
    assert stale[0].details["dated_sections"] == 4
    assert stale[0].details["examples"][0].startswith("2026-08-01")


def test_stale_history_ignores_h1_and_counts_undated_delta(tmp_path):
    body = (
        "# 2026-08-01 Root title with a date\n\nAlpha and Beta.\n\n"
        "## Delta\n\nx\n\n## Change log\n\ny\n\n## Update log\n\nz\n\n## 2026-09-01\n\nw\n"
    )
    kb = _two_child_kb(tmp_path, body)
    stale = [i for i in audit_summary_quality(kb, max_words=0) if i.code == "stale_history"]
    assert stale and stale[0].details["dated_sections"] == 4  # the H1 is not counted


def test_ceilings_skip_parents_with_only_background_children(tmp_path):
    kb = _basic_kb(tmp_path)
    _write_summary(kb, "people", "# People\n\n" + " ".join(["Sven Schmit is covered."] * 40))
    _write_summary(
        kb, "people/sven", _bloated_body(sections=6, words_per=300).replace("# Root", "# Sven")
    )
    _write_summary(kb, "people/sven/deep_context", "# Deep context\n\nLong notes.")
    codes = {(i.path, i.code) for i in audit_summary_quality(kb)}
    assert ("people/sven/_summary.md", "too_long") not in codes
    assert ("people/sven/_summary.md", "stale_history") not in codes


def test_max_words_semantics_none_zero_and_hard_ceiling(tmp_path):
    kb = _two_child_kb(
        tmp_path, _bloated_body(sections=2, words_per=100, dated=False)
    )  # ~215 words
    assert "too_long" not in _codes(audit_summary_quality(kb))  # None -> formula (1110)
    assert "too_long" not in _codes(audit_summary_quality(kb, max_words=0))  # 0 -> off
    assert "too_long" in _codes(audit_summary_quality(kb, max_words=100))  # N -> hard ceiling
    assert "too_long" not in _codes(audit_summary_quality(kb, max_words=5000))


def test_check_reports_ceilings_warn_only(tmp_path):
    kb = _two_child_kb(tmp_path, _bloated_body(sections=12, words_per=120))
    runner = CliRunner()
    result = runner.invoke(cli, ["check", "--kb-root", str(kb), "--summary-max-warnings", "10"])
    assert result.exit_code == 0, result.output
    assert "SUMMARY: _summary.md: too long" in result.output
    assert "SUMMARY: _summary.md: 12 dated/delta sections" in result.output
    for line in result.output.splitlines():
        assert line.startswith(("[KB]", "SUMMARY:", "PENDING:")), line

    as_json = runner.invoke(cli, ["check", "--kb-root", str(kb), "--json"])
    payload = json.loads(as_json.output)
    assert {w["code"] for w in payload["summary_warnings"]} >= {"too_long", "stale_history"}
    assert payload["success"] is True

    quiet = runner.invoke(
        cli,
        [
            "check",
            "--kb-root",
            str(kb),
            "--summary-max-words",
            "0",
            "--summary-max-dated-sections",
            "0",
        ],
    )
    assert quiet.exit_code == 0
    assert "too long" not in quiet.output and "dated/delta" not in quiet.output
