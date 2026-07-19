"""Tests for parent-summary quality auditing."""

from pathlib import Path

from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core.summary_quality import audit_summary_quality


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


def test_summary_quality_skips_symlinked_summaries(tmp_path):
    kb = _basic_kb(tmp_path)
    _write_summary(kb, ".", "# Root\n\nA stable root summary with no visible children.")
    outside = tmp_path / "outside.md"
    outside.write_text("---\n- invalid\n---\n", encoding="utf-8")
    child = kb / "unsafe"
    child.mkdir()
    (child / "_summary.md").symlink_to(outside)

    assert audit_summary_quality(kb) == []
