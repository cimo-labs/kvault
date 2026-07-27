"""Regression tests for the 2026-07-26 audit findings.

1. Node path components rejected digit-leading and hyphenated names, making real
   on-disk nodes unreachable through the node API — including every journal month
   kvault itself writes to ("journal/YYYY-MM").
2. DATA LOSS: _children_digest() hashed the *filtered* child list, so children the
   node API could not read were dropped from the digest. The stale-write guard then
   approved parent summaries composed without them.
3. `kvault check --kb-root <bad path>` exited 0 with no output, making a
   misconfigured hook indistinguishable from a clean KB.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import operations as ops


def _write_node(kb: Path, rel: str, body: str) -> None:
    """Create a node directory with a _summary.md."""
    target = kb if rel == "." else kb / rel
    target.mkdir(parents=True, exist_ok=True)
    (target / "_summary.md").write_text(f"---\nupdated: '2026-07-01'\n---\n\n{body}")


@pytest.fixture
def dated_kb(tmp_path):
    """KB containing the node shapes the old regex rejected."""
    kb = tmp_path / "kb"
    (kb / ".kvault").mkdir(parents=True)
    _write_node(kb, ".", "# Root\n")
    _write_node(kb, "journal", "# Journal\n")
    _write_node(kb, "journal/2026-03", "# March 2026\n")
    _write_node(kb, "journal/2026-07", "# July 2026\n")
    _write_node(kb, "customers", "# Customers\n")
    _write_node(kb, "customers/3d_engineering", "# 3D Engineering\n")
    return kb


# ============================================================================
# Finding 1: node path components
# ============================================================================


class TestNodeComponentPattern:
    """Digit-leading and hyphenated components are valid; traversal is not."""

    @pytest.mark.parametrize(
        "path",
        [
            "journal/2026-03",
            "journal/2026-07",
            "customers/3d_engineering",
        ],
    )
    def test_previously_unreachable_nodes_are_readable(self, dated_kb, path):
        node = ops.read_node(dated_kb, path)
        assert node is not None, f"{path} should be reachable via the node API"
        assert node["path"] == path

    def test_journal_month_matches_what_kvault_itself_writes(self):
        """get_journal_path() emits journal/YYYY-MM — that must be a valid node."""
        from kvault.core.validation import get_journal_path

        journal_dir = str(Path(get_journal_path()).parent)
        is_valid, err = ops._validate_node_path(journal_dir)
        assert is_valid, f"kvault writes to {journal_dir} but rejects it: {err}"

    @pytest.mark.parametrize(
        "path",
        [
            "people",
            "people/friends",
            "people/friends/alice_smith",
            "journal/2026-03",
            "customers/3d_engineering",
            "a-b-c/d1",
            "2026",
        ],
    )
    def test_valid_components_accepted(self, path):
        is_valid, _ = ops._validate_node_path(path)
        assert is_valid

    @pytest.mark.parametrize(
        "path",
        [
            "..",
            "people/..",
            "../escape",
            "/etc/passwd",
            "people//x",
            ".hidden",
            "people/.git",
            "_schema",
            "people/_internal",
            "-leading_dash",
            "has space",
            "has/../traversal",
            "",
        ],
    )
    def test_traversal_and_reserved_components_rejected(self, path):
        is_valid, err = ops._validate_node_path(path)
        assert not is_valid, f"{path!r} must stay invalid"
        assert err

    def test_widening_is_shared_between_node_and_entity_paths(self):
        """The pattern used to be duplicated and had drifted out of sync."""
        from kvault.core.validation import NODE_COMPONENT_RE, validate_entity_path

        assert ops._NODE_COMPONENT_RE is NODE_COMPONENT_RE
        for path in ("journal/2026-03", "customers/3d_engineering"):
            is_valid, err = validate_entity_path(path)
            assert is_valid, err

    def test_entity_can_be_written_to_a_date_named_node(self, dated_kb):
        result = ops.write_entity(
            dated_kb,
            "journal/2026-08",
            "# August 2026\n\nMonthly log.\n",
            meta={"source": "test", "aliases": ["August 2026"]},
            create=True,
        )
        assert result["success"] is True
        assert (dated_kb / "journal" / "2026-08" / "_summary.md").exists()


class TestReservedNamespaceEnumeration:
    """Internal '_'-prefixed dirs are not semantic nodes and must not be enumerated."""

    def test_underscore_dir_is_not_a_child_node(self, dated_kb):
        _write_node(dated_kb, "_schema", "# Schema Definitions\n")

        assert "_schema" not in ops._child_node_paths(dated_kb, ".")

    def test_reserved_dir_does_not_trip_the_digest_guard(self, dated_kb):
        """Regression: the live ProTec KB has a root-level _schema/ node.

        If enumeration yielded it while the node API refused to read it, the new
        refuse-on-unreadable guard would hard-fail every root summary update.
        """
        _write_node(dated_kb, "_schema", "# Schema Definitions\n")

        result = ops.prepare_summary_update(dated_kb, ".")
        assert result["success"] is True
        assert "_schema" not in [child["path"] for child in result["children"]]

    def test_hidden_dir_is_not_a_child_node(self, dated_kb):
        (dated_kb / ".secret").mkdir()
        (dated_kb / ".secret" / "_summary.md").write_text("# Secret\n")

        assert ".secret" not in ops._child_node_paths(dated_kb, ".")


# ============================================================================
# Finding 2: children digest must refuse, not silently drop
# ============================================================================


class TestChildrenDigestRefusesPartialChildren:
    """A digest over a filtered child list would APPROVE deleting the missing children."""

    def _plant_unreadable_child(self, kb: Path, parent: str, name: str) -> str:
        """Create an on-disk child node whose name the node API cannot resolve."""
        child = kb / parent / name
        child.mkdir(parents=True)
        (child / "_summary.md").write_text("# Unreadable\n")
        return f"{parent}/{name}"

    def test_digest_raises_naming_the_offending_path(self, dated_kb):
        bad = self._plant_unreadable_child(dated_kb, "journal", "Bad Name")

        children = ops._direct_child_raw_nodes(dated_kb, "journal")
        expected = ops._child_node_paths(dated_kb, "journal") + [bad]

        with pytest.raises(ops.ChildDigestError) as excinfo:
            ops._children_digest("journal", children, expected_paths=expected)

        assert bad in str(excinfo.value)
        assert bad in excinfo.value.unreadable
        assert excinfo.value.parent_path == "journal"

    def test_prepare_summary_update_refuses_and_names_the_path(self, dated_kb):
        bad = self._plant_unreadable_child(dated_kb, "journal", "Bad Name")

        result = ops.prepare_summary_update(dated_kb, "journal")

        assert result["success"] is False
        assert bad in result["error"]
        assert result["details"]["unreadable_children"] == [bad]
        assert "children_digest" not in result

    def test_no_digest_can_erase_the_hidden_children(self, dated_kb):
        """End-to-end data-loss path, replaying the exact pre-fix exploit.

        Pre-fix, prepare_summary_update() returned success plus a digest taken over
        the *filtered* child list, so a summary composed as if those children did
        not exist passed the stale-write guard and erased them.
        """
        self._plant_unreadable_child(dated_kb, "journal", "Bad Name")
        before = (dated_kb / "journal" / "_summary.md").read_text()

        prepared = ops.prepare_summary_update(dated_kb, "journal")
        assert prepared["success"] is False, "must not hand back a digest over a partial list"

        # Replay the digest the pre-fix code would have produced (readable children
        # only, no expected_paths cross-check) — it must still be refused.
        partial_digest = ops._children_digest(
            "journal", ops._direct_child_raw_nodes(dated_kb, "journal")
        )
        result = ops.write_parent_summary(
            dated_kb, "journal", "# Journal\n\nNo months yet.\n", partial_digest
        )

        assert result["success"] is False
        assert (dated_kb / "journal" / "_summary.md").read_text() == before
        # The children it would have erased are still on disk.
        assert (dated_kb / "journal" / "2026-03" / "_summary.md").exists()
        assert (dated_kb / "journal" / "Bad Name" / "_summary.md").exists()

    def test_healthy_kb_still_produces_a_digest(self, dated_kb):
        result = ops.prepare_summary_update(dated_kb, "journal")

        assert result["success"] is True
        assert result["child_count"] == 2
        assert result["children_digest"].startswith("sha256:")

    def test_digest_still_detects_a_new_child(self, dated_kb):
        first = ops.prepare_summary_update(dated_kb, "journal")["children_digest"]
        _write_node(dated_kb, "journal/2026-08", "# August 2026\n")
        second = ops.prepare_summary_update(dated_kb, "journal")["children_digest"]

        assert first != second

        stale = ops.write_parent_summary(dated_kb, "journal", "# Journal\n", first)
        assert stale["success"] is False
        assert "stale" in stale["error"].lower()

    def test_round_trip_write_succeeds_with_a_fresh_digest(self, dated_kb):
        prepared = ops.prepare_summary_update(dated_kb, "journal")

        result = ops.write_parent_summary(
            dated_kb,
            "journal",
            "# Journal\n\nMarch and July 2026 logs.\n",
            prepared["children_digest"],
        )

        assert result["success"] is True
        assert "March and July" in (dated_kb / "journal" / "_summary.md").read_text()


# ============================================================================
# Finding 3: `kvault check --kb-root` must not silently pass on a bad path
# ============================================================================


class TestCheckExplicitKbRoot:
    def test_nonexistent_explicit_root_is_a_hard_error(self, tmp_path):
        missing = tmp_path / "nope"
        result = CliRunner().invoke(cli, ["check", "--kb-root", str(missing)])

        assert result.exit_code != 0
        assert "does not exist" in result.output

    def test_existing_non_kb_directory_is_a_hard_error(self, tmp_path):
        plain = tmp_path / "just_a_dir"
        plain.mkdir()
        result = CliRunner().invoke(cli, ["check", "--kb-root", str(plain)])

        assert result.exit_code != 0
        assert "not a kvault KB" in result.output

    def test_bad_root_is_loud_in_json_mode_too(self, tmp_path):
        missing = tmp_path / "nope"
        result = CliRunner().invoke(cli, ["check", "--kb-root", str(missing), "--json"])

        assert result.exit_code != 0
        assert result.output.strip()

    def test_group_level_kb_root_is_also_validated(self, tmp_path):
        missing = tmp_path / "nope"
        result = CliRunner().invoke(cli, ["--kb-root", str(missing), "check"])

        assert result.exit_code != 0

    def test_no_kb_root_outside_a_kb_stays_silent(self, tmp_path):
        """BY DESIGN: this runs as a UserPromptSubmit hook in every directory.

        Only EXPLICIT --kb-root paths are hard errors — auto-detection failure
        must remain a silent exit 0.
        """
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["check"])

        assert result.exit_code == 0
        assert result.output == ""

    def test_valid_kb_root_still_runs_checks(self, dated_kb):
        result = CliRunner().invoke(cli, ["check", "--kb-root", str(dated_kb), "--json"])

        assert result.exit_code in (0, 1)  # 1 = hard warnings, still a real run
        assert '"warnings"' in result.output


# ============================================================================
# Regression: the digest guard must not false-positive on case normalization
# ============================================================================


def _fs_is_case_insensitive(probe_dir):
    """True on macOS/APFS-style filesystems, False on ext4/Linux."""
    marker = probe_dir / "CaseProbe"
    marker.mkdir(parents=True, exist_ok=True)
    return (probe_dir / "caseprobe").exists()


class TestChildDigestCaseNormalization:
    """A mixed-case child directory: the guard must be right on BOTH filesystems.

    `_read_node_raw` runs paths through `_normalize_node_path`, which lowercases,
    while the on-disk enumeration does not. What that means depends on the
    filesystem, and the correct behaviour differs:

    - Case-INSENSITIVE (macOS): looking up "marketing" resolves to "Marketing/",
      so the child IS readable. Comparing the two sets raw would call it missing
      and hard-block a legitimate summary update -- the exact inverse of the bug
      the guard exists to catch. Normalizing both sides fixes that.

    - Case-SENSITIVE (Linux, and CI): "marketing" does not resolve, so the child
      genuinely CANNOT be reached through the node API. Refusing is then correct:
      writing that parent summary really would erase it.

    An earlier version of these tests asserted the macOS outcome unconditionally
    and passed locally while failing on CI. The behaviour is not a bug on either
    platform; the test was.
    """

    @pytest.fixture
    def mixed_case_kb(self, tmp_path):
        kb = tmp_path / "kb"
        (kb / ".kvault").mkdir(parents=True)
        _write_node(kb, ".", "# Root\n")
        (kb / "Marketing").mkdir(parents=True, exist_ok=True)
        (kb / "Marketing" / "_summary.md").write_text(
            "---\nupdated: '2026-07-01'\n---\n\n# Marketing\n"
        )
        return kb

    def test_behaviour_matches_filesystem_semantics(self, mixed_case_kb, tmp_path):
        result = ops.prepare_summary_update(mixed_case_kb, ".")
        if _fs_is_case_insensitive(tmp_path):
            # Child is reachable -> must NOT be reported missing.
            assert "error" not in result, result
            paths = {child["path"] for child in result["children"]}
            assert "marketing" in paths, paths
        else:
            # Child is genuinely unreachable -> refusing is the correct outcome.
            assert result.get("error_code") == "validation_error", result
            assert "Marketing" in result["details"]["unreadable_children"], result

    def test_lowercase_child_is_never_flagged(self, tmp_path):
        """Platform-independent control: a normal child must always be readable."""
        kb = tmp_path / "kb"
        (kb / ".kvault").mkdir(parents=True)
        _write_node(kb, ".", "# Root\n")
        _write_node(kb, "marketing", "# Marketing\n")
        result = ops.prepare_summary_update(kb, ".")
        assert "error" not in result, result
        assert "marketing" in {c["path"] for c in result["children"]}, result

    def test_genuinely_missing_child_still_raises(self, tmp_path):
        """The true positive must survive the normalization fix."""
        kb = tmp_path / "kb"
        (kb / ".kvault").mkdir(parents=True)
        _write_node(kb, ".", "# Root\n")
        _write_node(kb, "real_child", "# Real\n")
        with pytest.raises(ops.ChildDigestError) as excinfo:
            ops._children_digest(".", [], expected_paths=["real_child"])
        assert "real_child" in str(excinfo.value)
