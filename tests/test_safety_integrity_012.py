"""0.12 regressions for path containment and the shared integrity audit."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from kvault.cli.main import cli
from kvault.core import operations as ops
from kvault.core.frontmatter import build_frontmatter
from kvault.core.paths import (
    PathSafetyError,
    resolve_node_path,
    resolve_within_root,
    validate_node_target,
)
from kvault.core.search import scan_search_documents
from kvault.core.storage import SimpleStorage, scan_entities
from kvault.core.validation import audit_kb, compute_children_digest


def _summary(path: Path, body: str, meta=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = build_frontmatter(meta) + body if meta is not None else body
    path.write_text(text, encoding="utf-8")


def _entity_meta(**extra):
    return {
        "created": "2026-07-19",
        "updated": "2026-07-19",
        "source": "test",
        "aliases": [],
        **extra,
    }


def test_canonical_resolver_rejects_absolute_and_traversal(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()

    with pytest.raises(PathSafetyError, match="relative"):
        resolve_within_root(root, tmp_path / "outside")
    with pytest.raises(PathSafetyError, match="traversal"):
        resolve_within_root(root, "branch/../../outside")


def test_canonical_resolver_rejects_symlink_escape(tmp_path):
    root = tmp_path / "kb"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "alias").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathSafetyError, match="escapes"):
        resolve_within_root(root, "alias/secret")
    with pytest.raises(PathSafetyError, match="Symlink"):
        resolve_node_path(root, "alias")


@pytest.mark.parametrize("target", [".", ".kvault", "journal/2026-07", "_internal"])
def test_safe_delete_target_rejects_reserved_namespaces(tmp_path, target):
    root = tmp_path / "kb"
    root.mkdir()
    with pytest.raises(PathSafetyError):
        validate_node_target(root, target, require_exists=False)


def test_safe_delete_target_requires_real_node(tmp_path):
    root = tmp_path / "kb"
    non_node = root / "people" / "orphan"
    non_node.mkdir(parents=True)

    with pytest.raises(PathSafetyError, match="not a semantic node"):
        validate_node_target(root, "people/orphan")

    _summary(non_node / "_summary.md", "# Orphan\n\nSubstantive body.\n", _entity_meta())
    assert validate_node_target(root, "people/orphan") == non_node.resolve()


def test_simple_storage_cannot_delete_outside_root(tmp_path):
    root = tmp_path / "kb"
    victim = tmp_path / "victim"
    root.mkdir()
    victim.mkdir()
    (victim / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.warns(DeprecationWarning):
        storage = SimpleStorage(root)
    with pytest.raises(PathSafetyError):
        storage.delete_entity("../victim")
    assert (victim / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_read_and_compatibility_surfaces_refuse_symlink_summary_escape(tmp_path):
    root = tmp_path / "kb"
    outside = tmp_path / "outside-summary.md"
    _summary(root / "_summary.md", "# Root\n", _entity_meta())
    node = root / "people" / "alice"
    node.mkdir(parents=True)
    outside.write_text("# Secret\n\nMust stay outside the KB.\n", encoding="utf-8")
    (node / "_summary.md").symlink_to(outside)

    assert ops.read_node(root, "people/alice") is None
    assert ops.read_summary(root, "people/alice") is None
    assert all(document.path != "people/alice" for document in scan_search_documents(root))
    assert scan_entities(root) == []

    with pytest.warns(DeprecationWarning):
        storage = SimpleStorage(root)
    with pytest.raises(PathSafetyError, match="Symlink"):
        storage.read_summary("people/alice")
    with pytest.raises(PathSafetyError, match="Symlink"):
        storage.write_summary("people/alice", "# Overwrite\n")
    assert outside.read_text(encoding="utf-8") == "# Secret\n\nMust stay outside the KB.\n"


def test_audit_rejects_symlinks_inside_temporal_storage(tmp_path):
    root = tmp_path / "kb"
    outside = tmp_path / "outside-events"
    _summary(root / "_summary.md", "# Root\n", _entity_meta())
    outside.mkdir()
    (outside / "01").mkdir()
    (outside / "01" / "evt_outside.md").write_text("outside", encoding="utf-8")
    event_base = root / "journal" / "events"
    event_base.mkdir(parents=True)
    (event_base / "2026").symlink_to(outside, target_is_directory=True)

    result = audit_kb(root)

    journal_issues = [
        issue for issue in result["issues"] if issue["type"] == "temporal_journal_invalid"
    ]
    assert len(journal_issues) == 1
    assert "symlink" in journal_issues[0]["message"].lower()


def test_simple_storage_writes_canonical_frontmatter_only(tmp_path):
    with pytest.warns(DeprecationWarning):
        storage = SimpleStorage(tmp_path)
    node = storage.create_entity(
        "people/alice",
        {
            "created": "2026-07-19",
            "last_updated": "2026-07-19",
            "sources": ["test"],
            "aliases": ["Alice"],
        },
        "# Alice\n\nSubstantive profile.\n",
    )

    assert not (node / "_meta.json").exists()
    raw = (node / "_summary.md").read_text(encoding="utf-8")
    assert "updated: '2026-07-19'" in raw
    assert "source: test" in raw
    assert "last_updated:" not in raw
    assert "sources:" not in raw


def test_audit_reports_malformed_frontmatter_and_orphan_hierarchy(tmp_path):
    root = tmp_path / "kb"
    _summary(root / "_summary.md", "# Root\n")
    _summary(root / "people" / "contacts" / "alice" / "_summary.md", "---\n- bad\n---\n")

    result = audit_kb(root)
    types = {issue["type"] for issue in result["issues"]}
    assert result["valid"] is False
    assert "malformed_frontmatter" in types
    assert "missing_parent_summary" in types


def test_audit_reports_required_fields_and_incomplete_entity(tmp_path):
    root = tmp_path / "kb"
    _summary(root / "_summary.md", "# Root\n")
    _summary(root / "people" / "_summary.md", "# People\n")
    _summary(root / "people" / "alice" / "_summary.md", "# Alice\n\nTBD\n", {})

    result = audit_kb(root)
    by_type = {issue["type"] for issue in result["issues"]}
    assert "missing_frontmatter_fields" in by_type
    assert "incomplete_entity" in by_type


def test_audit_verifies_exact_persisted_children_digest(tmp_path):
    root = tmp_path / "kb"
    _summary(root / "_summary.md", "# Root\n", _entity_meta())
    _summary(root / "people" / "_summary.md", "# People\n", _entity_meta())
    _summary(
        root / "people" / "alice" / "_summary.md",
        "# Alice\n\nSubstantive profile.\n",
        _entity_meta(),
    )
    digest = compute_children_digest(root, ".")
    _summary(
        root / "_summary.md",
        "# Root\n",
        _entity_meta(children_digest=digest),
    )

    clean = audit_kb(root)
    assert not [issue for issue in clean["issues"] if issue["type"] == "children_digest_mismatch"]

    _summary(root / "people" / "_summary.md", "# People\n\nChanged.\n", _entity_meta())
    stale = audit_kb(root)
    mismatch = [issue for issue in stale["issues"] if issue["type"] == "children_digest_mismatch"]
    assert len(mismatch) == 1
    assert mismatch[0]["path"] == "."


def test_shared_digest_matches_strict_summary_workflow(tmp_path):
    from kvault.core import operations as ops

    root = tmp_path / "kb"
    _summary(root / "_summary.md", "# Root\n", _entity_meta())
    _summary(root / "people" / "_summary.md", "# People\n", _entity_meta())
    _summary(root / "journal" / "_summary.md", "# Journal\n", _entity_meta())

    prepared = ops.prepare_summary_update(root, ".")
    assert prepared["success"] is True
    assert compute_children_digest(root, ".") == prepared["children_digest"]


def test_children_digest_rejects_traversal_and_symlink_aliases(tmp_path):
    root = tmp_path / "kb"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()

    with pytest.raises(PathSafetyError, match="traversal"):
        compute_children_digest(root, "../outside")

    alias = root / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathSafetyError, match="Symlink"):
        compute_children_digest(root, "alias")


def test_check_json_missing_root_is_nonzero_and_structured(tmp_path):
    missing = tmp_path / "missing"
    result = CliRunner().invoke(cli, ["check", "--kb-root", str(missing), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert payload["error_code"] == "not_found"


def test_check_json_delegates_malformed_frontmatter_to_core_audit(tmp_path):
    root = tmp_path / "kb"
    _summary(root / "_summary.md", "---\nnot: [closed\n---\n# Root\n")

    result = CliRunner().invoke(cli, ["check", "--kb-root", str(root), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert any(issue["type"] == "malformed_frontmatter" for issue in payload["issues"])
