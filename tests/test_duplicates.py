"""DUPLICATE: the same thing filed in two places, anywhere in the KB (0.16).

SIBLINGS compares names under one parent. What survived it on real KBs was
cross-folder: one supplier under two branches with different names, a topic
under two roots, a company under its full name and its acronym. These tests
pin each signal and, as importantly, the pairs that must never be reported.
"""

from pathlib import Path

from kvault.core import operations as ops
from kvault.core.check import run_checks
from kvault.core.duplicates import duplicate_pairs
from kvault.core.plan import build_plan

ROUTING = (
    "The routing model pools evidence across segments with a hierarchical prior, "
    "shrinks small segments toward the global mean, and feeds the explorer dashboard "
    "that ranks candidate policies by estimated uplift with credible intervals for "
    "each segment and a weekly refresh from the experiment warehouse tables."
)
PRICING = (
    "Quarterly pricing review for the distribution channel covers list price changes, "
    "freight surcharges, rebate tiers for the top accounts, and the approval path for "
    "exceptions, with the finance lead signing off before any customer sees a quote "
    "and the sales team logging every concession in the account record."
)


def _node(kb: Path, rel: str, body: str, aliases=(), extra: str = "") -> None:
    d = kb if rel == "." else kb / rel
    d.mkdir(parents=True, exist_ok=True)
    alias_yaml = "".join(f"\n- {a}" for a in aliases) if aliases else " []"
    (d / "_summary.md").write_text(
        f"---\nupdated: '2026-09-01'\nsource: manual\naliases:{alias_yaml}\n{extra}---\n" + body
    )


def _kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / ".kvault").mkdir()
    _node(kb, ".", "# Root\n\nRoot.\n")
    for rel in ("projects", "projects/causal", "tech", "tech/models", "sales", "suppliers"):
        _node(kb, rel, f"# {rel.split('/')[-1].title()}\n\nA branch.\n")
    return kb


def _pairs(kb: Path):
    return {(p["a"], p["b"]): p["signals"] for p in duplicate_pairs(kb)}


def test_twins_in_different_folders_are_found_by_name_title_and_body(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "tech/bayes_routing", f"# Bayes routing model\n\n{ROUTING}\n")
    _node(kb, "tech/models/bayes_routing", f"# Bayes routing model\n\n{ROUTING}\n")
    # different names, near-identical text
    _node(
        kb, "projects/uplift_modeling", f"# Uplift modeling\n\n{ROUTING} Owned by data science.\n"
    )
    _node(kb, "projects/causal/causal_uplift_explorer", f"# Explorer\n\n{ROUTING}\n")
    pairs = _pairs(kb)
    assert set(pairs[("tech/bayes_routing", "tech/models/bayes_routing")]) == {
        "similar_body",
        "same_title",
        "same_name",
    }
    assert (
        "similar_body"
        in pairs[("projects/causal/causal_uplift_explorer", "projects/uplift_modeling")]
    )


def test_alias_evidence_needs_more_than_one_shared_name(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "suppliers/northwind", "# Northwind\n\nLinings.\n", aliases=["Northwind", "NWT"])
    _node(kb, "sales/nwt", "# NWT\n\nProspect.\n", aliases=["Northwind", "NWT"])
    _node(kb, "suppliers/acme", "# Acme\n\nParts.\n", aliases=["Acme Corp"])
    _node(kb, "sales/acme_prospect", "# Acme prospect\n\nLead.\n", aliases=["Acme Corp"])
    _node(kb, "sales/jane", "# Jane\n\nBuyer.\n", aliases=["jane@example.com"])
    _node(kb, "suppliers/jane_doe", "# Jane Doe\n\nContact.\n", aliases=["jane@example.com"])
    pairs = _pairs(kb)
    assert pairs[("sales/nwt", "suppliers/northwind")] == ["shared_alias"]  # two shared aliases
    assert ("sales/jane", "suppliers/jane_doe") in pairs  # a shared email is an identifier
    assert ("sales/acme_prospect", "suppliers/acme") not in pairs  # one shared name only


