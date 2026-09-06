"""Channel-contract tests: the two invariants that keep the interfaces honest.

J1 — in ``--json`` mode, kvault emits exactly one JSON document and nothing
else, at every verbosity tier.

  The portable assertion is ``json.loads(result.output)``. On click 8.1.x
  (the Python 3.9 CI leg) ``Result.output`` AND ``Result.stdout`` are the
  MERGED stdout+stderr stream, and ``Result.stderr`` raises ValueError; on
  8.2+ the streams are separate but ``output`` stays merged. Parsing
  ``result.output`` therefore catches pollution on BOTH channels on the whole
  matrix. No test in this repo may use ``result.stderr``.

M1 — nothing under ``kvault/core/`` or ``kvault/mcp/`` writes to a stream.
Over MCP, stdout is the JSON-RPC transport; one stray print corrupts the
framing. Rendering lives only in ``kvault/cli/render.py``.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import events as ev

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_BODY = "A seeded memory candidate.\n"
SEED_EVENT_ID = ev._event_id("seed", None, SEED_BODY)  # deterministic, so rows can name it


@pytest.fixture()
def kb(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    (root / ".kvault").mkdir()
    (root / "_summary.md").write_text("# Test KB\n\nRoot.\n")
    (root / "people").mkdir()
    (root / "people" / "_summary.md").write_text("# People\n\nPeople.\n")
    (root / "people" / "contacts").mkdir()
    (root / "people" / "contacts" / "_summary.md").write_text("# Contacts\n\nContacts.\n")
    seeded = ev.capture_event(
        root, body=SEED_BODY, source="seed", captured_at="2026-01-01T00:00:00Z"
    )
    assert seeded["event_id"] == SEED_EVENT_ID
    return root


# (argv, stdin) — every JSON-capable command, exercised so notes actually fire
# (autofill on write, created on write-summary, truncation on search).
JSON_COMMANDS = [
    (["status"], None),
    (["status", "--root-summary"], None),
    (["doctor"], None),
    (["tree"], None),
    (["list", "."], None),
    (["read", "people"], None),
    (["read", "people", "--parents", "immediate"], None),
    (["read-summary", "people"], None),
    (["ancestors", "people/contacts"], None),
    (["ancestors", "people/contacts", "--paths-only"], None),
    (["validate"], None),
    (["search", "people", "--limit", "1"], None),
    (["search", "people", "--no-collapse", "--kind", "category", "--path", "people"], None),
    (["write", "people/contacts/jane_doe", "--create"], "# Jane Doe\n\nA fact.\n"),
    (["write-summary", "projects"], "# Projects\n\nNew branch.\n"),
    (
        ["update-summaries"],
        json.dumps([{"path": "people", "content": "# People\n\nRollup.\n"}]),
    ),
    (
        ["journal", "--source", "test", "--date", "bogus"],
        json.dumps([{"action_type": "update", "path": "people"}]),
    ),
    (["capture", "--source", "test"], "A memory candidate.\n"),
    (["events", "list"], None),
    (["events", "list", "--status", "retracted", "--limit", "5", "--since", "2025-01-01"], None),
    (["events", "show", SEED_EVENT_ID], None),
    (["events", "retract", SEED_EVENT_ID, "--reason", "wrong evidence"], None),
    (
        ["write", "people/contacts/seeded", "--create", "--event", SEED_EVENT_ID],
        "# Seeded\n\nFrom the seeded event.\n",
    ),
    (["log", "summary"], None),
    (["log", "tail"], None),
    (["artifact", "daily"], None),
    (["artifact", "daily", "--stdout"], None),
]

TIERS = [[], ["-q"], ["--explain"], ["--trace"]]


@pytest.mark.parametrize("tier", TIERS, ids=["normal", "quiet", "explain", "trace"])
@pytest.mark.parametrize("argv,stdin", JSON_COMMANDS, ids=[" ".join(c[0]) for c in JSON_COMMANDS])
def test_json_mode_emits_exactly_one_document(kb, argv, stdin, tier):
    runner = CliRunner()
    result = runner.invoke(cli, ["--kb-root", str(kb), "--json", *tier, *argv], input=stdin)
    assert result.exit_code == 0, result.output
    json.loads(result.output)  # merged stream on every supported click version


def test_quiet_and_explain_are_mutually_exclusive(kb):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--kb-root", str(kb), "-q", "--explain", "write", "people/x", "--create"],
        input="# X\n\nx.\n",
    )
    assert result.exit_code != 0
    assert "cannot be combined" in result.output


def test_strict_exits_3_on_partial_batch(kb):
    runner = CliRunner()
    updates = json.dumps(
        [
            {"path": "people", "content": "# People\n\nGood.\n"},
            {"path": None, "content": "orphan"},
        ]
    )
    result = runner.invoke(
        cli, ["--kb-root", str(kb), "--json", "--strict", "update-summaries"], input=updates
    )
    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["partial"] is True


def test_strict_passes_on_clean_write(kb):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--kb-root", str(kb), "--strict", "write", "people/contacts/ok", "--create"],
        input="# Ok\n\nok.\n",
    )
    assert result.exit_code == 0


def test_strict_is_rejected_on_check(kb):
    runner = CliRunner()
    result = runner.invoke(cli, ["--kb-root", str(kb), "--strict", "check"])
    assert result.exit_code != 0
    assert "exit codes are already a contract" in result.output


def test_check_json_parses_at_every_tier(kb):
    """check --json stays one parseable document; its exit code (0/1) is its
    own contract and is asserted stable across tiers, not forced to 0."""
    runner = CliRunner()
    baseline = runner.invoke(cli, ["--kb-root", str(kb), "--json", "check"])
    json.loads(baseline.output)
    for tier in (["-q"], ["--explain"], ["--trace"]):
        result = runner.invoke(cli, ["--kb-root", str(kb), "--json", *tier, "check"])
        json.loads(result.output)
        assert result.exit_code == baseline.exit_code


def test_check_human_output_is_frozen_across_tiers(kb):
    """check's stdout feeds UserPromptSubmit hooks and three Moss scripts:
    verbosity flags must not change a single byte of it, nor the exit code."""
    runner = CliRunner()
    baseline = runner.invoke(cli, ["--kb-root", str(kb), "check"])
    for tier in (["-q"], ["--explain"], ["--trace"]):
        result = runner.invoke(cli, ["--kb-root", str(kb), *tier, "check"])
        assert result.output == baseline.output
        assert result.exit_code == baseline.exit_code
    # And no new line prefixes beyond the historical contract.
    for line in baseline.output.splitlines():
        assert line.startswith(("[KB]", "SUMMARY:", "PENDING:", "RETRACTED:")), line


def test_verbosity_env_var_raises_tier(kb, monkeypatch):
    monkeypatch.setenv("KVAULT_VERBOSITY", "explain")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--kb-root", str(kb), "write", "people/contacts/env_probe", "--create"],
        input="# Env Probe\n\nx.\n",
    )
    assert result.exit_code == 0
    assert "why" in result.output  # explain-tier line, from the env var alone


def test_verbosity_env_var_typo_is_silent_normal(kb, monkeypatch):
    monkeypatch.setenv("KVAULT_VERBOSITY", "exlpain")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--kb-root", str(kb), "write", "people/contacts/typo_probe", "--create"],
        input="# Typo Probe\n\nx.\n",
    )
    assert result.exit_code == 0
    assert "why" not in result.output


# ---------------------------------------------------------------------------
# M1 — no stream writers outside the CLI
# ---------------------------------------------------------------------------

FORBIDDEN = ("click.echo", "sys.stdout", "print(")


def test_core_and_mcp_have_no_stream_writers():
    offenders = []
    for package_dir in (REPO_ROOT / "kvault" / "core", REPO_ROOT / "kvault" / "mcp"):
        for path in sorted(package_dir.rglob("*.py")):
            source = path.read_text()
            for needle in FORBIDDEN:
                if needle in source:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {needle}")
    assert not offenders, (
        "stream writers are forbidden outside kvault/cli "
        "(MCP stdout is the JSON-RPC transport): " + "; ".join(offenders)
    )


def test_mcp_server_never_imports_the_renderer():
    subprocess.check_call(
        [
            sys.executable,
            "-c",
            "import sys; import kvault.mcp.server; "
            "assert 'kvault.cli.render' not in sys.modules, 'MCP must not import the renderer'",
        ],
        cwd=str(REPO_ROOT),
    )


def test_cross_level_flag_conflict_fails_before_the_write(kb):
    """kvault -q write --explain must error out BEFORE touching the KB."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--kb-root", str(kb), "-q", "write", "people/pre_write", "--create", "--explain"],
        input="# X\n\nx.\n",
    )
    assert result.exit_code != 0
    assert not (kb / "people" / "pre_write").exists()


