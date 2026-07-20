"""Hardening regressions: path containment, write lock, strict frontmatter."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kvault.core import operations as ops
from kvault.core.frontmatter import (
    FrontmatterError,
    build_frontmatter,
    parse_frontmatter,
    parse_frontmatter_strict,
)
from kvault.core.locks import KBWriteLock, LockError, atomic_write_text
from kvault.core.paths import (
    PathSafetyError,
    resolve_node_path,
    resolve_within_root,
    validate_node_target,
)
from kvault.core.storage import SimpleStorage


def _entity_meta(**extra):
    return {
        "created": "2026-07-19",
        "updated": "2026-07-19",
        "source": "test",
        "aliases": [],
        **extra,
    }


def _make_entity(root: Path, path: str, body: str = "# Node\n\nBody.\n") -> Path:
    node = root / path
    node.mkdir(parents=True, exist_ok=True)
    (node / "_summary.md").write_text(build_frontmatter(_entity_meta()) + body)
    return node


# ---------------------------------------------------------------------------
# paths.py
# ---------------------------------------------------------------------------


def test_resolver_rejects_absolute_and_traversal(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()

    with pytest.raises(PathSafetyError, match="relative"):
        resolve_within_root(root, tmp_path / "outside")
    with pytest.raises(PathSafetyError, match="traversal"):
        resolve_within_root(root, "branch/../../outside")
    with pytest.raises(PathSafetyError, match="NUL"):
        resolve_within_root(root, "branch/\x00evil")


def test_resolver_rejects_symlink_escape(tmp_path):
    root = tmp_path / "kb"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "alias").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathSafetyError, match="escapes"):
        resolve_within_root(root, "alias/secret")
    with pytest.raises(PathSafetyError, match="Symlink"):
        resolve_node_path(root, "alias")


@pytest.mark.parametrize("target", [".", "", ".kvault", "_internal"])
def test_delete_target_rejects_root_and_reserved(tmp_path, target):
    root = tmp_path / "kb"
    root.mkdir()
    with pytest.raises(PathSafetyError):
        validate_node_target(root, target, require_exists=False)


def test_delete_target_requires_real_node(tmp_path):
    root = tmp_path / "kb"
    non_node = root / "people" / "orphan"
    non_node.mkdir(parents=True)

    with pytest.raises(PathSafetyError, match="not a semantic node"):
        validate_node_target(root, "people/orphan")

    _make_entity(root, "people/orphan")
    assert validate_node_target(root, "people/orphan") == non_node.resolve()


def test_journal_remains_a_valid_node_path(tmp_path):
    # personal KBs may have a real semantic node named "journal"; unlike the
    # 0.12 redesign, it is not a reserved namespace.
    root = tmp_path / "kb"
    _make_entity(root, "journal")
    assert validate_node_target(root, "journal") == (root / "journal").resolve()


# ---------------------------------------------------------------------------
# operations-level containment
# ---------------------------------------------------------------------------


def test_delete_entity_refuses_root_and_control_dir(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    (root / ".kvault").mkdir()
    _make_entity(root, "people/alice")

    for target in ("", ".", ".kvault"):
        result = ops.delete_entity(root, target)
        assert not result.get("success"), target
    assert root.exists()
    assert (root / ".kvault").exists()

    assert ops.delete_entity(root, "people/alice").get("success")
    assert not (root / "people" / "alice").exists()


def test_delete_entity_refuses_non_node_directory(tmp_path):
    root = tmp_path / "kb"
    plain = root / "people" / "raw_files"
    plain.mkdir(parents=True)
    result = ops.delete_entity(root, "people/raw_files")
    assert not result.get("success")
    assert plain.exists()


def test_write_node_rejects_symlink_component(tmp_path):
    root = tmp_path / "kb"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "people").symlink_to(outside, target_is_directory=True)

    result = ops.write_entity(
        root,
        "people/alice",
        "# Alice\n",
        meta=_entity_meta(),
        create=True,
    )
    assert not result.get("success")
    assert not (outside / "alice").exists()


def test_move_entity_refuses_own_subtree(tmp_path):
    root = tmp_path / "kb"
    _make_entity(root, "projects/big")
    result = ops.move_entity(root, "projects/big", "projects/big/nested")
    assert not result.get("success")


def test_simple_storage_cannot_escape_root(tmp_path):
    root = tmp_path / "kb"
    victim = tmp_path / "victim"
    root.mkdir()
    victim.mkdir()
    (victim / "keep.txt").write_text("keep")

    storage = SimpleStorage(root)
    with pytest.raises(PathSafetyError):
        storage.delete_entity("../victim")
    with pytest.raises(PathSafetyError):
        storage.write_summary("../victim/planted", "# Evil\n")
    assert (victim / "keep.txt").read_text() == "keep"


def test_simple_storage_write_refuses_symlink_summary(tmp_path):
    root = tmp_path / "kb"
    outside = tmp_path / "outside-summary.md"
    outside.write_text("# Secret\n")
    node = root / "people" / "alice"
    node.mkdir(parents=True)
    (node / "_summary.md").symlink_to(outside)

    storage = SimpleStorage(root)
    with pytest.raises(PathSafetyError, match="Symlink"):
        storage.write_summary("people/alice", "# Overwrite\n")
    assert outside.read_text() == "# Secret\n"


def test_validate_kb_flags_malformed_frontmatter(tmp_path):
    root = tmp_path / "kb"
    node = root / "people" / "broken"
    node.mkdir(parents=True)
    (node / "_summary.md").write_text("---\nkey: [unclosed\n---\n\n# Broken\n")

    result = ops.validate_kb(root)
    types = {issue["type"] for issue in result["issues"]}
    assert "malformed_frontmatter" in types


# ---------------------------------------------------------------------------
# frontmatter strictness
# ---------------------------------------------------------------------------


def test_tolerant_parse_survives_malformed_blocks():
    assert parse_frontmatter("---\nkey: [broken\n---\n\nbody") == (
        {},
        "---\nkey: [broken\n---\n\nbody",
    )
    # A non-mapping payload degrades to no-frontmatter instead of returning
    # a list to callers that expect a dict.
    meta, _ = parse_frontmatter("---\n- a\n- b\n---\n\nbody")
    assert meta == {}


def test_strict_parse_rejects_malformed_blocks():
    with pytest.raises(FrontmatterError, match="Unclosed"):
        parse_frontmatter_strict("---\nkey: value\n")
    with pytest.raises(FrontmatterError, match="mapping"):
        parse_frontmatter_strict("---\n- a\n---\n\nbody")
    with pytest.raises(FrontmatterError, match="Duplicate"):
        parse_frontmatter_strict("---\nkey: one\nkey: two\n---\n\nbody")


def test_strict_parse_accepts_valid_block():
    meta, body = parse_frontmatter_strict("---\nsource: test\naliases: []\n---\n\n# Hi\n")
    assert meta == {"source": "test", "aliases": []}
    assert body == "# Hi\n"


def test_build_frontmatter_requires_mapping_and_safe_dump():
    with pytest.raises(TypeError):
        build_frontmatter(["not", "a", "mapping"])
    rendered = build_frontmatter({"aliases": ("a", "b")})
    assert "python/" not in rendered  # no unsafe python-object tags


# ---------------------------------------------------------------------------
# locks
# ---------------------------------------------------------------------------


def test_atomic_write_text_replaces_content(tmp_path):
    target = tmp_path / "deep" / "file.md"
    atomic_write_text(target, "one")
    atomic_write_text(target, "two")
    assert target.read_text() == "two"
    assert list(target.parent.glob("*.tmp")) == []


def test_lock_is_reentrant_within_process(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    with KBWriteLock(root):
        with KBWriteLock(root):
            assert (root / ".kvault" / "lock").exists()
        assert (root / ".kvault" / "lock").exists()
    assert not (root / ".kvault" / "lock").exists()


def test_lock_blocks_second_process(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; sys.path.insert(0, sys.argv[2]); "
            "from kvault.core.locks import KBWriteLock; "
            "lock = KBWriteLock(sys.argv[1]); lock.acquire(); "
            "print('held', flush=True); time.sleep(3); lock.release()",
            str(root),
            str(Path(__file__).resolve().parents[1]),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(LockError):
            KBWriteLock(root, timeout=0.5).acquire()
    finally:
        holder.wait(timeout=10)

    # Holder released; acquire must now succeed.
    lock = KBWriteLock(root, timeout=2.0)
    lock.acquire()
    lock.release()


def test_lock_breaks_stale_dead_owner(tmp_path):
    root = tmp_path / "kb"
    lock_dir = root / ".kvault" / "lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / "owner.json").write_text(
        json.dumps({"pid": 99999999, "acquired_at": "2026-01-01T00:00:00+00:00"})
    )
    old = time.time() - 120
    os.utime(lock_dir, (old, old))

    lock = KBWriteLock(root, timeout=2.0)
    lock.acquire()
    try:
        assert (lock_dir / "owner.json").exists()
        owner = json.loads((lock_dir / "owner.json").read_text())
        assert owner["pid"] == os.getpid()
    finally:
        lock.release()


def test_lock_respects_live_owner(tmp_path):
    root = tmp_path / "kb"
    lock_dir = root / ".kvault" / "lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / "owner.json").write_text(
        json.dumps({"pid": os.getpid(), "acquired_at": "2026-01-01T00:00:00+00:00"})
    )
    old = time.time() - 120  # older than grace, younger than hard-stale
    os.utime(lock_dir, (old, old))

    with pytest.raises(LockError):
        KBWriteLock(root, timeout=0.5).acquire()
