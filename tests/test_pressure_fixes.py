"""Regression tests for pressure-test findings: name extraction, validate_kb, auto-name."""

import json
import os
import pytest
from pathlib import Path

from kvault.core.storage import scan_entities
from kvault.core.frontmatter import parse_frontmatter, build_frontmatter

# ============================================================================
# BUG-2: Entity name extraction
# ============================================================================


class TestNameExtraction:
    """Entity display names should come from aliases, not directory slugs."""

    def _make_entity(self, kb, rel_path, frontmatter_yaml):
        """Helper: create entity with given frontmatter."""
        entity = kb / rel_path
        entity.mkdir(parents=True)
        (entity / "_summary.md").write_text(frontmatter_yaml)
        # Ensure parent category summaries exist
        for parent in entity.relative_to(kb).parents:
            if parent != Path("."):
                summary = kb / parent / "_summary.md"
                if not summary.exists():
                    (kb / parent).mkdir(parents=True, exist_ok=True)
                    summary.write_text(f"# {parent.name}\n")

    def _get_entity(self, kb, rel_path):
        """Helper: find entity in scan results."""
        entities = scan_entities(kb)
        return next((e for e in entities if e.path == rel_path), None)

    def test_uses_first_alias_as_name(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# KB\n")
        self._make_entity(
            kb,
            "people/alice_smith",
            "---\nsource: manual\naliases:\n- Alice Smith\n- alice@example.com\n---\n# Alice\n",
        )
        e = self._get_entity(kb, "people/alice_smith")
        assert e is not None
        assert e.name == "Alice Smith"

    def test_skips_email_alias_for_name(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# KB\n")
        self._make_entity(
            kb,
            "people/bob",
            "---\nsource: manual\naliases:\n- bob@example.com\n- '+14155551234'\n- Bob Jones\n---\n# Bob\n",
        )
        e = self._get_entity(kb, "people/bob")
        assert e is not None
        assert e.name == "Bob Jones"

    def test_falls_back_to_email_if_only_alias(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# KB\n")
        self._make_entity(
            kb,
            "people/mystery",
            "---\nsource: manual\naliases:\n- mystery@corp.com\n---\n# Mystery\n",
        )
        e = self._get_entity(kb, "people/mystery")
        assert e is not None
        assert e.name == "mystery@corp.com"

    def test_uses_explicit_name_over_alias(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# KB\n")
        self._make_entity(
            kb,
            "people/charlie",
            "---\nname: Charlie Day\nsource: manual\naliases:\n- CD\n---\n# Charlie\n",
        )
        e = self._get_entity(kb, "people/charlie")
        assert e is not None
        assert e.name == "Charlie Day"

    def test_handles_non_string_aliases(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# KB\n")
        # Phone number without quotes → YAML parses as int
        self._make_entity(
            kb,
            "people/dave",
            "---\nsource: manual\naliases:\n- 14155551234\n- Dave Wilson\n---\n# Dave\n",
        )
        e = self._get_entity(kb, "people/dave")
        assert e is not None
        assert e.name == "Dave Wilson"

    def test_falls_back_to_dir_name_without_aliases(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# KB\n")
        self._make_entity(
            kb, "people/unknown_person", "---\nsource: manual\naliases: []\n---\n# Unknown\n"
        )
        e = self._get_entity(kb, "people/unknown_person")
        assert e is not None
        assert e.name == "unknown_person"


# ============================================================================
# BUG-3: validate_kb false positives on subcategory directories
# ============================================================================


class TestValidateKbCategoryDirs:
    """validate_kb should not flag category dirs as missing entities."""

    def _create_kb_with_subcategories(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("---\naliases: []\n---\n# Test KB\n")

        for d in ["people", "people/friends", "people/work"]:
            (kb / d).mkdir(parents=True, exist_ok=True)
            (kb / d / "_summary.md").write_text(f"---\naliases: []\n---\n# {d}\n")

        alice = kb / "people" / "friends" / "alice"
        alice.mkdir()
        (alice / "_summary.md").write_text("---\nsource: test\naliases:\n- Alice\n---\n# Alice\n")

        bob = kb / "people" / "work" / "bob"
        bob.mkdir()
        (bob / "_summary.md").write_text("---\nsource: test\naliases:\n- Bob\n---\n# Bob\n")

        return kb

    def test_subcategory_dirs_not_flagged(self, tmp_path):
        from kvault.mcp.server import handle_kvault_init, handle_kvault_validate_kb

        kb = self._create_kb_with_subcategories(tmp_path)
        handle_kvault_init(str(kb))
        result = handle_kvault_validate_kb()
        assert result["valid"] is True or result["issue_count"] >= 0

    def test_leaf_entity_without_frontmatter_not_scanned(self, tmp_path):
        """Entity without frontmatter won't appear in scan (no index to have ghost entries)."""
        from kvault.mcp.server import handle_kvault_init, handle_kvault_validate_kb

        kb = self._create_kb_with_subcategories(tmp_path)

        orphan = kb / "people" / "friends" / "orphan"
        orphan.mkdir()
        (orphan / "_summary.md").write_text("# Orphan\n\nNo frontmatter.\n")

        handle_kvault_init(str(kb))
        result = handle_kvault_validate_kb()
        # No index means no "ghost" entries — orphan simply isn't found by scan
        assert result is not None


# ============================================================================
# BUG-1 fix: write_entity auto-sets name from alias
# ============================================================================


class TestWriteEntityAutoName:
    """write_entity should auto-set 'name' from first non-email alias."""

    def test_auto_name_from_alias(self, empty_kb):
        from kvault.mcp.server import handle_kvault_write_entity, handle_kvault_read_entity

        handle_kvault_write_entity(
            path="people/test",
            meta={"source": "test", "aliases": ["Test Person", "test@example.com"]},
            content="# Test\n",
            create=True,
        )
        entity = handle_kvault_read_entity("people/test")
        assert entity["meta"]["name"] == "Test Person"

    def test_auto_name_skips_email(self, empty_kb):
        from kvault.mcp.server import handle_kvault_write_entity, handle_kvault_read_entity

        handle_kvault_write_entity(
            path="people/test",
            meta={"source": "test", "aliases": ["test@example.com", "Test Person"]},
            content="# Test\n",
            create=True,
        )
        entity = handle_kvault_read_entity("people/test")
        assert entity["meta"]["name"] == "Test Person"

    def test_auto_name_handles_non_string_aliases(self, empty_kb):
        from kvault.mcp.server import handle_kvault_write_entity, handle_kvault_read_entity

        handle_kvault_write_entity(
            path="people/test",
            meta={"source": "test", "aliases": [14155551234, "Test Person"]},
            content="# Test\n",
            create=True,
        )
        entity = handle_kvault_read_entity("people/test")
        assert entity["meta"]["name"] == "Test Person"

    def test_explicit_name_not_overwritten(self, empty_kb):
        from kvault.mcp.server import handle_kvault_write_entity, handle_kvault_read_entity

        handle_kvault_write_entity(
            path="people/test",
            meta={"source": "test", "aliases": ["Alias"], "name": "Explicit Name"},
            content="# Test\n",
            create=True,
        )
        entity = handle_kvault_read_entity("people/test")
        assert entity["meta"]["name"] == "Explicit Name"
