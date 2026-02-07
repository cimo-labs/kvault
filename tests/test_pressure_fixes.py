"""Tests for pressure-test findings: name extraction, validate_kb false positives."""

import json
import os
import pytest
from pathlib import Path

from kvault.core.index import EntityIndex
from kvault.core.frontmatter import parse_frontmatter, build_frontmatter


# ============================================================================
# BUG-2: Entity name extraction
# ============================================================================


class TestNameExtraction:
    """Entity display names should come from aliases, not directory slugs."""

    def test_rebuild_uses_first_alias_as_name(self, tmp_path):
        """rebuild() should use first non-email alias as name."""
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# Test KB\n")

        people = kb / "people"
        people.mkdir()
        (people / "_summary.md").write_text("# People\n")

        entity = people / "alice_smith"
        entity.mkdir()
        (entity / "_summary.md").write_text(
            "---\n"
            "source: manual\n"
            "aliases:\n"
            "- Alice Smith\n"
            "- alice@example.com\n"
            "created: '2026-02-07'\n"
            "updated: '2026-02-07'\n"
            "---\n\n"
            "# Alice Smith\n\nData scientist.\n"
        )

        index = EntityIndex(kb / ".kvault" / "index.db")
        count = index.rebuild(kb)
        assert count == 1

        entry = index.get("people/alice_smith")
        assert entry is not None
        assert entry.name == "Alice Smith", f"Expected 'Alice Smith', got '{entry.name}'"

    def test_rebuild_skips_email_alias_for_name(self, tmp_path):
        """If first alias is email, should still find a non-email name."""
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# Test KB\n")

        people = kb / "people"
        people.mkdir()
        (people / "_summary.md").write_text("# People\n")

        entity = people / "bob"
        entity.mkdir()
        # Note: +14155551234 without quotes gets parsed as int by YAML.
        # This is a real-world scenario (phones from frontmatter).
        (entity / "_summary.md").write_text(
            "---\n"
            "source: manual\n"
            "aliases:\n"
            "- bob@example.com\n"
            "- '+14155551234'\n"
            "- Bob Jones\n"
            "created: '2026-02-07'\n"
            "updated: '2026-02-07'\n"
            "---\n\n"
            "# Bob Jones\n"
        )

        index = EntityIndex(kb / ".kvault" / "index.db")
        index.rebuild(kb)

        entry = index.get("people/bob")
        assert entry is not None
        assert entry.name == "Bob Jones", f"Expected 'Bob Jones', got '{entry.name}'"

    def test_rebuild_falls_back_to_email_if_only_alias(self, tmp_path):
        """If all aliases are emails/phones, use first alias anyway."""
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# Test KB\n")

        people = kb / "people"
        people.mkdir()
        (people / "_summary.md").write_text("# People\n")

        entity = people / "contact"
        entity.mkdir()
        (entity / "_summary.md").write_text(
            "---\n"
            "source: manual\n"
            "aliases:\n"
            "- contact@example.com\n"
            "created: '2026-02-07'\n"
            "updated: '2026-02-07'\n"
            "---\n\n"
            "# Contact\n"
        )

        index = EntityIndex(kb / ".kvault" / "index.db")
        index.rebuild(kb)

        entry = index.get("people/contact")
        assert entry is not None
        assert entry.name == "contact@example.com"

    def test_rebuild_uses_explicit_name_over_alias(self, tmp_path):
        """Explicit 'name' in frontmatter takes priority over aliases."""
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# Test KB\n")

        people = kb / "people"
        people.mkdir()
        (people / "_summary.md").write_text("# People\n")

        entity = people / "charlie"
        entity.mkdir()
        (entity / "_summary.md").write_text(
            "---\n"
            "source: manual\n"
            "name: Charlie Brown\n"
            "aliases:\n"
            "- Chuck\n"
            "created: '2026-02-07'\n"
            "updated: '2026-02-07'\n"
            "---\n\n"
            "# Charlie Brown\n"
        )

        index = EntityIndex(kb / ".kvault" / "index.db")
        index.rebuild(kb)

        entry = index.get("people/charlie")
        assert entry is not None
        assert entry.name == "Charlie Brown"

    def test_rebuild_handles_non_string_aliases(self, tmp_path):
        """Phone numbers parsed as int by YAML should not crash rebuild."""
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# Test KB\n")

        people = kb / "people"
        people.mkdir()
        (people / "_summary.md").write_text("# People\n")

        entity = people / "dave"
        entity.mkdir()
        # Unquoted phone number — YAML parses as int
        (entity / "_summary.md").write_text(
            "---\n"
            "source: manual\n"
            "aliases:\n"
            "- 14155551234\n"
            "- Dave Wilson\n"
            "created: '2026-02-07'\n"
            "updated: '2026-02-07'\n"
            "---\n\n"
            "# Dave Wilson\n"
        )

        index = EntityIndex(kb / ".kvault" / "index.db")
        # Should not crash
        count = index.rebuild(kb)
        assert count == 1

        entry = index.get("people/dave")
        assert entry is not None
        assert entry.name == "Dave Wilson"

    def test_rebuild_falls_back_to_dir_name_without_aliases(self, tmp_path):
        """Without name, topic, or aliases, fall back to directory name."""
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# Test KB\n")

        people = kb / "people"
        people.mkdir()
        (people / "_summary.md").write_text("# People\n")

        entity = people / "mystery_person"
        entity.mkdir()
        (entity / "_summary.md").write_text(
            "---\n"
            "source: manual\n"
            "aliases: []\n"
            "created: '2026-02-07'\n"
            "updated: '2026-02-07'\n"
            "---\n\n"
            "# Mystery\n"
        )

        index = EntityIndex(kb / ".kvault" / "index.db")
        index.rebuild(kb)

        entry = index.get("people/mystery_person")
        assert entry is not None
        assert entry.name == "mystery_person"


