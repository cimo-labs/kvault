"""Business rules and validation for kvault MCP server.

Shared validation logic extracted from the CLI orchestrator.
"""

import re
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


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

    Valid paths: category/entity or category/subcategory/entity
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

    if len(parts) > 4:
        return False, "Path too deep (max 4 levels)"

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
