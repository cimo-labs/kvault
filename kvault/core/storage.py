"""Deprecated storage adapter plus canonical frontmatter entity scanning."""

import json
import os
import re
import shutil
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from kvault.core.frontmatter import build_frontmatter, parse_frontmatter
from kvault.core.paths import (
    PathSafetyError,
    resolve_node_path,
    resolve_within_root,
    validate_node_target,
)


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
    """Deprecated compatibility adapter over canonical frontmatter nodes.

    New mutations should use :mod:`kvault.core.events` and
    :mod:`kvault.core.reconciliation`. The legacy metadata names
    ``last_updated`` and ``sources`` remain accepted at this API boundary, but
    new files contain only canonical YAML frontmatter in ``_summary.md``.
    """

    REQUIRED_FIELDS = ["created", "last_updated", "sources", "aliases"]

    def __init__(self, kg_root: Path):
        """Initialize storage with knowledge graph root.

        Args:
            kg_root: Root directory of the knowledge graph
        """
        warnings.warn(
            "SimpleStorage is deprecated; use capture_event and reconciliation instead",
            DeprecationWarning,
            stacklevel=2,
        )
        self.kg_root = Path(kg_root).expanduser().resolve()

    @staticmethod
    def _canonical_meta(data: Dict[str, Any]) -> Dict[str, Any]:
        """Translate legacy field names to the canonical frontmatter schema."""
        meta = dict(data)
        updated = meta.pop("last_updated", None)
        sources = meta.pop("sources", None)
        if "updated" not in meta and updated is not None:
            meta["updated"] = updated
        if not meta.get("source") and sources:
            if isinstance(sources, list):
                meta["source"] = str(sources[0]) if sources else "legacy:simple-storage"
            else:
                meta["source"] = str(sources)
        meta.setdefault("source", "legacy:simple-storage")
        meta.setdefault("aliases", [])
        if not isinstance(meta["aliases"], list):
            raise ValueError("frontmatter field 'aliases' must be a list")
        return meta

    @staticmethod
    def _legacy_view(meta: Dict[str, Any]) -> Dict[str, Any]:
        """Return compatibility aliases without persisting legacy fields."""
        result = dict(meta)
        result.setdefault("last_updated", result.get("updated"))
        source = result.get("source")
        result.setdefault("sources", [source] if source else [])
        return result

    def _get_entity_path(self, entity_path: str) -> Path:
        """Get full filesystem path for an entity."""
        return resolve_node_path(self.kg_root, entity_path)

    def _get_meta_path(self, entity_path: str) -> Path:
        """Get path to _meta.json for an entity."""
        self._get_entity_path(entity_path)
        return resolve_within_root(
            self.kg_root,
            Path(entity_path) / "_meta.json",
            allow_root=False,
            reject_symlinks=True,
        )

    def _get_summary_path(self, entity_path: str) -> Path:
        """Get path to _summary.md for an entity."""
        self._get_entity_path(entity_path)
        return resolve_within_root(
            self.kg_root,
            Path(entity_path) / "_summary.md",
            allow_root=False,
            reject_symlinks=True,
        )

    def read_meta(self, entity_path: str) -> Optional[Dict[str, Any]]:
        """Read entity metadata from frontmatter, with legacy JSON fallback.

        Args:
            entity_path: Relative path to entity (e.g., "people/collaborators/alice_smith")

        Returns:
            Metadata dictionary if found, None otherwise
        """
        summary_path = self._get_summary_path(entity_path)
        if summary_path.exists():
            meta, _ = parse_frontmatter(summary_path.read_text(encoding="utf-8"))
            if meta:
                return self._legacy_view(meta)

        # Read-only fallback for pre-frontmatter vaults.
        meta_path = self._get_meta_path(entity_path)
        if meta_path.exists():
            with meta_path.open(encoding="utf-8") as handle:
                return json.load(handle)
        return None

    def write_meta(self, entity_path: str, data: Dict[str, Any]) -> None:
        """Rewrite canonical frontmatter while preserving the Markdown body.

        Args:
            entity_path: Relative path to entity
            data: Metadata to write (must include required fields)

        Raises:
            ValueError: If required fields are missing
        """
        missing = []
        if "created" not in data:
            missing.append("created")
        if "updated" not in data and "last_updated" not in data:
            missing.append("updated")
        if "source" not in data and "sources" not in data:
            missing.append("source")
        if "aliases" not in data:
            missing.append("aliases")
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        summary_path = self._get_summary_path(entity_path)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        body = self.read_summary(entity_path) or ""
        summary_path.write_text(
            build_frontmatter(self._canonical_meta(data)) + body,
            encoding="utf-8",
        )
        meta_path = self._get_meta_path(entity_path)
        if meta_path.exists():
            meta_path.unlink()

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

        raw = summary_path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        return body if meta else raw

    def write_summary(self, entity_path: str, content: str) -> None:
        """Write _summary.md for an entity.

        Args:
            entity_path: Relative path to entity
            content: Markdown content to write
        """
        summary_path = self._get_summary_path(entity_path)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.read_meta(entity_path)
        if existing is None:
            now = datetime.now().strftime("%Y-%m-%d")
            existing = {
                "created": now,
                "updated": now,
                "source": "legacy:simple-storage",
                "aliases": [],
            }
        summary_path.write_text(
            build_frontmatter(self._canonical_meta(existing)) + content,
            encoding="utf-8",
        )

    def entity_exists(self, entity_path: str) -> bool:
        """Check if an entity directory contains ``_summary.md``.

        Args:
            entity_path: Relative path to entity

        Returns:
            True if entity exists
        """
        return self._get_summary_path(entity_path).is_file()

    def create_entity(
        self,
        entity_path: str,
        meta: Dict[str, Any],
        summary: str,
    ) -> Path:
        """Create a new entity as one frontmatter-backed ``_summary.md``.

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
        meta.setdefault("updated", meta.get("last_updated", now))
        if not meta.get("source"):
            sources = meta.get("sources")
            if isinstance(sources, list) and sources:
                meta["source"] = str(sources[0])
            elif sources:
                meta["source"] = str(sources)
            else:
                meta["source"] = "legacy:simple-storage"
        meta.setdefault("aliases", [])

        entity_dir = self._get_entity_path(entity_path)
        entity_dir.mkdir(parents=True, exist_ok=True)

        canonical = self._canonical_meta(meta)
        entity_dir.joinpath("_summary.md").write_text(
            build_frontmatter(canonical) + summary,
            encoding="utf-8",
        )

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
            if "sources" in meta and "source" not in meta:
                sources = meta["sources"]
                if isinstance(sources, list) and sources:
                    existing_meta["source"] = str(sources[0])
                elif sources:
                    existing_meta["source"] = str(sources)
            existing_meta["updated"] = datetime.now().strftime("%Y-%m-%d")
            self.write_meta(entity_path, existing_meta)

        if summary is not None:
            self.write_summary(entity_path, summary)

    def delete_entity(self, entity_path: str) -> None:
        """Delete entity directory.

        Args:
            entity_path: Relative path to entity
        """
        entity_dir = self._get_entity_path(entity_path)
        if not entity_dir.exists():
            return
        entity_dir = validate_node_target(self.kg_root, entity_path)
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
            if item.is_dir() and not item.is_symlink() and not item.name.startswith("_"):
                summary_path = item / "_summary.md"
                if summary_path.is_file() and not summary_path.is_symlink():
                    entities.append(str(item.relative_to(self.kg_root)))

        return sorted(entities)

    def list_all_entities(self) -> List[str]:
        """List all entity paths in the knowledge graph.

        Returns:
            List of all entity paths relative to kg_root
        """
        entities = []

        for summary_path in self.kg_root.rglob("_summary.md"):
            # Skip hidden directories and .kvault
            rel = summary_path.parent.relative_to(self.kg_root)
            if not rel.parts or any(part.startswith((".", "_")) for part in rel.parts):
                continue
            if rel.parts[0] == "journal":
                continue
            try:
                resolve_within_root(
                    self.kg_root,
                    summary_path.relative_to(self.kg_root),
                    allow_root=False,
                    must_exist=True,
                    reject_symlinks=True,
                )
            except PathSafetyError:
                continue
            entities.append(str(rel))

        return sorted(entities)

    def get_ancestors(self, entity_path: str) -> List[str]:
        """Return ancestor paths from entity to root.

        Args:
            entity_path: Relative path to entity

        Returns:
            List of ancestor paths (closest first, excluding the entity itself)
            Example: ["people/collaborators", "people"] for "people/collaborators/alice"
        """
        self._get_entity_path(entity_path)  # Validate containment and reserved namespaces.
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
            if item.is_dir() and not item.is_symlink() and not item.name.startswith("_"):
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
    kg_root = Path(kg_root).resolve()
    entities: List[EntityRecord] = []

    for summary_path in kg_root.rglob("_summary.md"):
        try:
            rel_summary = summary_path.relative_to(kg_root)
            summary_path = resolve_within_root(
                kg_root,
                rel_summary,
                allow_root=False,
                must_exist=True,
                reject_symlinks=True,
            )
        except (PathSafetyError, ValueError):
            continue
        if not summary_path.is_file() or summary_path.is_symlink():
            continue
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
            if meta_path.exists() and not meta_path.is_symlink():
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
