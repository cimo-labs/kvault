"""Tests for the work-reporting vocabulary (kvault/core/notes.py)."""

import pytest

from kvault.core import notes as nt


def test_note_codes_are_a_closed_set():
    with pytest.raises(nt.UnknownNoteCode):
        nt.note("surprise", "this code does not exist")


def test_note_carries_why_and_next_on_the_note_itself():
    entry = nt.note("autofilled", "source=auto:cli", why="none supplied", next_step="check it")
    assert entry["code"] == "autofilled"
    assert entry["why"] == "none supplied"
    assert entry["next"] == "check it"
    assert entry["level"] == nt.NORMAL


def test_partial_survives_quiet_tier():
    notes = [
        nt.note("partial", "half-failed"),
        nt.note("autofilled", "invented a value"),
        nt.note("propagate", "stale ancestors"),
    ]
    quiet_visible = nt.visible(notes, nt.QUIET)
    assert [n["code"] for n in quiet_visible] == ["partial"]


def test_tier_filtering_is_monotonic():
    notes = [
        nt.note("partial", "p"),  # QUIET
        nt.note("unchanged", "u"),  # NORMAL
        nt.note("propagate", "pr"),  # EXPLAIN
        nt.note("waited", "w"),  # TRACE
    ]
    assert len(nt.visible(notes, nt.QUIET)) == 1
    assert len(nt.visible(notes, nt.NORMAL)) == 2
    assert len(nt.visible(notes, nt.EXPLAIN)) == 3
    assert len(nt.visible(notes, nt.TRACE)) == 4


def test_tier_from_name_is_typo_safe():
    assert nt.tier_from_name("explain") == nt.EXPLAIN
    assert nt.tier_from_name("EXPLAIN") == nt.EXPLAIN
    assert nt.tier_from_name("exlpain") is None
    assert nt.tier_from_name("") is None
    assert nt.tier_from_name(None) is None


def test_collapse_groups_by_code_with_bounded_examples():
    notes = [nt.note("created", f"new node {i}", detail={"path": f"a/b{i}"}) for i in range(5)] + [
        nt.note("removed", "dropped keys", detail={"path": "a/b0"})
    ]
    collapsed = nt.collapse(notes)
    assert [c["code"] for c in collapsed] == ["created", "removed"]
    created = collapsed[0]
    assert created["count"] == 5
    assert len(created["examples"]) == 3  # bounded
    assert created["examples"][0].startswith("a/b0: ")


def test_collapse_keeps_most_severe_level():
    notes = [
        nt.note("waited", "routine", level=nt.TRACE),
        nt.note("waited", "broke a stale lock", level=nt.NORMAL),
    ]
    collapsed = nt.collapse(notes)
    assert collapsed[0]["level"] == nt.NORMAL


def test_has_partial():
    assert nt.has_partial([nt.note("partial", "x")])
    assert not nt.has_partial([nt.note("unchanged", "x")])
    assert not nt.has_partial([])
