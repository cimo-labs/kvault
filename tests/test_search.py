"""Tests for kvault.core.search — filesystem-based entity search.

No SQLite, no index. Just reads files and scores matches.
"""

import pytest
from pathlib import Path

from kvault.core.search import (
    scan_entities,
    search,
    find_by_alias,
    find_by_email_domain,
    count_entities,
    list_entities,
    _normalize,
    _is_email,
    _is_domain_query,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_kb(tmp_path):
    """Create a minimal KB for testing."""
    kb = tmp_path / "kb"
    kb.mkdir()
    # Root summary (should be skipped — depth < 2)
    (kb / "_summary.md").write_text("---\naliases: []\n---\n# Root\n")

    # Category summary (should be skipped — depth < 2)
    (kb / "people").mkdir()
    (kb / "people" / "_summary.md").write_text("---\naliases: []\n---\n# People\n")

    # Entity: Alice
    alice = kb / "people" / "friends" / "alice_smith"
    alice.mkdir(parents=True)
    (alice / "_summary.md").write_text(
        "---\naliases:\n  - Alice Smith\n  - alice@acme.com\n  - Ali\n---\n\n"
        "# Alice Smith\n\nWorks at Acme Corp. Previously at Anthropic.\n"
    )

    # Entity: Bob
    bob = kb / "people" / "work" / "bob_jones"
    bob.mkdir(parents=True)
    (bob / "_summary.md").write_text(
        "---\naliases:\n  - Bob Jones\n  - bob@bigcorp.com\n  - Bobby\n---\n\n"
        "# Bob Jones\n\nSenior engineer at BigCorp.\n"
    )

    # Entity: José (accented)
    jose = kb / "people" / "friends" / "jose_garcia"
    jose.mkdir(parents=True)
    (jose / "_summary.md").write_text(
        "---\naliases:\n  - José García\n  - Jose Garcia\n  - jose@startup.io\n---\n\n"
        "# José García\n\nFounder of StartupIO.\n"
    )

    # Entity: kvault (project)
    proj = kb / "projects" / "active" / "kvault"
    proj.mkdir(parents=True)
    (proj / "_summary.md").write_text(
        "---\naliases:\n  - kvault\n  - knowledgevault\ntopic: kvault\n---\n\n"
        "# kvault\n\nPersonal knowledge base for AI agents.\n"
    )

    # Entity with phone number alias
    charlie = kb / "people" / "work" / "charlie_day"
    charlie.mkdir(parents=True)
    (charlie / "_summary.md").write_text(
        "---\naliases:\n  - Charlie Day\n  - 5551234567\nphone: 5551234567\nemail: charlie@company.com\n---\n\n"
        "# Charlie Day\n\nProduct manager at Company Inc.\n"
    )

    return kb


@pytest.fixture
def entities(sample_kb):
    """Pre-scanned entities for the sample KB."""
    return scan_entities(sample_kb)


# ---------------------------------------------------------------------------
# scan_entities
# ---------------------------------------------------------------------------

class TestScanEntities:
    def test_finds_all_entities(self, sample_kb):
        entities = scan_entities(sample_kb)
        assert len(entities) == 5

    def test_skips_root_summary(self, sample_kb):
        entities = scan_entities(sample_kb)
        paths = [e.path for e in entities]
        assert "." not in paths
        assert "" not in paths

    def test_skips_category_summary(self, sample_kb):
        entities = scan_entities(sample_kb)
        paths = [e.path for e in entities]
        assert "people" not in paths

    def test_extracts_name_from_aliases(self, sample_kb):
        entities = scan_entities(sample_kb)
        alice = next(e for e in entities if "alice" in e.path)
        assert alice.name == "Alice Smith"

    def test_extracts_name_from_topic(self, sample_kb):
        entities = scan_entities(sample_kb)
        proj = next(e for e in entities if "kvault" in e.path)
        assert proj.name == "kvault"

    def test_extracts_email_domains(self, sample_kb):
        entities = scan_entities(sample_kb)
        alice = next(e for e in entities if "alice" in e.path)
        assert "acme.com" in alice.email_domains

    def test_extracts_category(self, sample_kb):
        entities = scan_entities(sample_kb)
        alice = next(e for e in entities if "alice" in e.path)
        assert alice.category == "people"

    def test_coerces_phone_number_alias(self, sample_kb):
        entities = scan_entities(sample_kb)
        charlie = next(e for e in entities if "charlie" in e.path)
        assert "5551234567" in charlie.aliases

    def test_adds_email_field_to_aliases(self, sample_kb):
        entities = scan_entities(sample_kb)
        charlie = next(e for e in entities if "charlie" in e.path)
        assert "charlie@company.com" in charlie.aliases

    def test_skips_hidden_dirs(self, sample_kb):
        hidden = sample_kb / ".kvault" / "cache" / "thing"
        hidden.mkdir(parents=True)
        (hidden / "_summary.md").write_text("---\naliases: [hidden]\n---\nhidden\n")
        entities = scan_entities(sample_kb)
        assert not any(".kvault" in e.path for e in entities)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_lowercases(self):
        assert _normalize("Alice") == "alice"

    def test_strips_accents(self):
        assert _normalize("José") == "jose"

    def test_underscores_to_spaces(self):
        assert _normalize("alice_smith") == "alice smith"

    def test_collapses_whitespace(self):
        assert _normalize("alice   smith") == "alice smith"

    def test_preserves_email(self):
        assert _normalize("alice@acme.com") == "alice@acme.com"


class TestIsEmail:
    def test_real_email(self):
        assert _is_email("alice@acme.com") is True

    def test_domain_not_email(self):
        assert _is_email("@acme.com") is False

    def test_no_dot(self):
        assert _is_email("alice@localhost") is False

    def test_plain_text(self):
        assert _is_email("alice smith") is False


class TestIsDomainQuery:
    def test_at_domain(self):
        assert _is_domain_query("@acme.com") is True

    def test_bare_domain(self):
        assert _is_domain_query("acme.com") is True

    def test_not_domain(self):
        assert _is_domain_query("alice smith") is False

    def test_email_not_domain(self):
        assert _is_domain_query("alice@acme.com") is False


# ---------------------------------------------------------------------------
# Unified search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_name_match(self, sample_kb, entities):
        results = search(sample_kb, "Alice Smith", _entities=entities)
        assert len(results) >= 1
        assert results[0].name == "Alice Smith"

    def test_partial_name(self, sample_kb, entities):
        results = search(sample_kb, "Alice", _entities=entities)
        assert any(r.name == "Alice Smith" for r in results)

    def test_email_exact_match(self, sample_kb, entities):
        results = search(sample_kb, "alice@acme.com", _entities=entities)
        assert len(results) >= 1
        assert results[0].name == "Alice Smith"
        assert results[0].score == 1.0

    def test_domain_query_at_prefix(self, sample_kb, entities):
        results = search(sample_kb, "@acme.com", _entities=entities)
        assert len(results) >= 1
        assert results[0].name == "Alice Smith"
        assert results[0].score == 0.9

    def test_domain_query_bare(self, sample_kb, entities):
        results = search(sample_kb, "bigcorp.com", _entities=entities)
        assert len(results) >= 1
        assert results[0].name == "Bob Jones"

    def test_content_keyword(self, sample_kb, entities):
        results = search(sample_kb, "Anthropic", _entities=entities)
        assert any(r.name == "Alice Smith" for r in results)

    def test_accent_insensitive(self, sample_kb, entities):
        results = search(sample_kb, "Jose", _entities=entities)
        assert any("jose" in r.path or "garcia" in r.path for r in results)

    def test_category_filter(self, sample_kb, entities):
        results = search(sample_kb, "kvault", category="projects", _entities=entities)
        assert len(results) >= 1
        assert all(r.category == "projects" for r in results)

    def test_category_filter_excludes(self, sample_kb, entities):
        results = search(sample_kb, "Alice", category="projects", _entities=entities)
        assert not any(r.name == "Alice Smith" for r in results)

    def test_limit(self, sample_kb, entities):
        results = search(sample_kb, "a", limit=2, _entities=entities)
        assert len(results) <= 2

    def test_empty_query(self, sample_kb, entities):
        results = search(sample_kb, "", _entities=entities)
        assert results == []

    def test_no_match(self, sample_kb, entities):
        results = search(sample_kb, "zzzznonexistent", _entities=entities)
        assert results == []

    def test_alias_match(self, sample_kb, entities):
        results = search(sample_kb, "Bobby", _entities=entities)
        assert any(r.name == "Bob Jones" for r in results)

    def test_results_sorted_by_score(self, sample_kb, entities):
        results = search(sample_kb, "Alice", _entities=entities)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_generic_domain_in_email_search(self, sample_kb):
        """Email from generic domain (gmail) should still exact-match alias."""
        # Add entity with gmail alias
        gmail_user = sample_kb / "people" / "work" / "gmail_user"
        gmail_user.mkdir(parents=True)
        (gmail_user / "_summary.md").write_text(
            "---\naliases:\n  - Gmail User\n  - user@gmail.com\n---\nA gmail user.\n"
        )
        results = search(sample_kb, "user@gmail.com")
        assert len(results) >= 1
        assert results[0].score == 1.0  # exact alias match still works


# ---------------------------------------------------------------------------
# find_by_alias
# ---------------------------------------------------------------------------

class TestFindByAlias:
    def test_exact_match(self, sample_kb, entities):
        result = find_by_alias(sample_kb, "Alice Smith", _entities=entities)
        assert result is not None
        assert result.name == "Alice Smith"

    def test_case_insensitive(self, sample_kb, entities):
        result = find_by_alias(sample_kb, "alice smith", _entities=entities)
        assert result is not None

    def test_email_alias(self, sample_kb, entities):
        result = find_by_alias(sample_kb, "bob@bigcorp.com", _entities=entities)
        assert result is not None
        assert result.name == "Bob Jones"

    def test_nickname_alias(self, sample_kb, entities):
        result = find_by_alias(sample_kb, "Bobby", _entities=entities)
        assert result is not None
        assert result.name == "Bob Jones"

    def test_no_match(self, sample_kb, entities):
        result = find_by_alias(sample_kb, "Nobody Here", _entities=entities)
        assert result is None

    def test_accent_match(self, sample_kb, entities):
        result = find_by_alias(sample_kb, "José García", _entities=entities)
        assert result is not None

    def test_accent_stripped_match(self, sample_kb, entities):
        result = find_by_alias(sample_kb, "Jose Garcia", _entities=entities)
        assert result is not None


# ---------------------------------------------------------------------------
# find_by_email_domain
# ---------------------------------------------------------------------------

class TestFindByEmailDomain:
    def test_finds_by_domain(self, sample_kb, entities):
        results = find_by_email_domain(sample_kb, "acme.com", _entities=entities)
        assert len(results) >= 1
        assert any(r.name == "Alice Smith" for r in results)

    def test_no_match(self, sample_kb, entities):
        results = find_by_email_domain(sample_kb, "nonexistent.com", _entities=entities)
        assert results == []

    def test_case_insensitive(self, sample_kb, entities):
        results = find_by_email_domain(sample_kb, "ACME.COM", _entities=entities)
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# count_entities / list_entities
# ---------------------------------------------------------------------------

class TestCountAndList:
    def test_count_all(self, sample_kb, entities):
        assert count_entities(sample_kb, _entities=entities) == 5

    def test_count_by_category(self, sample_kb, entities):
        assert count_entities(sample_kb, category="people", _entities=entities) == 4
        assert count_entities(sample_kb, category="projects", _entities=entities) == 1

    def test_list_all(self, sample_kb, entities):
        results = list_entities(sample_kb, _entities=entities)
        assert len(results) == 5

    def test_list_sorted_by_name(self, sample_kb, entities):
        results = list_entities(sample_kb, _entities=entities)
        names = [r.name.lower() for r in results]
        assert names == sorted(names)

    def test_list_by_category(self, sample_kb, entities):
        results = list_entities(sample_kb, category="projects", _entities=entities)
        assert len(results) == 1
        assert results[0].name == "kvault"
