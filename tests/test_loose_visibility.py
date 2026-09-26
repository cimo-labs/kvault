"""Loose files are visible to search and tree (0.16).

check has reported LOOSE: files since 0.15, but search and tree said nothing:
on a real ~1,000-node KB, 43 Markdown files (legacy node files among them)
were silently missing from every search.
"""

from pathlib import Path

from kvault.core import operations as ops
from kvault.core.structure import IGNORE_FILE


def _node(kb: Path, rel: str, body: str = "# Node\n\nA node.\n") -> None:
    d = kb if rel == "." else kb / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "_summary.md").write_text("---\nsource: manual\naliases: []\n---\n" + body)


def _kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".kvault").mkdir()
    _node(kb, ".", "# Root\n\nRoot.\n")
    _node(kb, "projects", "# Projects\n\nUplift projects.\n")
    _node(kb, "projects/deep_context", "# Background\n\nNotes.\n")
    (kb / "memo.md").write_text("# Memo\n\nThe uplift memo.\n")
    (kb / "projects" / "report.md").write_text(
        "---\nsource: manual\n---\n# Report\n\nUplift analysis report.\n"
    )
    (kb / "projects" / "draft.md").write_text("# Draft\n\nUnrelated.\n")
    (kb / "projects" / "chart.png").write_bytes(
        b"x"
    )  # not Markdown: tree counts it, search does not
    (kb / "_index.md").write_text("internal")  # kvault's own files are skipped
    (kb / "projects" / "deep_context" / "loose.md").write_text("background material")
    (kb / "scripts").mkdir()
    (kb / "scripts" / "readme.md").write_text("uplift tooling")
    (kb / IGNORE_FILE).write_text("scripts\n")
    return kb


def _not_indexed(result):
    return [
        n for n in result.get("notes", []) if (n.get("detail") or {}).get("kind") == "not_indexed"
    ]


def test_search_reports_markdown_it_cannot_see(tmp_path):
    kb = _kb(tmp_path)
    (note,) = _not_indexed(ops.search_nodes(kb, "uplift"))
    assert note["code"] == "truncated"
    assert note["detail"]["loose_markdown"] == 3
    assert note["detail"]["matching"] == ["memo.md", "projects/report.md"]
    assert "2 contain the query" in note["text"]

    (quiet,) = _not_indexed(ops.search_nodes(kb, "zebra"))
    assert quiet["detail"]["matching"] == [] and "contain" not in quiet["text"]

    (scoped,) = _not_indexed(ops.search_nodes(kb, "uplift", path_prefix="projects"))
    assert scoped["detail"]["loose_markdown"] == 2


def test_search_is_silent_when_nothing_is_loose(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    _node(kb, ".", "# Root\n\nRoot about uplift.\n")
    assert _not_indexed(ops.search_nodes(kb, "uplift")) == []


def test_tree_counts_loose_files_beside_ghosts(tmp_path):
    kb = _kb(tmp_path)
    outline = ops.build_outline(kb)
    assert outline["loose_count"] == 1  # memo.md; _index.md and the ignore file are not loose
    projects = [c for c in outline["children"] if c["slug"] == "projects"][0]
    assert projects["loose_count"] == 3  # report.md, draft.md, chart.png
    background = [c for c in projects["children"] if c["slug"] == "deep_context"][0]
    assert background["loose_count"] == 0  # background material belongs there
    text = ops.render_outline_text(outline)
    assert "+1 loose" in text and "+3 loose" in text
