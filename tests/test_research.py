"""Tests for kvault.core.research helpers."""

from pathlib import Path

from kvault.core.research import EntityResearcher


def _write_entity(kg_root: Path, rel_path: str, name: str, aliases: list[str]) -> None:
    entity_dir = kg_root / rel_path
    entity_dir.mkdir(parents=True, exist_ok=True)
    alias_lines = "\n".join(f"  - {alias}" for alias in aliases)
    summary = (
        "---\n"
        "source: test\n"
        "aliases:\n"
        f"{alias_lines}\n"
        "---\n"
        f"# {name}\n\n"
        "Fixture entity.\n"
    )
    (entity_dir / "_summary.md").write_text(summary)


def test_research_returns_exact_candidate(tmp_path):
    kg_root = tmp_path / "knowledge_graph"
    _write_entity(
        kg_root,
        "customers/key/universal_robots",
        "Universal Robots",
        ["Universal Robots", "UR"],
    )
    _write_entity(
        kg_root,
        "suppliers/champion_technologies",
        "Champion Technologies",
        ["Champion"],
    )

    researcher = EntityResearcher(kg_root)
    candidates = researcher.research("Universal Robots", aliases=["UR"])

    assert candidates
    best = candidates[0]
    assert best.candidate_path == "customers/key/universal_robots"
    assert best.match_score >= EntityResearcher.UPDATE_THRESHOLD


def test_suggest_action_create_when_no_match(tmp_path):
    kg_root = tmp_path / "knowledge_graph"
    _write_entity(kg_root, "customers/key/alcon", "Alcon", ["Alcon", "Alcon Research"])

    researcher = EntityResearcher(kg_root)
    action, target_path, confidence = researcher.suggest_action("Completely New Co")

    assert action == "create"
    assert target_path is None
    assert confidence > 0


def test_research_cache_invalidates_after_write(tmp_path):
    kg_root = tmp_path / "knowledge_graph"
    researcher = EntityResearcher(kg_root)

    # Warm cache before entity exists.
    initial = researcher.research("Acme")
    assert initial == []

    _write_entity(
        kg_root,
        "customers/key/acme",
        "Acme",
        ["Acme"],
    )
    researcher.invalidate()

    action, target_path, _ = researcher.suggest_action("Acme")
    assert action in ("update", "review")
    assert target_path == "customers/key/acme"
