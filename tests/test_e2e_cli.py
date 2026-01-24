"""End-to-end tests for kgraph CLI.

Covers processing a small corpus, writing entities, updating the index,
and querying logs. Focuses on realistic flows over isolated units.
"""

import json
from pathlib import Path

from click.testing import CliRunner

from kgraph.cli.main import cli
from kgraph.core.index import EntityIndex


def _write(p: Path, content: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_process_dry_run_and_apply(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    kg_root = tmp_path / "kg"

    _write(corpus / "email1.txt", "Reach me at john.smith@acmecorp.com for quotes.")
    _write(corpus / "email2.md", "Please contact support@globaltech.io for info.")

    runner = CliRunner()

    # Dry run: should not create entity folders
    res_dry = runner.invoke(
        cli,
        [
            "process",
            "--corpus",
            str(corpus),
            "--kg-root",
            str(kg_root),
            "--dry-run",
        ],
        catch_exceptions=False,
    )
    assert res_dry.exit_code == 0, res_dry.output
    plan = json.loads(res_dry.output)
    assert plan["files_processed"] == 2
    assert any(op["type"] == "person" for op in plan["planned_ops"])  # extracted person
    assert any(op["type"] == "org" for op in plan["planned_ops"])     # extracted org

    # No entities yet
    assert not (kg_root / "people").exists()
    assert not (kg_root / "orgs").exists()

    # Apply: should create entities and update index/logs
    res_apply = runner.invoke(
        cli,
        [
            "process",
            "--corpus",
            str(corpus),
            "--kg-root",
            str(kg_root),
            "--apply",
        ],
        catch_exceptions=False,
    )
    assert res_apply.exit_code == 0, res_apply.output
    plan2 = json.loads(res_apply.output)
    session_id = plan2["session"]

    # Entities exist
    # Expect a person for John Smith and orgs for acmecorp and globaltech
    assert (kg_root / "people" / "john_smith" / "_meta.json").exists()
    assert (kg_root / "orgs" / "acmecorp" / "_meta.json").exists()
    assert (kg_root / "orgs" / "globaltech" / "_meta.json").exists()

    # Index contains alias lookup for the person email
    index_db = kg_root / ".kgraph" / "index.db"
    index = EntityIndex(index_db)
    entry = index.find_by_alias("john.smith@acmecorp.com")
    assert entry is not None
    assert entry.path == "people/john_smith"

    # Logs summary for the process session
    res_log = runner.invoke(
        cli,
        [
            "log",
            "summary",
            "--db",
            str(kg_root / ".kgraph" / "logs.db"),
            "--session",
            session_id,
        ],
        catch_exceptions=False,
    )
    assert res_log.exit_code == 0, res_log.output
    summary = json.loads(res_log.output)
    assert summary["phase_counts"].get("input", 0) >= 1
    assert summary["phase_counts"].get("decide", 0) >= 1
    assert summary["phase_counts"].get("write", 0) >= 1


def test_review_threshold_suggests_review_for_similar_org(tmp_path: Path) -> None:
    # First, create a baseline KG by applying a small corpus
    base_corpus = tmp_path / "corpus_base"
    kg_root = tmp_path / "kg"
    _write(base_corpus / "a.txt", "Contact john.smith@acmecorp.com")

    runner = CliRunner()
    res_apply = runner.invoke(
        cli,
        [
            "process",
            "--corpus",
            str(base_corpus),
            "--kg-root",
            str(kg_root),
            "--apply",
        ],
        catch_exceptions=False,
    )
    assert res_apply.exit_code == 0, res_apply.output

    # Now a new corpus with a similar but not identical org domain
    # "acmeco.com" should be fuzzy-similar to "acmecorp" name but not exact
    new_corpus = tmp_path / "corpus_new"
    _write(new_corpus / "b.txt", "Please email contact@acmeco.com")

    res_dry = runner.invoke(
        cli,
        [
            "process",
            "--corpus",
            str(new_corpus),
            "--kg-root",
            str(kg_root),
            "--dry-run",
        ],
        catch_exceptions=False,
    )
    assert res_dry.exit_code == 0, res_dry.output
    plan = json.loads(res_dry.output)

    # Expect at least one org operation marked as review (ambiguous match)
    assert any(op["type"] == "org" and op["action"] == "review" for op in plan["planned_ops"])


def test_second_run_updates_existing_entity_sources(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    kg_root = tmp_path / "kg"

    _write(corpus / "first.txt", "Reach me at john.smith@acmecorp.com")

    runner = CliRunner()
    # Apply first time
    res1 = runner.invoke(
        cli,
        [
            "process",
            "--corpus",
            str(corpus),
            "--kg-root",
            str(kg_root),
            "--apply",
        ],
        catch_exceptions=False,
    )
    assert res1.exit_code == 0, res1.output

    # Apply a second time with another file referencing the same email
    _write(corpus / "second.txt", "Follow up: john.smith@acmecorp.com")
    res2 = runner.invoke(
        cli,
        [
            "process",
            "--corpus",
            str(corpus),
            "--kg-root",
            str(kg_root),
            "--apply",
        ],
        catch_exceptions=False,
    )
    assert res2.exit_code == 0, res2.output

    # Verify the existing person entity has at least two sources recorded
    meta_path = kg_root / "people" / "john_smith" / "_meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert len(meta.get("sources", [])) >= 2

