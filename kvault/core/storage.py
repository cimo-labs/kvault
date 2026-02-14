"""
SimpleStorage - Filesystem storage with minimal schema.

Stores entities as directories with:
- _meta.json: 4-field metadata (created, last_updated, sources, aliases)
- _summary.md: Freeform markdown content

Also provides scan_entities() for walking the KB and parsing entity metadata.
"""

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from kvault.core.frontmatter import parse_frontmatter


def normalize_entity_id(name: str) -> str:
    """Convert entity name to a normalized ID.

    Rules:
    1. Lowercase
    2. Replace spaces with underscores
    3. Remove special characters except underscores
    4. Collapse multiple underscores

    Examples:
        "Alice Smith" -> "alice_smith"
        "R&L Carriers" -> "rl_carriers"
        "Universal Robots A/S" -> "universal_robots_as"
    """
    name = name.lower()
    name = name.replace("_", " ")
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


class SimpleStorage:
    """Filesystem storage with minimal schema.

    Schema for _meta.json (4 required fields):
    - created: ISO date when entity was created
    - last_updated: ISO date of last update
    - sources: List of source identifiers
    - aliases: List of alternative names/emails

    Additional fields are allowed but not required.
    """

    REQUIRED_FIELDS = ["created", "last_updated", "sources", "aliases"]

    def __init__(self, kg_root: Path):
        """Initialize storage with knowledge graph root.

        Args:
            kg_root: Root directory of the knowledge graph
        """
        self.kg_root = Path(kg_root)

    def _get_entity_path(self, entity_path: str) -> Path:
        """Get full filesystem path for an entity."""
        return self.kg_root / entity_path

    def _get_meta_path(self, entity_path: str) -> Path:
        """Get path to _meta.json for an entity."""
        return self._get_entity_path(entity_path) / "_meta.json"

    def _get_summary_path(self, entity_path: str) -> Path:
        """Get path to _summary.md for an entity."""
        return self._get_entity_path(entity_path) / "_summary.md"

    def read_meta(self, entity_path: str) -> Optional[Dict[str, Any]]:
        """Read _meta.json for an entity.

        Args:
            entity_path: Relative path to entity (e.g., "people/collaborators/alice_smith")

        Returns:
            Metadata dictionary if found, None otherwise
        """
        meta_path = self._get_meta_path(entity_path)
        if not meta_path.exists():
            return None

        with open(meta_path) as f:
            return json.load(f)

    def write_meta(self, entity_path: str, data: Dict[str, Any]) -> None:
        """Write _meta.json for an entity.

        Args:
            entity_path: Relative path to entity
            data: Metadata to write (must include required fields)

        Raises:
            ValueError: If required fields are missing
        """
        missing = [f for f in self.REQUIRED_FIELDS if f not in data]
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        meta_path = self._get_meta_path(entity_path)
        meta_path.parent.mkdir(parents=True, exist_ok=True)

        with open(meta_path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

    def read_summary(self, entity_path: str) -> Optional[str]:
        """Read _summary.md for an entity.

        Args:
            entity_path: Relative path to entity

        Returns:
            Summary content if found, None otherwise
        """
        summary_path = self._get_summary_path(entity_path)
        if not summary_path.exists():
            return None

        with open(summary_path) as f:
            return f.read()

    def write_summary(self, entity_path: str, content: str) -> None:
        """Write _summary.md for an entity.

        Args:
            entity_path: Relative path to entity
            content: Markdown content to write
        """
        summary_path = self._get_summary_path(entity_path)
        summary_path.parent.mkdir(parents=True, exist_ok=True)

        with open(summary_path, "w") as f:
            f.write(content)

    def entity_exists(self, entity_path: str) -> bool:
        """Check if entity directory exists with _meta.json.

        Args:
            entity_path: Relative path to entity

        Returns:
            True if entity exists
        """
        return self._get_meta_path(entity_path).exists()

    def create_entity(
        self,
        entity_path: str,
        meta: Dict[str, Any],
        summary: str,
    ) -> Path:
        """Create new entity with _meta.json and _summary.md.

        Args:
            entity_path: Relative path for new entity
            meta: Metadata (must include required fields)
            summary: Markdown summary content

        Returns:
            Full path to created entity directory

        Raises:
            ValueError: If entity already exists or required fields missing
        """
        if self.entity_exists(entity_path):
            raise ValueError(f"Entity already exists: {entity_path}")

        # Ensure required fields with defaults
        now = datetime.now().strftime("%Y-%m-%d")
        meta.setdefault("created", now)
        meta.setdefault("last_updated", now)
        meta.setdefault("sources", [])
        meta.setdefault("aliases", [])

        entity_dir = self._get_entity_path(entity_path)
        entity_dir.mkdir(parents=True, exist_ok=True)

        self.write_meta(entity_path, meta)
        self.write_summary(entity_path, summary)

        return entity_dir

    def update_entity(
        self,
        entity_path: str,
        meta: Optional[Dict[str, Any]] = None,
        summary: Optional[str] = None,
    ) -> None:
        """Update existing entity (partial update supported).

        Args:
            entity_path: Relative path to entity
            meta: Optional metadata updates (merged with existing)
            summary: Optional new summary content (replaces existing)

        Raises:
            ValueError: If entity doesn't exist
        """
        if not self.entity_exists(entity_path):
            raise ValueError(f"Entity doesn't exist: {entity_path}")

        if meta:
            existing_meta = self.read_meta(entity_path) or {}
            existing_meta.update(meta)
            existing_meta["last_updated"] = datetime.now().strftime("%Y-%m-%d")
            self.write_meta(entity_path, existing_meta)

        if summary is not None:
            self.write_summary(entity_path, summary)

    def delete_entity(self, entity_path: str) -> None:
        """Delete entity directory.

        Args:
            entity_path: Relative path to entity
        """
        entity_dir = self._get_entity_path(entity_path)
        if entity_dir.exists():
            shutil.rmtree(entity_dir)

    def list_entities(self, category_path: str) -> List[str]:
        """List entity paths under a category.

        Args:
            category_path: Relative path to category (e.g., "people/collaborators")

        Returns:
            List of entity paths relative to kg_root
        """
        category_dir = self._get_entity_path(category_path)
        if not category_dir.exists():
            return []

        entities = []
        for item in category_dir.iterdir():
            if item.is_dir() and not item.name.startswith("_"):
                meta_path = item / "_meta.json"
                if meta_path.exists():
                    entities.append(str(item.relative_to(self.kg_root)))

        return sorted(entities)

    def list_all_entities(self) -> List[str]:
        """List all entity paths in the knowledge graph.

        Returns:
            List of all entity paths relative to kg_root
        """
        entities = []

        for meta_path in self.kg_root.rglob("_meta.json"):
            # Skip hidden directories and .kvault
            if any(part.startswith(".") for part in meta_path.parts):
                continue

            entity_dir = meta_path.parent
            rel_path = str(entity_dir.relative_to(self.kg_root))
            entities.append(rel_path)

        return sorted(entities)

    def get_ancestors(self, entity_path: str) -> List[str]:
        """Return ancestor paths from entity to root.

        Args:
            entity_path: Relative path to entity

        Returns:
            List of ancestor paths (closest first, excluding the entity itself)
            Example: ["people/collaborators", "people"] for "people/collaborators/alice"
        """
        parts = Path(entity_path).parts
        ancestors = []

        for i in range(len(parts) - 1, 0, -1):
            ancestor_path = str(Path(*parts[:i]))
            ancestors.append(ancestor_path)

        return ancestors

    def get_children(self, category_path: str) -> List[str]:
        """Get immediate child entity paths.

        Args:
            category_path: Relative path to category

        Returns:
            List of child entity paths
        """
        category_dir = self._get_entity_path(category_path)
        if not category_dir.exists():
            return []

        children = []
        for item in category_dir.iterdir():
            if item.is_dir() and not item.name.startswith("_"):
                rel_path = str(item.relative_to(self.kg_root))
                children.append(rel_path)

        return sorted(children)

    def get_entity_name(self, entity_path: str) -> Optional[str]:
        """Get display name for an entity.

        Args:
            entity_path: Relative path to entity

        Returns:
            Name from _meta.json or derived from path
        """
        meta = self.read_meta(entity_path)
        if meta:
            return meta.get("topic", meta.get("name", Path(entity_path).name))
        return None


# ---------------------------------------------------------------------------
# Entity scanning (moved from search.py)
# ---------------------------------------------------------------------------


@dataclass
class EntityRecord:
    """Parsed entity from disk — cheap to build, cached per search call."""

    path: str
    name: str
    aliases: List[str]
    category: str
    email_domains: List[str]
    content: str  # raw markdown body (after frontmatter)
    last_updated: str = ""  # YYYY-MM-DD from file mtime


def scan_entities(kg_root: Path) -> List[EntityRecord]:
    """Walk the KB and parse every entity.

    An entity is a directory containing _summary.md with YAML frontmatter,
    at depth >= 2 from kg_root (i.e. category/entity at minimum).

    Returns list of EntityRecord. Cheap at < 1000 entities.
    """
    kg_root = Path(kg_root)
    entities: List[EntityRecord] = []

    for summary_path in kg_root.rglob("_summary.md"):
        entity_dir = summary_path.parent
        rel_path = entity_dir.relative_to(kg_root)

        # Skip hidden dirs (check relative path parts, not absolute)
        if any(part.startswith(".") for part in rel_path.parts):
            continue

        # Must be at least 2 levels deep (category/entity)
        if len(rel_path.parts) < 2:
            continue

        try:
            content = summary_path.read_text()
        except OSError:
            continue

        meta, body = parse_frontmatter(content)
        if not meta:
            # Check for legacy _meta.json
            meta_path = entity_dir / "_meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
            else:
                continue

        # Extract aliases (coerce non-strings)
        aliases = [str(a) for a in meta.get("aliases", []) if a is not None]

        # Add phone/email from dedicated fields
        for extra_field in ("phone", "email"):
            val = meta.get(extra_field)
            if val and str(val) not in aliases:
                aliases.append(str(val))

        # Derive display name
        name = meta.get("name") or meta.get("topic")
        if not name and aliases:
            for a in aliases:
                if (
                    isinstance(a, str)
                    and "@" not in a
                    and not a.startswith("+")
                    and not a.isdigit()
                ):
                    name = a
                    break
            if not name:
                name = str(aliases[0])
        if not name:
            name = entity_dir.name

        # Extract email domains
        email_domains = []
        for a in aliases:
            if "@" in a:
                domain = a.split("@")[-1].lower()
                if domain not in email_domains:
                    email_domains.append(domain)

        category = rel_path.parts[0]

        # Derive last_updated from frontmatter or file mtime
        last_updated = meta.get("updated") or meta.get("created") or ""
        if not last_updated:
            try:
                mtime = os.path.getmtime(summary_path)
                last_updated = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            except OSError:
                last_updated = ""

        entities.append(
            EntityRecord(
                path=str(rel_path),
                name=name,
                aliases=aliases,
                category=category,
                email_domains=email_domains,
                content=body,
                last_updated=last_updated,
            )
        )

    return entities


def count_entities(
    kg_root: Path,
    category: Optional[str] = None,
    *,
    _entities: Optional[List[EntityRecord]] = None,
) -> int:
    """Count entities, optionally filtered by category."""
    entities = _entities or scan_entities(kg_root)
    if category:
        entities = [e for e in entities if e.category == category]
    return len(entities)


def list_entity_records(
    kg_root: Path,
    category: Optional[str] = None,
    *,
    _entities: Optional[List[EntityRecord]] = None,
) -> List[EntityRecord]:
    """List all entities (optionally filtered). Returns them sorted by name."""
    entities = _entities or scan_entities(kg_root)
    if category:
        entities = [e for e in entities if e.category == category]
    entities.sort(key=lambda e: e.name.lower())
    return entities


__all__ = [
    "SimpleStorage",
    "normalize_entity_id",
    "EntityRecord",
    "scan_entities",
    "count_entities",
    "list_entity_records",
]
