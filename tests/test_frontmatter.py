"""Tests for kvault.core.frontmatter module."""

import pytest

from kvault.core.frontmatter import build_frontmatter, merge_frontmatter, parse_frontmatter


class TestParseFrontmatter:
    """Tests for parse_frontmatter()."""

    def test_basic_frontmatter(self):
        content = "---\nname: Alice\naliases: [Ali]\n---\n\n# Alice\n"
        meta, body = parse_frontmatter(content)
        assert meta == {"name": "Alice", "aliases": ["Ali"]}
        assert body.strip() == "# Alice"

    def test_no_frontmatter(self):
        content = "# Just Markdown\n\nSome text."
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_empty_string(self):
        meta, body = parse_frontmatter("")
        assert meta == {}
        assert body == ""

    def test_unclosed_frontmatter(self):
        content = "---\nname: Alice\nThis never closes"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_frontmatter_only_dashes(self):
        content = "---\n---\n\nBody here."
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body.strip() == "Body here."

    def test_complex_yaml(self):
        content = (
            "---\n"
            "created: '2026-01-01'\n"
            "aliases:\n"
            "  - Alice\n"
            "  - alice@example.com\n"
            "  - '+14155551234'\n"
            "source: manual\n"
            "---\n\n"
            "# Content\n"
        )
        meta, body = parse_frontmatter(content)
        assert meta["created"] == "2026-01-01"
        assert meta["aliases"] == ["Alice", "alice@example.com", "+14155551234"]
        assert meta["source"] == "manual"

    def test_invalid_yaml(self):
        content = "---\n: invalid: yaml: [unclosed\n---\n\nBody."
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_preserves_body_content(self):
        content = "---\nkey: value\n---\n\nLine 1\nLine 2\n\nLine 3\n"
        meta, body = parse_frontmatter(content)
        assert "Line 1" in body
        assert "Line 2" in body
        assert "Line 3" in body

    def test_empty_aliases_list(self):
        content = "---\naliases: []\nsource: test\n---\n\n# Entity\n"
        meta, body = parse_frontmatter(content)
        assert meta["aliases"] == []

    def test_frontmatter_with_special_characters(self):
        content = "---\nname: José García\naliases: [José, García]\n---\n\n# José\n"
        meta, body = parse_frontmatter(content)
        assert meta["name"] == "José García"


class TestBuildFrontmatter:
    """Tests for build_frontmatter()."""

    def test_basic_build(self):
        meta = {"name": "Alice", "source": "manual"}
        result = build_frontmatter(meta)
        assert result.startswith("---\n")
        assert result.endswith("---\n\n")
        assert "name: Alice" in result

    def test_roundtrip(self):
        original_meta = {
            "created": "2026-01-01",
            "source": "test",
            "aliases": ["A", "B"],
        }
        frontmatter = build_frontmatter(original_meta)
        full_content = frontmatter + "# Body\n"
        parsed_meta, body = parse_frontmatter(full_content)
        assert parsed_meta["created"] == "2026-01-01"
        assert parsed_meta["source"] == "test"
        assert set(parsed_meta["aliases"]) == {"A", "B"}

    def test_empty_meta(self):
        result = build_frontmatter({})
        assert result.startswith("---\n")
        assert result.endswith("---\n\n")

    def test_preserves_key_order(self):
        from collections import OrderedDict
        meta = OrderedDict([("source", "test"), ("aliases", []), ("created", "2026-01-01")])
        result = build_frontmatter(meta)
        source_pos = result.index("source")
        aliases_pos = result.index("aliases")
        created_pos = result.index("created")
        assert source_pos < aliases_pos < created_pos


class TestMergeFrontmatter:
    """Tests for merge_frontmatter()."""

    def test_basic_merge(self):
        existing = {"name": "Alice", "source": "manual"}
        new = {"email": "alice@example.com"}
        result = merge_frontmatter(existing, new)
        assert result["name"] == "Alice"
        assert result["email"] == "alice@example.com"

    def test_existing_values_preserved(self):
        existing = {"name": "Alice", "source": "manual"}
        new = {"name": "Bob", "source": "auto"}
        result = merge_frontmatter(existing, new)
        assert result["name"] == "Alice"
        assert result["source"] == "manual"

    def test_updated_always_overwritten(self):
        existing = {"updated": "2026-01-01"}
        new = {"updated": "2026-02-01"}
        result = merge_frontmatter(existing, new)
        assert result["updated"] == "2026-02-01"

    def test_aliases_merged_and_deduplicated(self):
        existing = {"aliases": ["Alice", "Ali"]}
        new = {"aliases": ["Ali", "A. Smith"]}
        result = merge_frontmatter(existing, new)
        assert set(result["aliases"]) == {"Alice", "Ali", "A. Smith"}

    def test_aliases_from_empty(self):
        existing = {"aliases": []}
        new = {"aliases": ["Alice"]}
        result = merge_frontmatter(existing, new)
        assert result["aliases"] == ["Alice"]

    def test_does_not_mutate_input(self):
        existing = {"name": "Alice", "aliases": ["A"]}
        new = {"aliases": ["B"]}
        merge_frontmatter(existing, new)
        assert existing["aliases"] == ["A"]
