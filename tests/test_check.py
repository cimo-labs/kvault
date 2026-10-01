"""Tests for kvault.core.check — propagation staleness detection."""

import os
import time
from datetime import date, datetime
from pathlib import Path

from kvault.core.check import _get_updated_date, check_journal, check_propagation
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


def _at(day: date, hour: int) -> float:
    return datetime.combine(day, datetime.min.time()).replace(hour=hour).timestamp()


def test_same_day_child_edit_after_its_parent_is_stale(tmp_path):
    """0.17: `updated` has day granularity, so a child rewritten hours after its
    parent on the same day was never reported. Equal days fall back to mtime."""
    kb = tmp_path / "kb"
    parent_dir = kb / "category"
    child = parent_dir / "entity" / "_summary.md"
    day = date.today()
    for f, title in ((kb / "_summary.md", "Root"), (parent_dir / "_summary.md", "Category")):
        _write_summary(f, f"# {title}\n", meta={"updated": day.isoformat()})
    _write_summary(child, "# Entity\n", meta={"updated": day.isoformat()})
    for f in kb.rglob("_summary.md"):
        os.utime(f, (_at(day, 1), _at(day, 1)))  # the 01:00 rollup job
    os.utime(child, (_at(day, 15), _at(day, 15)))  # a daytime edit
    stale = [w for w in check_propagation(kb, threshold_minutes=5) if "entity" in w]
    assert len(stale) == 1 and "840m newer" in stale[0]
    # a fresh clone gives every file the same mtime: nothing to report
    for f in kb.rglob("_summary.md"):
        os.utime(f, (_at(day, 16), _at(day, 16)))
    assert not [w for w in check_propagation(kb, threshold_minutes=5) if "entity" in w]


def test_a_later_checkout_of_an_old_child_is_not_stale(tmp_path):
    """A pulled clone gives a moved node the checkout time. Its `updated` day is
    months old, like its parent's, so its new mtime says nothing about edits."""
    kb = tmp_path / "kb"
    parent_dir = kb / "category"
    child = parent_dir / "entity" / "_summary.md"
    old = date(2026, 5, 16)
    for f, title in ((kb / "_summary.md", "Root"), (parent_dir / "_summary.md", "Category")):
        _write_summary(f, f"# {title}\n", meta={"updated": old.isoformat()})
        os.utime(f, (_at(old, 9), _at(old, 9)))
    _write_summary(child, "# Entity\n", meta={"updated": old.isoformat()})  # mtime: now
    assert not [w for w in check_propagation(kb, threshold_minutes=5) if "entity" in w]


# ── write_entity ancestors tests ─────────────────────────────────────


def test_write_entity_returns_ancestors(empty_kb):
    """write_entity result should include ancestors list with current content."""
    from kvault.core import operations as ops

    result = ops.write_entity(
        empty_kb,
        path="people/friends/test_person",
        content="# Test Person\n\nA test entity.\n",
        meta={"source": "manual", "aliases": ["Test Person"]},
        create=True,
    )

    assert result["success"] is True
    assert "ancestors" in result
    assert isinstance(result["ancestors"], list)
    assert len(result["ancestors"]) >= 1


def test_ancestors_includes_root(empty_kb):
    """ancestors list should include '.' for root."""
    from kvault.core import operations as ops

    result = ops.write_entity(
        empty_kb,
        path="people/friends/test_person",
        content="# Test Person\n",
        meta={"source": "manual", "aliases": ["Test Person"]},
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


def test_a_timestamp_in_updated_is_read_as_its_day(tmp_path):
    """`updated: 2026-10-01 09:30:00` is a datetime; comparing it with a date raised."""
    kb = tmp_path / "kb"
    _write_summary(kb / "_summary.md", "# Root\n", meta={"updated": "2026-02-01"})
    (kb / "category").mkdir(parents=True, exist_ok=True)
    (kb / "category" / "_summary.md").write_text(
        "---\nupdated: 2026-02-02 09:30:00\n---\n# Category\n"
    )
    assert check_propagation(kb, threshold_minutes=5)  # a finding, not a TypeError


def test_propagate_and_log_walk_managed_directories_only(tmp_path):
    """0.17.1: a custom journal layout listed in .kvaultignore, and a node's own
    deep_context/ notes, were compared like children; every other check
    already walked managed directories only."""
    kb = tmp_path / "kb"
    old = date(2026, 5, 16)
    for rel in (".", "logs", "logs/y2026", "people", "people/ada", "people/ada/deep_context"):
        f = (kb if rel == "." else kb / rel) / "_summary.md"
        _write_summary(f, f"# {rel}\n", meta={"updated": old.isoformat()})
        os.utime(f, (_at(old, 9), _at(old, 9)))
    for rel in ("logs/y2026/week_40", "people/ada/deep_context/notes"):  # newer than their parents
        _write_summary(
            kb / rel / "_summary.md", "# x\n", meta={"updated": date.today().isoformat()}
        )
    (kb / ".kvaultignore").write_text("logs/\n")
    assert not check_propagation(kb, threshold_minutes=5)
    assert not check_journal(kb)
    (kb / ".kvaultignore").unlink()
    assert [w for w in check_propagation(kb, threshold_minutes=5) if "week_40" in w]
    assert check_journal(kb)  # the ignored tree's leaf was edited today
