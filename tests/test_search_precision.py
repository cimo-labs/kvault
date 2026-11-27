"""Search precision (0.17), from a field report on a ~700-node KB.

- The phrase bonus matched raw substrings: the query "ai" earned it inside
  "email" and "detail", and the false title/path match blocked collapse.
- Nodes parked under ``deep_context/`` were searched as live entities and
  competed with their keepers; the collapse rule checked only the leaf name,
  so a parked copy could collapse its keeper's parents. A match there is now
  folded into its keeper when the keeper matches too.
- ``.kvaultignore`` was not honored.
"""

from pathlib import Path

import pytest

from kvault.core import operations as ops


def _node(kb: Path, rel: str, body: str) -> None:
    d = kb if rel == "." else kb / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "_summary.md").write_text("---\nsource: manual\naliases: []\n---\n" + body)


def _kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / ".kvault").mkdir(parents=True)
    _node(kb, ".", "# Root\n\nRoot.\n")
    _node(kb, "projects", "# Projects\n\nProject notes.\n")
    return kb


def _paths(result):
    return [r["path"] for r in result["results"]]


def test_short_queries_match_whole_words_only(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb, "projects/email_templates", "# Email templates\n\nEmail detail for the html mailer.\n"
    )
    _node(kb, "projects/ai_strategy", "# AI strategy\n\nWhere AI helps.\n")
    result = ops.search_nodes(kb, "ai", limit=10)
    assert _paths(result)[0] == "projects/ai_strategy"
    email = [r for r in result["results"] if r["path"] == "projects/email_templates"]
    assert not email or not {"title", "path"} & set(email[0]["matched_fields"])


def test_compact_snippet_keeps_the_source_invoice_status(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb,
        "accounts",
        "# Accounts\n\nThe handoff contains one outstanding item for accounting. "
        "The unpaid invoice still requires approval. Follow-up is scheduled for next Thursday "
        "after the review committee meets to review the supporting details and attachments. "
        "Keep the original note with the ledger.",
    )
    hit = ops.search_nodes(kb, "follow-up", compact=True)["results"][0]
    assert hit["snippet"].startswith("...unpaid invoice still requires approval. Follow-up")
    assert len(hit["snippet"]) <= 120


def test_snippet_ends_before_a_partial_approval_status(tmp_path):
    kb = _kb(tmp_path)
    _node(
        kb,
        "reviews",
        "# Desk\n\nLaunch notice: release is approved-pending-review and awaits signoff.",
    )
    budget = len("# Desk Launch notice: release is approved") + 6
    hit = ops.search_nodes(kb, "launch", snippet_chars=budget)["results"][0]
    assert hit["snippet"] == "# Desk Launch notice: release is..."


@pytest.mark.parametrize(
    ("query", "opening", "evidence"),
    [
        ("paid invoice", "That unpaid invoice remains open.", "The PAID / invoice is separate."),
        ("AI decision", "Read the email with the details.", "The AI review starts tomorrow."),
    ],
)
def test_snippet_anchors_the_same_whole_tokens_as_ranking(tmp_path, query, opening, evidence):
    kb = _kb(tmp_path)
    _node(kb, "reviews", "# Desk\n\n" + opening + " Some background." * 20 + " " + evidence)
    hit = ops.search_nodes(kb, query, compact=True)["results"][0]
    assert evidence in hit["snippet"]


def test_snippet_omits_an_overlong_word_instead_of_making_a_new_label(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "approvals", "unapproved")
    for budget in range(10):
        hit = ops.search_nodes(kb, "unapproved", snippet_chars=budget)["results"][0]
        assert hit["snippet"] == "..."[:budget]


def test_parked_copies_are_background(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "projects/keeper", "# Keeper\n\nThe zebra protocol owner.\n")
    _node(kb, "projects/keeper/deep_context/old_twin", "# Old twin\n\nThe zebra protocol owner.\n")
    result = ops.search_nodes(kb, "zebra protocol", limit=10)
    assert _paths(result) == ["projects/keeper"]
    note = next(n for n in result["notes"] if (n.get("detail") or {}).get("kind") == "background")
    assert note["detail"]["hidden"] == 1
    assert note["detail"]["paths"] == ["projects/keeper/deep_context/old_twin"]
    assert "projects/keeper/deep_context/old_twin" in note["text"]
    every = ops.search_nodes(kb, "zebra protocol", limit=10, include_background=True)
    assert "projects/keeper/deep_context/old_twin" in _paths(every)


def test_a_fact_only_in_long_form_notes_is_still_found(tmp_path):
    """deep_context/ also holds an entity's long-form notes: a match there whose
    keeper does not match is returned, not hidden."""
    kb = _kb(tmp_path)
    _node(kb, "projects/keeper", "# Keeper\n\nThe current state.\n")
    _node(kb, "projects/keeper/deep_context/notes", "# Notes\n\nThe quokka migration plan.\n")
    result = ops.search_nodes(kb, "quokka migration", limit=10)
    assert _paths(result) == ["projects/keeper/deep_context/notes"]
    assert not [n for n in result.get("notes", []) if (n.get("detail") or {}).get("kind")]


def test_a_strong_notes_match_beats_a_weak_keeper_match(tmp_path):
    """Folding needs the keeper to score at least half as much: a keeper that
    shares one word with the query does not hide the notes that hold the fact."""
    kb = _kb(tmp_path)
    _node(kb, "projects/keeper", "# Keeper\n\nThe migration is done.\n")
    _node(
        kb,
        "projects/keeper/deep_context/notes",
        "# Notes\n\nThe quokka migration plan: quokka cutover, quokka rollback.\n",
    )
    result = ops.search_nodes(kb, "quokka migration", limit=10)
    assert "projects/keeper/deep_context/notes" in _paths(result)


def test_a_parked_copy_never_collapses_its_keepers_parents(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "projects", "# Projects\n\nOwners of the zebra protocol live here.\n")
    _node(kb, "projects/keeper", "# Keeper\n\nAn unrelated node.\n")
    _node(
        kb,
        "projects/keeper/deep_context/old_twin",
        "# Old twin\n\nThe zebra protocol, the zebra protocol, the zebra protocol owners.\n",
    )
    result = ops.search_nodes(kb, "zebra protocol", limit=10, include_background=True)
    assert "projects" in _paths(result) and result["collapsed"] == 0


def test_ignored_paths_are_not_searched(tmp_path):
    kb = _kb(tmp_path)
    (kb / ".kvaultignore").write_text("vendor/\n")
    _node(kb, "vendor/lib", "# Lib\n\nThe zebra protocol shim.\n")
    _node(kb, "projects/zebra", "# Zebra\n\nThe zebra protocol.\n")
    assert _paths(ops.search_nodes(kb, "zebra protocol", limit=10)) == ["projects/zebra"]


def test_a_fold_never_drops_the_best_match_off_the_page(tmp_path):
    """Folding into a keeper that falls outside the returned page would lose
    the best match; such a match is restored."""
    kb = _kb(tmp_path)
    _node(kb, "people", "# People\n\nPeople.\n")
    _node(kb, "people/alice", "# Alice\n\nAlice keeps quokka notes.\n")
    _node(
        kb,
        "people/alice/deep_context",
        "# Notes\n\nquokka migration quokka migration quokka migration plan.\n",
    )
    for i in range(5):
        _node(kb, f"projects/p{i}", f"# P{i}\n\nquokka migration plan, quokka migration.\n")
    page = _paths(ops.search_nodes(kb, "quokka migration", limit=5))
    assert "people/alice" not in page  # the keeper ranks below the page
    assert "people/alice/deep_context" in page
