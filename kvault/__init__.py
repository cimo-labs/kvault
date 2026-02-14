"""
kvault - Personal knowledge base for AI agents.

Stores entities as YAML-frontmatter Markdown files with hierarchical
summary propagation. Runs as an MCP server inside Claude Code, Cursor,
VS Code, or any MCP-compatible tool.

No external API keys. No extra cost. Just files.
"""

__version__ = "0.6.0"

from kvault.core.frontmatter import parse_frontmatter, build_frontmatter, merge_frontmatter
from kvault.core.storage import (
    SimpleStorage,
    normalize_entity_id,
    EntityRecord,
    scan_entities,
    count_entities,
    list_entity_records,
)

__all__ = [
    "parse_frontmatter",
    "build_frontmatter",
    "merge_frontmatter",
    "SimpleStorage",
    "normalize_entity_id",
    "EntityRecord",
    "scan_entities",
    "count_entities",
    "list_entity_records",
]
