"""YAML frontmatter parsing for markdown files."""

import yaml
from typing import Any, Dict, Tuple


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
        If no frontmatter found, returns ({}, original_content).
    """
    if not content.startswith("---"):
        return {}, content

    # Find closing ---
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content

    yaml_content = content[4:end]
    remaining = content[end + 4 :].lstrip("\n")

    try:
        meta = yaml.safe_load(yaml_content)
        return meta or {}, remaining
    except yaml.YAMLError:
        return {}, content


def build_frontmatter(meta: Dict[str, Any]) -> str:
    """Build YAML frontmatter string from dict.

    Args:
        meta: Dictionary of metadata fields

    Returns:
        Formatted frontmatter string with --- delimiters and trailing newlines
    """
    yaml_str = yaml.dump(
        meta,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    return f"---\n{yaml_str}---\n\n"


def merge_frontmatter(
    existing: Dict[str, Any], new: Dict[str, Any]
) -> Dict[str, Any]:
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
