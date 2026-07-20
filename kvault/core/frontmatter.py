"""YAML frontmatter parsing for markdown files.

Reads are tolerant by default — a malformed block degrades to "no
frontmatter" so a legacy corpus stays readable.  Validation and write paths
should use :func:`parse_frontmatter_strict`, which raises
:class:`FrontmatterError` on unclosed blocks, invalid or unsafe YAML,
duplicate keys, and non-mapping payloads.
"""

import yaml
from typing import Any, Dict, Mapping, Tuple


class FrontmatterError(ValueError):
    """Raised by strict parsing when a frontmatter block is malformed."""


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys."""


def _construct_mapping_no_duplicates(
    loader: "_StrictLoader", node: yaml.MappingNode, deep: bool = False
) -> Dict[Any, Any]:
    seen = set()
    for key_node, _value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise FrontmatterError(f"Duplicate frontmatter key: {key!r}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_no_duplicates
)


def _split_frontmatter(content: str) -> Tuple[str, str, bool]:
    """Return (yaml_block, remaining, found); found is False when no block."""
    if not content.startswith("---"):
        return "", content, False
    end = content.find("\n---", 3)
    if end == -1:
        return "", content, False
    return content[4:end], content[end + 4 :].lstrip("\n"), True


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content, tolerantly.

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
        If no frontmatter is found, or the block is malformed or not a
        mapping, returns ({}, original_content).
    """
    yaml_content, remaining, found = _split_frontmatter(content)
    if not found:
        return {}, content

    try:
        meta = yaml.safe_load(yaml_content)
    except yaml.YAMLError:
        return {}, content
    if meta is None:
        return {}, remaining
    if not isinstance(meta, dict):
        return {}, content
    return meta, remaining


def parse_frontmatter_strict(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse frontmatter, raising :class:`FrontmatterError` on malformed blocks.

    Unlike :func:`parse_frontmatter`, an opened-but-unclosed block, invalid
    YAML, duplicate keys, and non-mapping payloads are errors instead of
    degrading to no-frontmatter.
    """
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end == -1:
            raise FrontmatterError("Unclosed YAML frontmatter block")
    yaml_content, remaining, found = _split_frontmatter(content)
    if not found:
        return {}, content

    try:
        meta = yaml.load(yaml_content, Loader=_StrictLoader)
    except FrontmatterError:
        raise
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"Invalid YAML frontmatter: {exc}") from exc
    if meta is None:
        return {}, remaining
    if not isinstance(meta, dict):
        raise FrontmatterError("YAML frontmatter must be a mapping")
    return meta, remaining


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
