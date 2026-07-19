"""Business rules and validation for kvault operations.

Shared validation logic used by both the MCP server and CLI commands.
"""

import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kvault.core.frontmatter import FrontmatterError, parse_frontmatter
from kvault.core.paths import resolve_within_root

SUMMARY_UPDATE_DIGEST_ALGORITHM = "direct-child-summary-sha256-v1"
_PLACEHOLDER_LINE_RE = re.compile(
    r"^[-*\s]*"
    r"(?:(?:context|background|details|notes|summary|overview)\s*[:\-]?\s*)?"
    r"(?:tbd|tbc|todo|to be determined|to be added|placeholder|\(placeholder\)|fill in)\.?$",
    re.IGNORECASE,
)


class ErrorCode(Enum):
    """Structured error codes for MCP responses."""

    VALIDATION_ERROR = "validation_error"  # 400: Bad input
    NOT_FOUND = "not_found"  # 404: Entity doesn't exist
    ALREADY_EXISTS = "already_exists"  # 409: Conflict
    WORKFLOW_ERROR = "workflow_error"  # 422: Wrong workflow step
    NOT_INITIALIZED = "not_initialized"  # 500: Server not initialized
    SYSTEM_ERROR = "system_error"  # 500: Infrastructure issue


def error_response(
    code: ErrorCode,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a structured error response.

    Args:
        code: Error code enum value
        message: Human-readable error message
        details: Optional additional details
        hint: Optional hint for resolving the error

    Returns:
        Structured error response dictionary
    """
    response: Dict[str, Any] = {
        "success": False,
        "error_code": code.value,
        "error": message,
    }
    if details:
        response["details"] = details
    if hint:
        response["hint"] = hint
    return response


def success_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build a structured success response.

    Args:
        data: Response data dictionary

    Returns:
        Response with success=True and data merged in
    """
    return {"success": True, **data}


def normalize_phone(phone: str) -> str:
    """Normalize phone number to +1XXXXXXXXXX format.

    Args:
        phone: Raw phone number in various formats

    Returns:
        Normalized phone number or original if can't parse
    """
    # Remove all non-digit characters
    digits = re.sub(r"\D", "", phone)

    # Handle different formats
    if len(digits) == 10:
        return f"+1{digits}"
    elif len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    elif len(digits) > 10:
        return f"+{digits}"

    return phone  # Return original if can't normalize


def normalize_path(path: str) -> str:
    """Normalize entity path.

    - Remove trailing slashes
    - Remove _summary.md suffix
    - Convert to lowercase with underscores

    Args:
        path: Raw path

    Returns:
        Normalized path
    """
    path = path.rstrip("/")
    path = re.sub(r"/_summary\.md$", "", path)
    return path.lower()


def validate_entity_path(path: str) -> Tuple[bool, Optional[str]]:
    """Validate entity path format.

    Valid paths have at least two safe lowercase components.
    E.g., "people/contacts/john_doe", "projects/my_project"

    Args:
        path: Entity path to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    path = normalize_path(path)
    parts = path.split("/")

    if len(parts) < 2:
        return False, "Path must have at least 2 parts (category/entity)"

    # Check each part is valid identifier
    for part in parts:
        if not re.match(r"^[a-z][a-z0-9_]*$", part):
            return (
                False,
                f"Invalid path component: '{part}' (must be lowercase alphanumeric with underscores)",
            )

    return True, None


def validate_frontmatter(meta: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate frontmatter has required fields.

    Required: created, updated, source, aliases

    Args:
        meta: Frontmatter dictionary

    Returns:
        Tuple of (is_valid, list_of_missing_fields)
    """
    required = ["created", "updated", "source", "aliases"]
    missing = [f for f in required if f not in meta]
    return len(missing) == 0, missing


def build_default_frontmatter(
    source: str,
    aliases: Optional[List[str]] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    relationship_type: Optional[str] = None,
    context: Optional[str] = None,
) -> Dict[str, Any]:
    """Build frontmatter with defaults and optional fields.

    Args:
        source: Source identifier (required)
        aliases: List of aliases
        phone: Phone number (will be normalized)
        email: Email address
        relationship_type: Type of relationship
        context: Context string

    Returns:
        Complete frontmatter dictionary
    """
    today = datetime.now().strftime("%Y-%m-%d")

    meta: Dict[str, Any] = {
        "created": today,
        "updated": today,
        "source": source,
        "aliases": aliases or [],
    }

    if phone:
        normalized = normalize_phone(phone)
        meta["phone"] = normalized
        if normalized not in meta["aliases"]:
            meta["aliases"].append(normalized)

    if email:
        meta["email"] = email
        if email not in meta["aliases"]:
            meta["aliases"].append(email)

    if relationship_type:
        meta["relationship_type"] = relationship_type

    if context:
        meta["context"] = context

    return meta


def extract_identifiers(text: str) -> Dict[str, List[str]]:
    """Extract phone numbers, emails, and names from text.

    Args:
        text: Raw text to extract from

    Returns:
        Dictionary with 'phones', 'emails', 'names' keys
    """
    # Phone patterns
    phone_patterns = [
        r"\+1[- ]?\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}",  # +1 (555) 123-4567
        r"\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}",  # (555) 123-4567
    ]
    phones = []
    for pattern in phone_patterns:
        matches = re.findall(pattern, text)
        phones.extend([normalize_phone(m) for m in matches])

    # Email pattern
    email_pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    emails = re.findall(email_pattern, text)

    # Name pattern (capitalized words, excluding common words)
    common_words = {
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "you",
        "all",
        "can",
        "had",
        "her",
        "was",
        "one",
        "our",
        "out",
        "day",
        "get",
        "has",
        "him",
        "his",
        "how",
        "its",
        "may",
        "new",
        "now",
        "old",
        "see",
        "two",
        "way",
        "who",
        "boy",
        "did",
        "let",
        "put",
        "say",
        "she",
        "too",
        "use",
    }
    name_pattern = r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b"
    potential_names = re.findall(name_pattern, text)
    names = [n for n in potential_names if n.lower().split()[0] not in common_words]

    return {
        "phones": list(set(phones)),
        "emails": list(set(emails)),
        "names": list(set(names)),
    }


def get_journal_path(date: Optional[datetime] = None) -> str:
    """Get journal path for a given date.

    Args:
        date: Date to use (defaults to today)

    Returns:
        Journal path like "journal/2026-01/log.md"
    """
    date = date or datetime.now()
    return f"journal/{date.strftime('%Y-%m')}/log.md"


def format_journal_entry(
    actions: List[Dict[str, Any]],
    source: str,
    date: Optional[datetime] = None,
) -> str:
    """Format a journal entry for the given actions.

    Args:
        actions: List of action dicts with 'action_type', 'path', etc.
        source: Source identifier
        date: Date for the entry

    Returns:
        Formatted journal entry string
    """
    date = date or datetime.now()
    lines = [f"## {date.strftime('%Y-%m-%d')}", ""]

    for action in actions:
        action_type = action.get("action_type", "unknown")
        path = action.get("path", "unknown")

        if action_type == "create":
            lines.append(f"### Created {path.split('/')[-1]}")
            lines.append(f"- Created new entity at [{path}](../{path}/)")
        elif action_type == "update":
            lines.append(f"### Updated {path.split('/')[-1]}")
            lines.append(f"- Updated entity at [{path}](../{path}/)")
        elif action_type == "delete":
            lines.append(f"### Deleted {path.split('/')[-1]}")
            lines.append(f"- Removed entity at {path}")
        elif action_type == "move":
            target = action.get("target_path", "unknown")
            lines.append(f"### Moved {path.split('/')[-1]}")
            lines.append(f"- Moved from {path} to [{target}](../{target}/)")

        if action.get("reasoning"):
            lines.append(f"- Reason: {action['reasoning']}")
        lines.append("")

    lines.append(f"Source: {source}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared filesystem integrity audit
# ---------------------------------------------------------------------------


def is_incomplete_entity(content: str) -> bool:
    """Return whether a leaf body contains only headings/placeholders."""
    body_lines = [
        stripped
        for line in content.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]
    if not body_lines:
        return True
    return all(_PLACEHOLDER_LINE_RE.match(line) for line in body_lines)


def _node_path(root: Path, directory: Path) -> str:
    if directory == root:
        return "."
    return str(directory.relative_to(root))


def _visible_child_summaries(root: Path, parent: Path) -> List[Path]:
    children: List[Path] = []
    try:
        entries = list(parent.iterdir())
    except OSError:
        return children
    for child in entries:
        if not child.is_dir() or child.is_symlink() or child.name.startswith((".", "_")):
            continue
        summary = child / "_summary.md"
        if summary.is_file() and not summary.is_symlink():
            children.append(summary)
    return sorted(children)


def compute_children_digest(root: Path, parent_path: str) -> str:
    """Hash the exact raw content of a parent's direct child summaries."""
    root = Path(root).resolve()
    parent = resolve_within_root(
        root,
        parent_path,
        allow_root=True,
        must_exist=True,
        reject_symlinks=True,
    )
    if not parent.is_dir():
        raise ValueError(f"Digest parent is not a directory: {parent_path}")
    children = _visible_child_summaries(root, parent)
    payload = {
        "algorithm": SUMMARY_UPDATE_DIGEST_ALGORITHM,
        "parent_path": parent_path,
        "children": [
            {
                "path": _node_path(root, summary.parent),
                "summary_path": str(summary.relative_to(root)),
                "raw_sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
            }
            for summary in children
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def get_updated_date(path: Path) -> Optional[date]:
    """Read a node's frontmatter date, returning ``None`` when unavailable."""
    try:
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, FrontmatterError):
        return None
    for field in ("updated", "created"):
        value = meta.get(field)
        if value is None:
            continue
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return datetime.strptime(str(value).strip("'\""), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
    return None


def propagation_warnings(root: Path, threshold_minutes: int = 5) -> List[str]:
    """Return legacy freshness warnings for parents without persisted digests."""
    root = Path(root).resolve()
    threshold = timedelta(minutes=threshold_minutes)
    warnings: List[str] = []
    for summary in sorted(root.rglob("_summary.md")):
        if summary.is_symlink() or any(
            part.startswith((".", "_")) for part in summary.relative_to(root).parts[:-1]
        ):
            continue
        try:
            meta, _ = parse_frontmatter(summary.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, FrontmatterError):
            continue
        if meta.get("children_digest"):
            continue
        parent_date = get_updated_date(summary)
        for child in _visible_child_summaries(root, summary.parent):
            child_date = get_updated_date(child)
            stale = False
            detail = ""
            if child_date is not None and parent_date is not None and child_date != parent_date:
                stale = child_date > parent_date
                detail = f"child updated {child_date}, parent updated {parent_date}"
            else:
                try:
                    delta = datetime.fromtimestamp(child.stat().st_mtime) - datetime.fromtimestamp(
                        summary.stat().st_mtime
                    )
                except OSError:
                    continue
                stale = delta > threshold
                detail = f"{int(delta.total_seconds()) // 60}m newer"
            if stale:
                warnings.append(
                    f"PROPAGATE: edit {summary.relative_to(root)} "
                    f"({child.parent.name}/ is {detail})"
                )
    return warnings


def _issue(
    issue_type: str,
    severity: str,
    path: str,
    message: str,
    fix: str,
    **details: Any,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "type": issue_type,
        "severity": severity,
        "path": path,
        "message": message,
        "fix": fix,
    }
    if details:
        result["details"] = details
    return result


def _visible_directories(root: Path) -> List[Path]:
    result: List[Path] = []
    for directory, dirnames, _filenames in os.walk(root, followlinks=False):
        parent = Path(directory)
        kept: List[str] = []
        for name in dirnames:
            child = parent / name
            if name.startswith((".", "_")):
                continue
            if parent == root and name == "journal":
                if child.is_symlink() or (child / "_summary.md").is_file():
                    result.append(child)
                continue
            result.append(child)
            if not child.is_symlink():
                kept.append(name)
        dirnames[:] = kept
    return sorted(result)


def audit_kb(
    root: Path,
    *,
    threshold_minutes: int = 5,
    max_children: int = 10,
    check_journal: bool = True,
) -> Dict[str, Any]:
    """Run the canonical structural and content integrity audit for a KB.

    Transaction staging may disable journal checks because its overlay contains
    only the semantic tree. Live audits validate temporal records and any
    ``journal:<event-id>`` provenance references.
    """
    root = Path(root).expanduser().resolve()
    issues: List[Dict[str, Any]] = []
    root_summary = root / "_summary.md"
    if not root.is_dir():
        issues.append(
            _issue(
                "missing_root",
                "error",
                ".",
                "Knowledge base root does not exist or is not a directory",
                "Create or select an initialized kvault root",
            )
        )
        return _audit_result(issues)
    if not root_summary.is_file() or root_summary.is_symlink():
        issues.append(
            _issue(
                "missing_root_summary",
                "error",
                ".",
                "KB root must contain a real _summary.md file",
                "Initialize the KB or restore its root summary",
            )
        )

    directories = _visible_directories(root)
    for directory in directories:
        rel = _node_path(root, directory)
        if directory.is_symlink():
            issues.append(
                _issue(
                    "symlink_node",
                    "error",
                    rel,
                    "Semantic node directories cannot be symlinks",
                    "Replace the symlink with a real in-root node directory",
                )
            )
            continue
        if not (directory / "_summary.md").is_file():
            issues.append(
                _issue(
                    "missing_parent_summary",
                    "error",
                    rel,
                    "Visible directory is not a node and creates an orphan hierarchy",
                    "Create _summary.md for this branch or remove the orphan directory",
                )
            )

    summaries = [root_summary] if root_summary.is_file() else []
    summaries.extend(
        directory / "_summary.md"
        for directory in directories
        if not directory.is_symlink() and (directory / "_summary.md").is_file()
    )
    parsed: Dict[str, Tuple[Dict[str, Any], str]] = {}
    for summary in sorted(set(summaries)):
        path = _node_path(root, summary.parent)
        if summary.is_symlink():
            issues.append(
                _issue(
                    "symlink_summary",
                    "error",
                    path,
                    "Node summary cannot be a symlink",
                    "Replace it with a real in-root file",
                )
            )
            continue
        try:
            raw = summary.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(raw)
        except (OSError, UnicodeError, FrontmatterError) as exc:
            issues.append(
                _issue(
                    "malformed_frontmatter",
                    "error",
                    path,
                    f"Cannot parse node frontmatter: {exc}",
                    "Repair the YAML mapping between the frontmatter delimiters",
                )
            )
            continue
        parsed[path] = (meta, body)

        children = _visible_child_summaries(root, summary.parent)
        is_entity = path != "." and len(Path(path).parts) >= 2 and not children
        if is_entity:
            missing = [
                field
                for field in ("created", "updated", "source", "aliases")
                if field not in meta or meta[field] is None
            ]
            if missing:
                issues.append(
                    _issue(
                        "missing_frontmatter_fields",
                        "warning",
                        path,
                        f"Entity is missing required frontmatter fields: {', '.join(missing)}",
                        "Add created, updated, source, and aliases metadata",
                        missing_fields=missing,
                    )
                )
            elif not isinstance(meta.get("source"), str) or not meta["source"].strip():
                issues.append(
                    _issue(
                        "invalid_frontmatter_field",
                        "warning",
                        path,
                        "Entity frontmatter field 'source' must be a non-empty string",
                        "Set source to a stable provenance identifier",
                        field="source",
                    )
                )
            if "aliases" in meta and not isinstance(meta["aliases"], list):
                issues.append(
                    _issue(
                        "invalid_frontmatter_field",
                        "warning",
                        path,
                        "Entity frontmatter field 'aliases' must be a list",
                        "Store aliases as a YAML list, which may be empty",
                        field="aliases",
                    )
                )
            if is_incomplete_entity(body):
                issues.append(
                    _issue(
                        "incomplete_entity",
                        "info",
                        path,
                        "Entity has placeholder content that needs enrichment",
                        "Update the entity with complete context information",
                    )
                )

        persisted_digest = meta.get("children_digest")
        if persisted_digest is not None:
            expected = compute_children_digest(root, path)
            if persisted_digest != expected:
                issues.append(
                    _issue(
                        "children_digest_mismatch",
                        "error",
                        path,
                        "Persisted children_digest does not match direct child summaries",
                        "Rebuild this parent summary from current direct children",
                        expected_digest=expected,
                        persisted_digest=persisted_digest,
                    )
                )
        if len(children) > max_children:
            issues.append(
                _issue(
                    "too_many_direct_children",
                    "info",
                    path,
                    f"Branch has {len(children)} direct children (>{max_children})",
                    "Consider introducing an intermediate branch when it improves navigation",
                    child_count=len(children),
                    max_children=max_children,
                )
            )

    exact_paths = {
        path for path, (meta, _body) in parsed.items() if meta.get("children_digest") is not None
    }
    for warning in propagation_warnings(root, threshold_minutes):
        summary_rel = warning.split("edit ", 1)[-1].split(" (", 1)[0]
        parent_path = "." if summary_rel == "_summary.md" else str(Path(summary_rel).parent)
        if parent_path not in exact_paths:
            issues.append(
                _issue(
                    "stale_parent_summary",
                    "warning",
                    parent_path,
                    warning,
                    "Refresh the parent from its direct child summaries",
                )
            )

    if check_journal:
        try:
            from kvault.core.events import derive_event_states, list_events

            event_ids = {event.event_id for event in list_events(root)}
            derive_event_states(root)
        except Exception as exc:
            issues.append(
                _issue(
                    "temporal_journal_invalid",
                    "error",
                    "journal",
                    f"Temporal event or reconciliation record is invalid: {exc}",
                    "Repair or restore the immutable journal record from trusted history",
                )
            )
            event_ids = set()

        for path, (meta, _body) in parsed.items():
            source_refs = meta.get("source_refs")
            if source_refs is None:
                continue
            if not isinstance(source_refs, list):
                issues.append(
                    _issue(
                        "invalid_source_refs",
                        "error",
                        path,
                        "source_refs must be a list",
                        "Store provenance as a list of journal:<event-id> references",
                    )
                )
                continue
            for source_ref in source_refs:
                if not isinstance(source_ref, str) or not source_ref.startswith("journal:"):
                    continue
                event_id = source_ref.removeprefix("journal:")
                if event_id not in event_ids:
                    issues.append(
                        _issue(
                            "missing_source_event",
                            "error",
                            path,
                            f"Semantic node references missing event: {event_id}",
                            "Restore the immutable event or repair provenance through migration",
                            event_id=event_id,
                        )
                    )

    return _audit_result(issues)


def _audit_result(issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    severity_order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(
        key=lambda item: (
            severity_order.get(item["severity"], 99),
            item["path"],
            item["type"],
        )
    )
    summary = {
        "errors": sum(1 for issue in issues if issue["severity"] == "error"),
        "warnings": sum(1 for issue in issues if issue["severity"] == "warning"),
        "info": sum(1 for issue in issues if issue["severity"] == "info"),
    }
    return {
        "valid": summary["errors"] == 0 and summary["warnings"] == 0,
        "issue_count": len(issues),
        "issues": issues,
        "summary": summary,
    }
