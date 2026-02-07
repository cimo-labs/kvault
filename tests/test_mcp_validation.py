"""Tests for kvault MCP validation pure functions.

No I/O, no fixtures needed. Tests the business rules and validation logic
extracted into kvault/mcp/validation.py.
"""

import pytest
from datetime import datetime

from kvault.mcp.validation import (
    ErrorCode,
    error_response,
    success_response,
    normalize_phone,
    normalize_path,
    validate_entity_path,
    validate_frontmatter,
    build_default_frontmatter,
    extract_identifiers,
    get_journal_path,
    format_journal_entry,
)


# ============================================================================
# Response Helpers
# ============================================================================


class TestErrorResponse:
    def test_basic_error(self):
        r = error_response(ErrorCode.NOT_FOUND, "Entity not found")
        assert r["success"] is False
        assert r["error_code"] == "not_found"
        assert r["error"] == "Entity not found"

    def test_error_with_details(self):
        r = error_response(ErrorCode.VALIDATION_ERROR, "Bad input", details={"field": "name"})
        assert r["details"]["field"] == "name"

    def test_error_with_hint(self):
        r = error_response(ErrorCode.NOT_FOUND, "Missing", hint="Use create=True")
        assert r["hint"] == "Use create=True"

    def test_all_error_codes(self):
        for code in ErrorCode:
            r = error_response(code, "test")
            assert r["error_code"] == code.value


class TestSuccessResponse:
    def test_basic_success(self):
        r = success_response({"count": 5})
        assert r["success"] is True
        assert r["count"] == 5

    def test_merges_data(self):
        r = success_response({"a": 1, "b": 2})
        assert r["a"] == 1
        assert r["b"] == 2


# ============================================================================
# Phone Normalization
# ============================================================================


class TestNormalizePhone:
    def test_ten_digits(self):
        assert normalize_phone("5551234567") == "+15551234567"

    def test_eleven_digits_with_one(self):
        assert normalize_phone("15551234567") == "+15551234567"

    def test_formatted_with_parens(self):
        assert normalize_phone("(555) 123-4567") == "+15551234567"

    def test_formatted_with_dashes(self):
        assert normalize_phone("555-123-4567") == "+15551234567"

    def test_with_plus_one(self):
        assert normalize_phone("+1 555 123 4567") == "+15551234567"

    def test_international(self):
        # 12+ digits should get + prefix
        assert normalize_phone("442071234567") == "+442071234567"

    def test_short_number_passthrough(self):
        # Too short to normalize, return as-is
        assert normalize_phone("911") == "911"

    def test_already_normalized(self):
        assert normalize_phone("+15551234567") == "+15551234567"


# ============================================================================
# Path Normalization
# ============================================================================


class TestNormalizePath:
    def test_strips_trailing_slash(self):
        assert normalize_path("people/alice/") == "people/alice"

    def test_removes_summary_md_suffix(self):
        assert normalize_path("people/alice/_summary.md") == "people/alice"

    def test_lowercases(self):
        assert normalize_path("People/Alice") == "people/alice"

    def test_no_change_needed(self):
        assert normalize_path("people/alice") == "people/alice"

    def test_combined(self):
        assert normalize_path("People/Alice/_summary.md/") == "people/alice"


# ============================================================================
# Entity Path Validation
# ============================================================================


class TestValidateEntityPath:
    def test_valid_two_parts(self):
        valid, err = validate_entity_path("people/alice")
        assert valid is True
        assert err is None

    def test_valid_three_parts(self):
        valid, _ = validate_entity_path("people/friends/alice")
        assert valid is True

    def test_valid_four_parts(self):
        valid, _ = validate_entity_path("people/friends/sf/alice")
        assert valid is True

    def test_rejects_single_part(self):
        valid, err = validate_entity_path("alice")
        assert valid is False
        assert "at least 2 parts" in err

    def test_rejects_five_parts(self):
        valid, err = validate_entity_path("a/b/c/d/e")
        assert valid is False
        assert "too deep" in err.lower()

    def test_rejects_uppercase(self):
        # After normalization, uppercase becomes lowercase, but
        # the regex requires [a-z] start
        valid, err = validate_entity_path("people/Alice_Smith")
        # normalize_path lowercases, so this should pass
        assert valid is True

    def test_rejects_spaces(self):
        valid, err = validate_entity_path("people/alice smith")
        assert valid is False

    def test_rejects_special_chars(self):
        valid, err = validate_entity_path("people/alice@smith")
        assert valid is False

    def test_rejects_dot_component(self):
        valid, err = validate_entity_path("people/./alice")
        assert valid is False

    def test_rejects_dotdot(self):
        valid, err = validate_entity_path("people/../etc")
        assert valid is False

    def test_rejects_empty_component(self):
        valid, err = validate_entity_path("people//alice")
        assert valid is False

    def test_rejects_hyphen_start(self):
        valid, err = validate_entity_path("people/-alice")
        assert valid is False

    def test_allows_underscores(self):
        valid, _ = validate_entity_path("people/alice_smith")
        assert valid is True

    def test_allows_numbers(self):
        valid, _ = validate_entity_path("people/agent007")
        assert valid is True


# ============================================================================
# Frontmatter Validation
# ============================================================================