def test_dated_records_about_a_subject_are_not_its_duplicate(tmp_path):
    kb = _kb(tmp_path)
    names = ["Acme Clutch", "ACL"]
    _node(kb, "sales/acme_clutch", "# Acme Clutch\n\nCustomer.\n", aliases=names)
    _node(
        kb, "suppliers/acl_sample_review_2026_05_13", "# Sample review\n\nNotes.\n", aliases=names
    )
    assert _pairs(kb) == {}


def test_rollups_series_stubs_and_reserved_dirs_are_never_compared(tmp_path):
    kb = _kb(tmp_path)
    # a parent repeating its child's text is a rollup doing its job
    _node(kb, "sales/pricing", f"# Pricing\n\n{PRICING}\n")
    _node(kb, "sales/pricing/q3_review", f"# Q3 pricing review\n\n{PRICING}\n")
    # members of one date series belong to SERIES
    _node(kb, "sales/sweep_2026_05_09", f"# Sweep\n\n{ROUTING}\n")
    _node(kb, "sales/sweep_2026_05_10", f"# Sweep\n\n{ROUTING}\n")
    # kvault's own stubs all say the same thing
    for rel in ("projects/alpha", "tech/beta"):
        (kb / rel).mkdir(parents=True)
        (kb / rel / "_summary.md").write_text(
            "---\nsource: kvault-stub\naliases: []\n---\n# Placeholder title here\n\n"
            + PRICING
            + "\n"
        )
    # background material and history
    _node(kb, "tech/deep_context/pricing_notes", f"# Pricing notes\n\n{PRICING}\n")
    assert _pairs(kb) == {}


def test_homonyms_are_not_duplicates_but_bare_twins_are(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "sales/standard", "# Standard customers\n\nTier.\n")
    _node(kb, "suppliers/standard", "# Standard freight suppliers\n\nTier.\n")
    # one name at two depths with the same bare title: the split-brain case
    _node(kb, "projects/causal/models", "# Models\n\nNotes.\n")
    # one name at one depth under sibling parents is a facet layout (0.15), not a twin
    _node(kb, "projects/models", "# Models\n\nNotes.\n")
    pairs = _pairs(kb)
    assert ("sales/standard", "suppliers/standard") not in pairs
    assert pairs[("projects/causal/models", "tech/models")] == ["same_name"]
    assert ("projects/models", "tech/models") not in pairs


def test_a_title_shared_by_many_nodes_is_a_pattern(tmp_path):
    kb = _kb(tmp_path)
    for rel in ("projects/a1", "projects/b2", "tech/c3", "tech/d4", "sales/e5", "sales/f6"):
        _node(kb, rel, "# Weekly status\n\nShort.\n")
    assert _pairs(kb) == {}


def test_distinct_from_and_check_and_plan(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "tech/bayes_routing", f"# Bayes routing model\n\n{ROUTING}\n")
    _node(kb, "projects/routing_notes", f"# Routing notes\n\n{ROUTING}\n")
    doc = run_checks(kb, codes=["DUPLICATE"])
    (finding,) = doc["findings"]
    assert finding["path"] == "projects/routing_notes"
    assert finding["message"].startswith("and tech/bayes_routing: body ")
    assert "% alike" in finding["message"]
    assert "deep_context/bayes_routing" in finding["fix"]

    items = [i for i in build_plan(kb, limit=0)["items"] if i["kind"] == "duplicate"]
    assert len(items) == 1 and items[0]["other"] == "tech/bayes_routing"
    assert any("deep_context/bayes_routing" in c for c in items[0]["commands"])
    scoped = build_plan(kb, path="tech", limit=0)["items"]
    assert [i["kind"] for i in scoped if i["kind"] == "duplicate"] == ["duplicate"]

    ops.mark_node(kb, "tech/bayes_routing", distinct_from=["projects/routing_notes"])
    assert run_checks(kb, codes=["DUPLICATE"])["findings"] == []
