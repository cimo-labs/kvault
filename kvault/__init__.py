"""
kvault - Personal knowledge base for AI agents.

Stores entities as YAML-frontmatter Markdown files with hierarchical
summary propagation. CLI-first: agents call kvault commands via shell.
Also available as a legacy MCP server.

No external API keys. No extra cost. Just files.
"""

__version__ = "0.7.0"

from kvault.core.frontmatter import parse_frontmatter, build_frontmatter, merge_frontmatter
from kvault.core.daily_artifacts import DailyArtifactResult, generate_daily_artifact, parse_iso_date
from kvault.core.research import EntityResearcher, ResearchCandidate
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
    "DailyArtifactResult",
    "generate_daily_artifact",
    "parse_iso_date",
    "EntityResearcher",
    "ResearchCandidate",
]
