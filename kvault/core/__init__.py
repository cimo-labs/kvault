"""kvault core modules."""

from kvault.core.frontmatter import parse_frontmatter, build_frontmatter, merge_frontmatter
from kvault.core.storage import SimpleStorage, normalize_entity_id
from kvault.core.observability import ObservabilityLogger
from kvault.core.research import EntityResearcher, ResearchCandidate
from kvault.core.daily_artifacts import (
    DailyArtifactResult,
    generate_daily_artifact,
    parse_iso_date,
)
from kvault.core.summary_quality import (
    SummaryQualityIssue,
    audit_summary_quality,
    format_summary_quality_warnings,
)

__all__ = [
    "parse_frontmatter",
    "build_frontmatter",
    "merge_frontmatter",
    "SimpleStorage",
    "normalize_entity_id",
    "ObservabilityLogger",
    "EntityResearcher",
    "ResearchCandidate",
    "DailyArtifactResult",
    "generate_daily_artifact",
    "parse_iso_date",
    "SummaryQualityIssue",
    "audit_summary_quality",
    "format_summary_quality_warnings",
]
