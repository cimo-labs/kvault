"""Tests for kvault.cli.check — propagation staleness detection."""

import os
import time
from pathlib import Path

from kvault.cli.check import check_propagation, _get_updated_date
from kvault.core.frontmatter import build_frontmatter


def _write_summary(path: Path, content: str, meta: dict = None):
    """Helper to write a _summary.md with optional frontmatter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if meta:
        text = build_frontmatter(meta) + content
    else:
        text = content
    path.write_text(text)


# ── Frontmatter date tests ──────────────────────────────────────────


def test_propagation_detects_stale_parent(sample_kb):
    """Child with newer 'updated' date than parent should trigger warning."""
    # Update alice_smith to have a newer date than the friends summary
    child = sample_kb / "people" / "friends" / "alice_smith" / "_summary.md"
    _write_summary(
        child,
        "# Alice Smith\n\nUpdated content.\n",
        meta={
            "created": "2026-01-15",
            "updated": "2026-02-05",
            "source": "manual",
            "aliases": ["Alice Smith"],
        },
    )

    # Parent summary has no frontmatter date — give it an older one
    parent = sample_kb / "people" / "friends" / "_summary.md"
    _write_summary(
        parent,
        "# Friends\n\nStale summary.\n",
        meta={
            "updated": "2026-01-20",
        },
    )

    warnings = check_propagation(sample_kb, threshold_minutes=5)
    prop_warnings = [w for w in warnings if "alice_smith" in w]
    assert len(prop_warnings) >= 1
    assert "PROPAGATE" in prop_warnings[0]


def test_propagation_clean_when_dates_match(tmp_path):
    """Same updated dates on parent and child should produce no warning."""
    kb = tmp_path / "kb"
    parent_dir = kb / "people" / "friends"
    child_dir = parent_dir / "alice"

    _write_summary(kb / "_summary.md", "# Root\n", meta={"updated": "2026-02-01"})
    _write_summary(parent_dir / "_summary.md", "# Friends\n", meta={"updated": "2026-02-01"})
    _write_summary(
        child_dir / "_summary.md",
        "# Alice\n",
        meta={
            "updated": "2026-02-01",
            "source": "manual",
            "aliases": ["Alice"],
        },
    )

    warnings = check_propagation(kb, threshold_minutes=5)
    # No warnings expected — dates match
    prop_warnings = [w for w in warnings if "alice" in w]
    assert len(prop_warnings) == 0


def test_propagation_falls_back_to_mtime(tmp_path):
    """Without frontmatter dates, should use mtime with threshold."""
    kb = tmp_path / "kb"
    parent_dir = kb / "category"
    child_dir = parent_dir / "entity"

    # Write parent first (no frontmatter)
    _write_summary(kb / "_summary.md", "# Root\n")
    _write_summary(parent_dir / "_summary.md", "# Category\n")

    # Wait and write child so mtime differs beyond threshold
    parent_summary = parent_dir / "_summary.md"
    # Set parent mtime to 10 minutes ago
    old_time = time.time() - 600
    os.utime(parent_summary, (old_time, old_time))

    _write_summary(child_dir / "_summary.md", "# Entity\n")

    warnings = check_propagation(kb, threshold_minutes=5)
    prop_warnings = [w for w in warnings if "entity" in w]
    assert len(prop_warnings) >= 1
    assert "newer" in prop_warnings[0]


# ── write_entity ancestors tests ─────────────────────────────────────


def test_write_entity_returns_ancestors(empty_kb):
    """write_entity result should include ancestors list with current content."""
    from kvault.mcp.server import handle_kvault_write_entity

    result = handle_kvault_write_entity(
        path="people/friends/test_person",
        meta={"source": "manual", "aliases": ["Test Person"]},
        content="# Test Person\n\nA test entity.\n",
        create=True,
    )

    assert result["success"] is True
    assert "ancestors" in result
    assert isinstance(result["ancestors"], list)
    assert len(result["ancestors"]) >= 1


def test_ancestors_includes_root(empty_kb):
    """ancestors list should include '.' for root."""
    from kvault.mcp.server import handle_kvault_write_entity

    result = handle_kvault_write_entity(
        path="people/friends/test_person",
        meta={"source": "manual", "aliases": ["Test Person"]},
        content="# Test Person\n",
        create=True,
    )

    ancestor_paths = [a["path"] for a in result["ancestors"]]
    assert "." in ancestor_paths
    # Should also include intermediate ancestors
    assert "people" in ancestor_paths


# ── _get_updated_date helper tests ───────────────────────────────────


def test_get_updated_date_parses_frontmatter(tmp_path):
    """_get_updated_date should extract date from frontmatter."""
    from datetime import date

    summary = tmp_path / "_summary.md"
    _write_summary(summary, "# Test\n", meta={"updated": "2026-02-05"})

    result = _get_updated_date(summary)
    assert result == date(2026, 2, 5)


def test_get_updated_date_falls_back_to_created(tmp_path):
    """_get_updated_date should use 'created' if 'updated' is missing."""
    from datetime import date

    summary = tmp_path / "_summary.md"
    _write_summary(summary, "# Test\n", meta={"created": "2026-01-10"})

    result = _get_updated_date(summary)
    assert result == date(2026, 1, 10)


def test_get_updated_date_returns_none_without_frontmatter(tmp_path):
    """_get_updated_date should return None when no frontmatter."""
    summary = tmp_path / "_summary.md"
    summary.write_text("# Test\n\nNo frontmatter here.\n")

    result = _get_updated_date(summary)
    assert result is None
