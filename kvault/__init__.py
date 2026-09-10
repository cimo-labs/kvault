"""
kvault - Personal knowledge base for AI agents.

Stores entities as YAML-frontmatter Markdown files with hierarchical
summary propagation. CLI-first: agents call kvault commands via shell.
Also available as a legacy MCP server.

No external API keys. No extra cost. Just files.
"""

from kvault._version import __version__

from kvault.core.frontmatter import parse_frontmatter, build_frontmatter, merge_frontmatter
from kvault.core.daily_artifacts import DailyArtifactResult, generate_daily_artifact, parse_iso_date
from kvault.core.check import Finding, run_checks
from kvault.core.notes import NOTE_CODES, collapse, note
from kvault.core.plan import build_plan
from kvault.core.observability import ObservabilityLogger
from kvault.core.oplog import OpLog
from kvault.core.research import EntityResearcher, ResearchCandidate
from kvault.core.summary_quality import (
    SummaryQualityIssue,
    audit_summary_quality,
    format_summary_quality_warnings,
)
from kvault.core.search import SearchDocument, SearchResult, scan_search_documents, search_nodes
from kvault.core.storage import (
    SimpleStorage,
    normalize_entity_id,
    EntityRecord,
    scan_entities,
    count_entities,
    list_entity_records,
)

__all__ = [
    "__version__",
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
    "NOTE_CODES",
    "Finding",
    "run_checks",
    "build_plan",
    "note",
    "collapse",
    "ObservabilityLogger",
    "OpLog",
    "SummaryQualityIssue",
    "audit_summary_quality",
    "format_summary_quality_warnings",
    "SearchDocument",
    "SearchResult",
    "scan_search_documents",
    "search_nodes",
]