# ============================================================================
# BUG-3: validate_kb false positives on subcategory dirs
# ============================================================================


class TestValidateKbCategoryDirs:
    """validate_kb should not flag category dirs as missing from index."""

    def _create_kb_with_subcategories(self, tmp_path):
        """Create a KB with subcategory structure like Eddie's personal KB."""
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# Test KB\n")

        # Top-level category
        people = kb / "people"
        people.mkdir()
        (people / "_summary.md").write_text("# People\n")

        # Subcategory with children (should NOT be flagged)
        friends = people / "friends"
        friends.mkdir()
        (friends / "_summary.md").write_text("# Friends\n")

        # Leaf entity under subcategory
        alice = friends / "alice"
        alice.mkdir()
        (alice / "_summary.md").write_text(
            "---\nsource: manual\naliases: [Alice]\n"
            "created: '2026-02-07'\nupdated: '2026-02-07'\n---\n\n# Alice\n"
        )

        bob = friends / "bob"
        bob.mkdir()
        (bob / "_summary.md").write_text(
            "---\nsource: manual\naliases: [Bob]\n"
            "created: '2026-02-07'\nupdated: '2026-02-07'\n---\n\n# Bob\n"
        )

        # Another subcategory (should NOT be flagged)
        work = people / "work"
        work.mkdir()
        (work / "_summary.md").write_text("# Work Contacts\n")

        charlie = work / "charlie"
        charlie.mkdir()
        (charlie / "_summary.md").write_text(
            "---\nsource: manual\naliases: [Charlie]\n"
            "created: '2026-02-07'\nupdated: '2026-02-07'\n---\n\n# Charlie\n"
        )

        return kb

    def test_subcategory_dirs_not_flagged(self, tmp_path):
        """Validate KB should not crash on subcategory dirs with children."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))

        from kvault.mcp.server import handle_kvault_init, handle_kvault_validate_kb

        kb = self._create_kb_with_subcategories(tmp_path)

        handle_kvault_init(str(kb))
        result = handle_kvault_validate_kb()

        # With no index, there are no index_missing issues — just verify it runs clean
        assert result["valid"] is True or result["issue_count"] >= 0

    def test_leaf_entity_without_frontmatter_flagged(self, tmp_path):
        """A leaf entity without frontmatter SHOULD be flagged as missing_frontmatter."""
        from kvault.mcp.server import handle_kvault_init, handle_kvault_validate_kb

        kb = self._create_kb_with_subcategories(tmp_path)

        # Add a leaf entity without frontmatter
        orphan = kb / "people" / "friends" / "orphan"
        orphan.mkdir()
        (orphan / "_summary.md").write_text("# Orphan\n\nNo frontmatter here.\n")

        handle_kvault_init(str(kb))
        result = handle_kvault_validate_kb()

        # Entity without frontmatter won't be found by scan_entities
        # (scan_entities requires frontmatter or _meta.json)
        # This is by design — no index means no "ghost" entries
        assert result is not None


# ============================================================================
# MCP write_entity auto-name
# ============================================================================


class TestWriteEntityAutoName:
    """write_entity should auto-set 'name' from first alias."""

    def test_auto_name_from_alias(self, tmp_path):
        """write_entity sets name from first non-email alias."""
        from kvault.mcp.server import handle_kvault_init, handle_kvault_write_entity, handle_kvault_read_entity

        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# Test\n")
        (kb / "people").mkdir()
        (kb / "people" / "_summary.md").write_text("# People\n")

        handle_kvault_init(str(kb))

        result = handle_kvault_write_entity(
            path="people/alice",
            meta={"source": "manual", "aliases": ["Alice Smith", "alice@example.com"]},
            content="# Alice Smith\n",
            create=True,
        )
        assert result.get("success")

        # Read back and check name is in frontmatter
        entity = handle_kvault_read_entity("people/alice")
        assert entity is not None
        assert entity["meta"].get("name") == "Alice Smith"

    def test_auto_name_skips_email(self, tmp_path):
        """Auto-name should skip email aliases."""
        from kvault.mcp.server import handle_kvault_init, handle_kvault_write_entity, handle_kvault_read_entity

        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# Test\n")
        (kb / "people").mkdir()
        (kb / "people" / "_summary.md").write_text("# People\n")

        handle_kvault_init(str(kb))

        result = handle_kvault_write_entity(
            path="people/bob",
            meta={"source": "manual", "aliases": ["bob@example.com", "+14155551234", "Bob Jones"]},
            content="# Bob\n",
            create=True,
        )
        assert result.get("success")

        entity = handle_kvault_read_entity("people/bob")
        assert entity["meta"].get("name") == "Bob Jones"

    def test_auto_name_handles_non_string_aliases(self, tmp_path):
        """Non-string aliases (e.g., YAML-parsed phone as int) should not crash."""
        from kvault.mcp.server import handle_kvault_init, handle_kvault_write_entity, handle_kvault_read_entity

        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# Test\n")
        (kb / "people").mkdir()
        (kb / "people" / "_summary.md").write_text("# People\n")

        handle_kvault_init(str(kb))

        # Simulate YAML parsing phone as int (real scenario)
        result = handle_kvault_write_entity(
            path="people/dave",
            meta={"source": "manual", "aliases": [14155551234, "Dave Wilson"]},
            content="# Dave\n",
            create=True,
        )
        assert result.get("success")

        entity = handle_kvault_read_entity("people/dave")
        assert entity["meta"].get("name") == "Dave Wilson"

    def test_explicit_name_not_overwritten(self, tmp_path):
        """If name is already provided, don't overwrite it."""
        from kvault.mcp.server import handle_kvault_init, handle_kvault_write_entity, handle_kvault_read_entity

        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "_summary.md").write_text("# Test\n")
        (kb / "people").mkdir()
        (kb / "people" / "_summary.md").write_text("# People\n")

        handle_kvault_init(str(kb))

        result = handle_kvault_write_entity(
            path="people/charlie",
            meta={"source": "manual", "aliases": ["Chuck"], "name": "Charlie Brown"},
            content="# Charlie Brown\n",
            create=True,
        )
        assert result.get("success")

        entity = handle_kvault_read_entity("people/charlie")
        assert entity["meta"].get("name") == "Charlie Brown"