def test_doctor_never_fails_without_kb(tmp_path, monkeypatch):
    """doctor is what you run when things are broken: a missing KB is a finding."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KVAULT_KB_ROOT", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "doctor"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["kb"]["root"] is None
    assert report["kb"]["resolved_from"] == "none"
    assert report["kb"]["is_kb"] is False
    assert report["version"]


def test_doctor_reports_disallowed_root_without_failing(kb, monkeypatch):
    monkeypatch.setenv("KVAULT_ALLOWED_ROOTS", str(kb.parent / "elsewhere"))
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "doctor", "--kb-root", str(kb)])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["kb"]["is_kb"] is True
    assert report["kb"]["allowed_root_error"]
    assert report["env"]["KVAULT_ALLOWED_ROOTS"]


def test_doctor_survives_root_resolution_error(monkeypatch):
    """The contract is 'never fails' — including when KB-root resolution itself raises."""
    monkeypatch.delenv("KVAULT_KB_ROOT", raising=False)

    def boom():
        raise FileNotFoundError("cwd gone")

    monkeypatch.setattr("kvault.cli.doctor.find_kb_root", boom)
    result = CliRunner().invoke(cli, ["--json", "doctor"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["kb"]["resolved_from"] == "error" and report["kb"]["root"] is None
    assert "cwd gone" in report["kb"]["resolve_error"]
