"""Journal-first, local durable memory for AI agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("knowledgevault")
except PackageNotFoundError:
    __version__ = "0.12.0"

from kvault.core.frontmatter import parse_frontmatter, build_frontmatter, merge_frontmatter
from kvault.core.daily_artifacts import DailyArtifactResult, generate_daily_artifact, parse_iso_date
from kvault.core.observability import ObservabilityLogger
from kvault.core.research import EntityResearcher, ResearchCandidate
from kvault.core.summary_quality import (
    SummaryQualityIssue,
    audit_summary_quality,
    format_summary_quality_warnings,
)
from kvault.core.search import SearchDocument, SearchResult, scan_search_documents, search_nodes
from kvault.core.events import (
    CaptureEventResult,
    EventRecord,
    EventStatus,
    ReconciliationOutcome,
    Sensitivity,
    capture_event,
    derive_event_states,
    get_event,
    list_events,
)
from kvault.core.migration import (
    CURRENT_SCHEMA_VERSION,
    MigrationResult,
    MossImportResult,
    import_moss_capture,
    migrate,
)
from kvault.core.policy import ReconciliationPolicy, load_policy
from kvault.core.reconciliation import (
    EventDecision,
    Mutation,
    ReconciliationPlan,
    ReconciliationResult,
    apply_reconciliation,
    approve_reconciliation,
    prepare_reconciliation,
    reconciliation_status,
    recover_reconciliations,
)
from kvault.core.validation import audit_kb
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
    "CaptureEventResult",
    "EventRecord",
    "EventStatus",
    "ReconciliationOutcome",
    "Sensitivity",
    "capture_event",
    "derive_event_states",
    "get_event",
    "list_events",
    "CURRENT_SCHEMA_VERSION",
    "MigrationResult",
    "MossImportResult",
    "import_moss_capture",
    "migrate",
    "ReconciliationPolicy",
    "load_policy",
    "EventDecision",
    "Mutation",
    "ReconciliationPlan",
    "ReconciliationResult",
    "apply_reconciliation",
    "approve_reconciliation",
    "prepare_reconciliation",
    "reconciliation_status",
    "recover_reconciliations",
    "audit_kb",
    "EntityResearcher",
    "ResearchCandidate",
    "ObservabilityLogger",
    "SummaryQualityIssue",
    "audit_summary_quality",
    "format_summary_quality_warnings",
    "SearchDocument",
    "SearchResult",
    "scan_search_documents",
    "search_nodes",
]
