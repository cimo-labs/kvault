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
    "shrinks small segments toward the global mean, and feeds the segment dashboard "
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
    _node(kb, "projects/causal/causal_uplift_dashboard", f"# Dashboard\n\n{ROUTING}\n")
    pairs = _pairs(kb)
    assert set(pairs[("tech/bayes_routing", "tech/models/bayes_routing")]) == {
        "similar_body",
        "same_title",
        "same_name",
    }
    assert (
        "similar_body"
        in pairs[("projects/causal/causal_uplift_dashboard", "projects/uplift_modeling")]
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


# ── review round (2026-09-26) ─────────────────────────────────────────────


def test_twins_named_like_a_date_are_still_twins(tmp_path):
    """same_series(x, x) is true for any name with a date-like token (phase_2,
    late_payment_policy); identical names are one member, not a series."""
    kb = _kb(tmp_path)
    for rel in ("sales/late_payment_policy", "suppliers/late_payment_policy"):
        _node(kb, rel, f"# Late payment policy\n\n{PRICING}\n")
    assert ("sales/late_payment_policy", "suppliers/late_payment_policy") in _pairs(kb)


def test_ghosts_and_stubs_never_pair(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "sales/pricing", f"# Pricing\n\n{PRICING}\n")
    (kb / "projects" / "pricing").mkdir()  # a ghost: no summary
    (kb / "tech" / "pricing").mkdir()
    (kb / "tech" / "pricing" / "_summary.md").write_text(
        "---\nsource: kvault-stub\naliases: []\n---\n# Pricing\n\nPlaceholder summary.\n"
    )
    assert _pairs(kb) == {}


def test_distinct_command_for_a_root_category_resolves(tmp_path):
    from kvault.cli.main import cli
    from click.testing import CliRunner

    kb = _kb(tmp_path)
    _node(kb, "people", "# People\n\nEveryone.\n")
    _node(kb, "sales/people", "# People\n\nThe sales team.\n")
    (finding,) = run_checks(kb, codes=["DUPLICATE"])["findings"]
    command = finding["fix"].split("different things → ")[1]
    assert command == "kvault mark people --distinct-from sales/people"
    args = command.split()[1:] + ["--kb-root", str(kb)]
    assert CliRunner().invoke(cli, args).exit_code == 0
    assert run_checks(kb, codes=["DUPLICATE"])["findings"] == []
    selfref = ops.mark_node(kb, "sales/people", distinct_from=["people"])
    assert selfref["success"] is False and "itself" in selfref["error"]


def test_dates_and_addresses_are_not_phone_numbers(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "sales/a_note", "# Note A\n\nOne.\n", aliases=["2026-09-26"])
    _node(kb, "suppliers/b_note", "# Note B\n\nTwo.\n", aliases=["2026-09-26"])
    _node(kb, "sales/host", "# Host\n\nOne.\n", aliases=["192.168.1.100"])
    _node(kb, "suppliers/box", "# Box\n\nTwo.\n", aliases=["192.168.1.100"])
    _node(kb, "sales/ann", "# Ann\n\nBuyer.\n", aliases=["+1 (415) 555-0100"])
    _node(kb, "suppliers/ann_lee", "# Ann Lee\n\nRep.\n", aliases=["+1 415 555 0100"])
    assert set(_pairs(kb)) == {("sales/ann", "suppliers/ann_lee")}


def test_notes_named_by_a_date_are_not_twins(tmp_path):
    """Two notes from the same day in two folders share a date, not a subject."""
    kb = _kb(tmp_path)
    _node(kb, "projects/2026_05_13", "# 2026-05-13\n\nStandup.\n")
    _node(kb, "sales/2026_05_13", "# 2026-05-13\n\nCall notes.\n")
    assert _pairs(kb) == {}


def test_a_symlink_never_hides_the_real_node(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "sales/acme", "# Acme\n\nCustomer.\n", aliases=["Acme Corp", "ACME"])
    _node(kb, "suppliers/acme_supply", "# Acme supply\n\nVendor.\n", aliases=["Acme Corp", "ACME"])
    (kb / "projects" / "a_link").symlink_to(kb / "sales" / "acme", target_is_directory=True)
    assert set(_pairs(kb)) == {("sales/acme", "suppliers/acme_supply")}


def test_toll_free_numbers_are_identifiers_not_dates():
    from kvault.core.duplicates import _identifier

    assert _identifier("0120-12-3456") == "0120123456"
    assert _identifier("2026-09-26") is None


# ── 0.16.1: from the first run on a ~1,000-node KB ───────────────────────

PARAPHRASE_A = (
    "The widget pricing service computes regional discounts, applies partner rebates, "
    "caps promotional stacking, and publishes nightly price files for the storefront team "
    "after the finance review, keeping an audit trail of every override and exception. "
    "Escalations go to the pricing lead, who approves emergency changes within a day."
)
PARAPHRASE_B = (
    "Nightly, the widget pricing service publishes price files for the storefront team: it "
    "computes regional discounts, applies partner rebates and caps promotional stacking, "
    "with finance review first and an audit trail of each override or exception. The "
    "pricing lead approves emergency changes, usually within a day, and handles escalations."
)


def test_every_pair_reports_measured_overlap(tmp_path):
    """0.16.0 reported jaccard/containment 0.00 unless they cleared the bar;
    paraphrased copies share their words, not their 5-word shingles."""
    kb = _kb(tmp_path)
    _node(kb, "sales/widget_pricing", f"# Widget pricing\n\n{PARAPHRASE_A}\n")
    _node(kb, "projects/causal/widget_pricing", f"# Widget pricing\n\n{PARAPHRASE_B}\n")
    (pair,) = duplicate_pairs(kb)
    assert pair["words"] >= 0.8 and pair["jaccard"] is not None and pair["jaccard"] < 0.5
    from kvault.core.duplicates import describe

    assert "% of words shared" in describe(pair)
    _node(kb, "tech/models/tiny", "# Tiny\n\nShort.\n")  # two depths: not a facet layout
    _node(kb, "sales/tiny", "# Tiny\n\nShort.\n")
    tiny = [p for p in duplicate_pairs(kb) if p["a"].endswith("tiny")][0]
    assert tiny["words"] is None and tiny["jaccard"] is None  # too short to measure, not 0.00


def test_filler_title_words_do_not_make_twins_look_different(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "projects/northwind", "# Northwind Project\n\nThe account.\n")
    _node(kb, "tech/models/northwind", "# Northwind Architecture\n\nThe account.\n")
    _node(kb, "sales/northwind", "# Northwind — Category Summary\n\nThe account.\n")
    pairs = _pairs(kb)
    assert ("projects/northwind", "tech/models/northwind") in pairs
    assert ("sales/northwind", "tech/models/northwind") in pairs


def test_a_filler_word_tells_nodes_apart_when_the_texts_differ(tmp_path):
    kb = _kb(tmp_path)
    # one name, titles that differ only by a filler word, unrelated texts
    _node(kb, "projects/search", f"# Search Project\n\n{PRICING}\n")
    _node(kb, "tech/models/search", f"# Search Architecture\n\n{ROUTING}\n")
    assert ("projects/search", "tech/models/search") not in _pairs(kb)
    # the same titles over one text are twins again
    _node(kb, "projects/search", f"# Search Project\n\n{PARAPHRASE_A}\n")
    _node(kb, "tech/models/search", f"# Search Architecture\n\n{PARAPHRASE_B}\n")
    assert "same_name" in _pairs(kb)[("projects/search", "tech/models/search")]


def test_titles_still_match_exactly_filler_words_included(tmp_path):
    kb = _kb(tmp_path)
    _node(kb, "tech/billing_system", "# Billing System\n\nInvoices.\n")
    _node(kb, "sales/billing", "# Billing System\n\nInvoices.\n")
    # titles that differ only by a filler word are not the same title
    _node(kb, "projects/atlas_search", "# Atlas Search Project\n\nStaffing.\n")
    _node(kb, "tech/search_design", "# Atlas Search Architecture\n\nClusters.\n")
    assert _pairs(kb) == {("sales/billing", "tech/billing_system"): ["same_title"]}


def test_twins_in_a_facet_layout_pair_by_title(tmp_path):
    """One supplier under two root categories: the title decides, as in
    0.16.0, whether the texts are short or one is a card beside a page."""
    kb = _kb(tmp_path)
    _node(kb, "suppliers/acme_tooling", "# Acme Tooling\n\nMolds and dies.\n")
    _node(kb, "sales/acme_tooling", "# Acme Tooling\n\nMolds and dies.\n")
    assert _pairs(kb)[("sales/acme_tooling", "suppliers/acme_tooling")] == ["same_title"]
    # a 23-word card whose words all come from a 59-word page
    card = " ".join(PRICING.split()[:30])
    _node(kb, "sales/acme_tooling", f"# Acme Tooling\n\n{card}\n")
    _node(kb, "suppliers/acme_tooling", f"# Acme Tooling\n\n{PRICING} {ROUTING}\n")
    (pair,) = duplicate_pairs(kb)
    assert pair["signals"] == ["same_title"] and pair["words"] == 1.0


def test_titles_with_the_same_words_in_another_order_are_both_shown(tmp_path):
    from kvault.core.duplicates import describe

    kb = _kb(tmp_path)
    _node(kb, "sales/acme_tooling", "# Acme Tooling\n\nShort.\n")
    _node(kb, "projects/causal/tooling_acme", "# Tooling, Acme\n\nShort.\n")
    (pair,) = duplicate_pairs(kb)
    text = describe(pair)
    assert "«Acme Tooling»" in text and "«Tooling, Acme»" in text