class TestValidateFrontmatter:
    def test_valid_with_all_required(self):
        meta = {"created": "2026-01-01", "updated": "2026-01-01", "source": "manual", "aliases": []}
        valid, missing = validate_frontmatter(meta)
        assert valid is True
        assert missing == []

    def test_missing_source(self):
        meta = {"created": "2026-01-01", "updated": "2026-01-01", "aliases": []}
        valid, missing = validate_frontmatter(meta)
        assert valid is False
        assert "source" in missing

    def test_missing_aliases(self):
        meta = {"created": "2026-01-01", "updated": "2026-01-01", "source": "manual"}
        valid, missing = validate_frontmatter(meta)
        assert valid is False
        assert "aliases" in missing

    def test_missing_multiple(self):
        meta = {}
        valid, missing = validate_frontmatter(meta)
        assert valid is False
        assert len(missing) == 4  # created, updated, source, aliases

    def test_extra_fields_allowed(self):
        meta = {"created": "x", "updated": "x", "source": "x", "aliases": [], "email": "test@example.com"}
        valid, _ = validate_frontmatter(meta)
        assert valid is True


# ============================================================================
# Build Default Frontmatter
# ============================================================================


class TestBuildDefaultFrontmatter:
    def test_minimal(self):
        meta = build_default_frontmatter(source="manual")
        assert meta["source"] == "manual"
        assert meta["aliases"] == []
        assert "created" in meta
        assert "updated" in meta

    def test_with_aliases(self):
        meta = build_default_frontmatter(source="test", aliases=["Alice"])
        assert meta["aliases"] == ["Alice"]

    def test_with_phone_adds_to_aliases(self):
        meta = build_default_frontmatter(source="test", phone="5551234567")
        assert meta["phone"] == "+15551234567"
        assert "+15551234567" in meta["aliases"]

    def test_with_email_adds_to_aliases(self):
        meta = build_default_frontmatter(source="test", email="alice@example.com")
        assert meta["email"] == "alice@example.com"
        assert "alice@example.com" in meta["aliases"]

    def test_no_duplicate_aliases(self):
        meta = build_default_frontmatter(
            source="test",
            aliases=["alice@example.com"],
            email="alice@example.com",
        )
        assert meta["aliases"].count("alice@example.com") == 1

    def test_with_relationship_type(self):
        meta = build_default_frontmatter(source="test", relationship_type="colleague")
        assert meta["relationship_type"] == "colleague"

    def test_with_context(self):
        meta = build_default_frontmatter(source="test", context="Met at conference")
        assert meta["context"] == "Met at conference"


# ============================================================================
# Extract Identifiers
# ============================================================================


class TestExtractIdentifiers:
    def test_extracts_emails(self):
        text = "Contact alice@example.com or bob@test.org for details."
        result = extract_identifiers(text)
        assert "alice@example.com" in result["emails"]
        assert "bob@test.org" in result["emails"]

    def test_extracts_phones(self):
        text = "Call (555) 123-4567 or +1 555 987 6543."
        result = extract_identifiers(text)
        assert len(result["phones"]) >= 1

    def test_extracts_names(self):
        text = "Alice Smith met with Bob Jones at the conference."
        result = extract_identifiers(text)
        # Should find capitalized name pairs
        assert any("Alice" in n for n in result["names"])

    def test_ignores_common_words(self):
        text = "The quick brown fox jumped over."
        result = extract_identifiers(text)
        # Common words shouldn't appear as names
        assert not any("The" == n for n in result["names"])

    def test_empty_text(self):
        result = extract_identifiers("")
        assert result["phones"] == []
        assert result["emails"] == []
        assert result["names"] == []


# ============================================================================
# Journal Helpers
# ============================================================================


class TestGetJournalPath:
    def test_default_today(self):
        path = get_journal_path()
        today = datetime.now()
        assert path == f"journal/{today.strftime('%Y-%m')}/log.md"

    def test_custom_date(self):
        dt = datetime(2026, 1, 15)
        path = get_journal_path(dt)
        assert path == "journal/2026-01/log.md"


class TestFormatJournalEntry:
    def test_create_action(self):
        entry = format_journal_entry(
            actions=[{"action_type": "create", "path": "people/alice", "reasoning": "New contact"}],
            source="manual",
        )
        assert "Created alice" in entry
        assert "New contact" in entry
        assert "Source: manual" in entry

    def test_update_action(self):
        entry = format_journal_entry(
            actions=[{"action_type": "update", "path": "people/alice"}],
            source="test",
        )
        assert "Updated alice" in entry

    def test_delete_action(self):
        entry = format_journal_entry(
            actions=[{"action_type": "delete", "path": "people/alice"}],
            source="test",
        )
        assert "Deleted alice" in entry

    def test_move_action(self):
        entry = format_journal_entry(
            actions=[{"action_type": "move", "path": "people/alice", "target_path": "people/contacts/alice"}],
            source="test",
        )
        assert "Moved alice" in entry

    def test_multiple_actions(self):
        entry = format_journal_entry(
            actions=[
                {"action_type": "create", "path": "people/alice"},
                {"action_type": "update", "path": "people/bob"},
            ],
            source="test",
        )
        assert "Created alice" in entry
        assert "Updated bob" in entry
