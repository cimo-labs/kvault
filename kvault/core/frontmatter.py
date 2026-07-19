"""Strict YAML frontmatter parsing for Markdown files."""

from typing import Any, Dict, Mapping, Tuple

import yaml


class FrontmatterError(ValueError):
    """Raised when a frontmatter block exists but is malformed."""


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content.

    Frontmatter is enclosed between --- markers at the start of the file:
    ```
    ---
    key: value
    ---

    # Markdown content
    ```

    Args:
        content: Full markdown file content

    Returns:
        Tuple of (frontmatter_dict, remaining_content).
        If no frontmatter is present, returns ``({}, original_content)``.

    Raises:
        FrontmatterError: If a frontmatter block is unclosed, invalid YAML, or
            does not decode to a mapping.
    """
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, content

    end = next(
        (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if end is None:
        raise FrontmatterError("Unclosed YAML frontmatter block")

    yaml_content = "".join(lines[1:end])
    remaining = "".join(lines[end + 1 :]).lstrip("\r\n")
    try:
        meta = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"Invalid YAML frontmatter: {exc}") from exc

    if meta is None:
        return {}, remaining
    if not isinstance(meta, dict):
        raise FrontmatterError("YAML frontmatter must be a mapping")
    return meta, remaining


def parse_frontmatter_compat(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse frontmatter with the pre-0.12 silent-failure behavior.

    New code should use :func:`parse_frontmatter`.  This helper exists only for
    callers that must inspect a legacy corpus without failing fast.
    """
    try:
        return parse_frontmatter(content)
    except FrontmatterError:
        return {}, content


def build_frontmatter(meta: Mapping[str, Any]) -> str:
    """Build YAML frontmatter string from dict.

    Args:
        meta: Dictionary of metadata fields

    Returns:
        Formatted frontmatter string with --- delimiters and trailing newlines
    """
    if not isinstance(meta, Mapping):
        raise TypeError("frontmatter metadata must be a mapping")
    yaml_str = yaml.safe_dump(
        dict(meta),
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    return f"---\n{yaml_str}---\n\n"


def merge_frontmatter(existing: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Merge new frontmatter into existing, preserving existing values.

    Special handling:
    - Lists (like aliases) are combined and deduplicated
    - 'updated' field is always taken from new
    - Other fields: new values only added if key doesn't exist

    Args:
        existing: Current frontmatter dict
        new: New values to merge in

    Returns:
        Merged frontmatter dict
    """
    result = dict(existing)

    for key, value in new.items():
        if key == "updated":
            # Always update the 'updated' field
            result[key] = value
        elif key == "aliases":
            # Merge and deduplicate aliases
            existing_aliases = set(result.get("aliases", []))
            new_aliases = set(value) if isinstance(value, list) else {value}
            result["aliases"] = list(existing_aliases | new_aliases)
        elif key not in result:
            # Only add new keys, don't overwrite existing
            result[key] = value

    return result
