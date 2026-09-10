"""Tests for kvault.core.structure — the lexical tree-shape rules.

Calibrated on the 2026-09 audit fixture: 119 flat children under projects/,
infra/ beside infrastructure/, people/ beside org/people/, summary-less
directories, loose root files, two journal schemas.
"""

from pathlib import Path

from kvault.core import structure as st

# ── name comparison ────────────────────────────────────────────────────


def test_same_words_collision():
    c = st.compare_names("ai_overview", "ai_overviews")
    assert c is not None and c.kind == "same_words" and c.score == 1.0


def test_token_prefix_collision():
    c = st.compare_names("pdp_prompts", "pdp_prompts_concord")
    assert c is not None and c.kind == "prefix"
    c = st.compare_names("aio", "aio_architecture")
    assert c is not None and c.kind == "prefix"


def test_single_token_prefix_collision_needs_four_chars():
    c = st.compare_names("infra", "infrastructure")
    assert c is not None and c.kind == "prefix"
    assert st.compare_names("ai", "aio") is None


def test_overlap_collision():
    c = st.compare_names("code_reviews", "reviews")
    assert c is not None and c.kind == "overlap" and c.score == 0.5


def test_distinct_names_do_not_collide():
    assert st.compare_names("people", "team") is None
    assert st.compare_names("sarah_chen", "sarah_cohen") is None
    assert st.compare_names("alice", "alice") is None


def test_sibling_collisions_orders_strongest_first_and_limits():
    existing = ["ai_overviews", "ai_overview_standup", "ai", "commerce_ai", "ai_overview_1_5"]
    found = st.sibling_collisions(existing, "ai_overview", limit=3)
    assert [c.kind for c in found][0] == "same_words"
    assert found[0].name == "ai_overviews"
    assert len(found) == 3


def test_sibling_pairs_on_flat_fixture():
    names = ["code_reviews", "reviews", "infra", "infrastructure", "people", "team"]
    pairs = st.sibling_pairs(names)
    seen = {(a, c.name) for a, c in pairs}
    assert ("code_reviews", "reviews") in seen
    assert ("infra", "infrastructure") in seen
    assert not any({a, c.name} == {"people", "team"} for a, c in pairs)


# ── clustering ─────────────────────────────────────────────────────────


def test_cluster_by_leading_token():
    names = [
        "aio",
        "aio_architecture",
        "aio_reporting",
        "aio_scaling",
        "pdp_prompts",
        "pdp_prompt_ranking",
        "pdp_assortment_optimization",
        "shopping_agent_ga",
        "shopping_agents",
        "macys_pdp_prompts",
        "uplift_modeling",
    ]
    groups, leftovers = st.cluster_by_leading_token(names, min_size=3)
    assert [(k, len(v)) for k, v in groups] == [("aio", 4), ("pdp", 3)]
    assert leftovers == sorted(
        ["shopping_agent_ga", "shopping_agents", "macys_pdp_prompts", "uplift_modeling"]
    )


# ── ignore file ────────────────────────────────────────────────────────


def test_is_ignored_directory_pattern_covers_subtree():
    patterns = ["sources", "scripts/", "*.log"]
    assert st.is_ignored("sources", patterns)
    assert st.is_ignored("sources/gmail/credentials", patterns)
    assert st.is_ignored("scripts/run.sh", patterns)
    assert st.is_ignored("texput.log", patterns)
    assert not st.is_ignored("people/alice", patterns)
    assert not st.is_ignored(".", patterns)


def test_load_ignore_skips_comments_and_blank_lines(tmp_path):
    (tmp_path / st.IGNORE_FILE).write_text("# tooling\n\nscripts/\nsources\n")
    assert st.load_ignore(tmp_path) == ["scripts", "sources"]
    assert st.load_ignore(tmp_path / "missing") == []


# ── walking ────────────────────────────────────────────────────────────


def _mk(root: Path, rel: str, summary: bool = True) -> Path:
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    if summary:
        (d / "_summary.md").write_text(f"# {rel}\n")
    return d


def test_ghost_dirs_and_reserved_and_ignored(tmp_path):
    (tmp_path / "_summary.md").write_text("# Root\n")
    _mk(tmp_path, "people")
    _mk(tmp_path, "infra", summary=False)
    _mk(tmp_path, "tech/infrastructure", summary=False)
    _mk(tmp_path, "journal/2026-09", summary=False)
    _mk(tmp_path, "people/alice")
    _mk(tmp_path, "people/alice/deep_context", summary=False)
    _mk(tmp_path, "scripts", summary=False)
    _mk(tmp_path, ".kvault", summary=False)
    assert st.ghost_dirs(tmp_path, []) == ["infra", "scripts", "tech", "tech/infrastructure"]
    assert st.ghost_dirs(tmp_path, ["scripts"]) == ["infra", "tech", "tech/infrastructure"]


def test_legacy_meta_json_is_not_a_ghost(tmp_path):
    (tmp_path / "_summary.md").write_text("# Root\n")
    d = _mk(tmp_path, "people/bob", summary=False)
    (d / "_meta.json").write_text("{}")
    _mk(tmp_path, "people")
    assert st.ghost_dirs(tmp_path, []) == []


def test_basename_matches_and_duplicates(tmp_path):
    (tmp_path / "_summary.md").write_text("# Root\n")
    for rel in ("people", "org", "org/people", "models", "tech", "tech/models"):
        _mk(tmp_path, rel)
    assert st.basename_matches(tmp_path, "people", exclude="people", ignore=[]) == ["org/people"]
    dups = st.basename_duplicates(tmp_path, [])
    assert dups == {
        "people": ["org/people", "people"],
        "models": ["models", "tech/models"],
    } or dups == {
        "people": ["people", "org/people"],
        "models": ["models", "tech/models"],
    }


def test_loose_files(tmp_path):
    (tmp_path / "_summary.md").write_text("# Root\n")
    (tmp_path / "AGENTS.md").write_text("rules")
    (tmp_path / "diagram.png").write_bytes(b"x")
    (tmp_path / "todo.md").write_text("x")
    (tmp_path / ".hidden").write_text("x")
    alice = _mk(tmp_path, "people/alice")
    (alice / "notes.txt").write_text("x")
    dc = _mk(tmp_path, "people/alice/deep_context", summary=False)
    (dc / "long.md").write_text("x")
    _mk(tmp_path, "people")
    assert st.loose_files(tmp_path, []) == ["diagram.png", "todo.md", "people/alice/notes.txt"]
    assert st.loose_files(tmp_path, ["*.png", "people/alice/notes.txt"]) == ["todo.md"]


def test_journal_layout_findings(tmp_path):
    (tmp_path / "_summary.md").write_text("# Root\n")
    good = _mk(tmp_path, "journal/2026-09", summary=False)
    (good / "log.md").write_text("# log\n")
    bad = _mk(tmp_path, "journal/y2026/q3/week_37", summary=False)
    (bad / "log_1.md").write_text("# log\n")
    _mk(tmp_path, "journal/archive", summary=False)
    _mk(tmp_path, "archive/journal", summary=False)
    paths = {f["path"] for f in st.journal_layout_findings(tmp_path)}
    assert "journal/y2026" in paths
    # one finding per stray subtree, not one per nested path
    assert "journal/y2026/q3" not in paths
    assert "journal/y2026/q3/week_37/log_1.md" not in paths
    assert "journal/archive" in paths
    assert "archive/journal" in paths
    assert "journal/2026-09" not in paths
    assert "journal/2026-09/log.md" not in paths
