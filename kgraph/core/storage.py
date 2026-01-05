"""
Storage interface for kgraph.

Defines the abstract interface for knowledge graph storage and provides
a filesystem-based implementation.
"""

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import fcntl


class StorageInterface(ABC):
    """Abstract interface for knowledge graph storage."""

    @abstractmethod
    def write_entity(
        self, entity_type: str, entity_id: str, data: dict, tier: Optional[str] = None
    ) -> bool:
        """Write an entity to storage.

        Args:
            entity_type: Type of entity (e.g., "customer", "person")
            entity_id: Normalized entity identifier
            data: Entity data to store
            tier: Optional tier for tiered storage

        Returns:
            True if successful
        """
        pass

    @abstractmethod
    def read_entity(
        self, entity_type: str, entity_id: str, tier: Optional[str] = None
    ) -> Optional[dict]:
        """Read an entity from storage.

        Args:
            entity_type: Type of entity
            entity_id: Normalized entity identifier
            tier: Optional tier for tiered storage

        Returns:
            Entity data if found, None otherwise
        """
        pass

    @abstractmethod
    def merge_entities(
        self,
        source_data: dict,
        target_type: str,
        target_id: str,
        target_tier: Optional[str] = None,
    ) -> bool:
        """Merge source data into an existing entity.

        Args:
            source_data: Data to merge in
            target_type: Type of target entity
            target_id: ID of target entity
            target_tier: Tier of target entity

        Returns:
            True if successful
        """
        pass

    @abstractmethod
    def list_entities(
        self, entity_type: str, tier: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List all entities of a type.

        Args:
            entity_type: Type of entity
            tier: Optional tier filter

        Returns:
            List of entity summaries with at least {id, name, path}
        """
        pass

    @abstractmethod
    def entity_exists(
        self, entity_type: str, entity_id: str, tier: Optional[str] = None
    ) -> bool:
        """Check if an entity exists.

        Args:
            entity_type: Type of entity
            entity_id: Normalized entity identifier
            tier: Optional tier

        Returns:
            True if entity exists
        """
        pass

    @abstractmethod
    def append_to_registry(
        self, entity_type: str, tier: str, entry: dict
    ) -> bool:
        """Append an entry to a JSONL registry.

        Args:
            entity_type: Type of entity
            tier: Tier (registry location)
            entry: Entry to append

        Returns:
            True if successful
        """
        pass


def normalize_entity_id(name: str) -> str:
    """Convert entity name to a normalized ID.

    Rules:
    1. Lowercase
    2. Replace spaces with underscores
    3. Remove special characters except underscores
    4. Collapse multiple underscores

    Examples:
        "Mack Trucks" -> "mack_trucks"
        "R&L Carriers" -> "rl_carriers"
        "Universal Robots A/S" -> "universal_robots_as"
    """
    name = name.lower()
    # Convert underscores to spaces first for consistent handling
    name = name.replace("_", " ")
    # Remove special chars except alphanumeric and spaces
    name = re.sub(r"[^a-z0-9\s]", "", name)
    # Spaces to underscores
    name = re.sub(r"\s+", "_", name)
    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


class FilesystemStorage(StorageInterface):
    """Filesystem-based storage implementation.

    Stores entities as directories with:
    - _meta.json: Machine-readable metadata
    - _summary.md: Human-readable summary

    For JSONL tiers, stores entries in _registry.jsonl files.
    """

    def __init__(self, kg_path: Path, config: Optional["KGraphConfig"] = None):
        """Initialize filesystem storage.

        Args:
            kg_path: Root path of knowledge graph
            config: Optional KGraphConfig for tier/entity type info
        """
        self.kg_path = Path(kg_path)
        self.config = config

    def _get_entity_dir(
        self, entity_type: str, entity_id: str, tier: Optional[str] = None
    ) -> Path:
        """Get directory path for an entity."""
        if self.config:
            et_config = self.config.entity_types.get(entity_type)
            if et_config:
                base = self.kg_path / et_config.directory
            else:
                base = self.kg_path / entity_type
        else:
            base = self.kg_path / entity_type

        if tier:
            return base / tier / entity_id
        return base / entity_id

    def _get_registry_path(self, entity_type: str, tier: str) -> Path:
        """Get path to JSONL registry for a tier."""
        if self.config:
            et_config = self.config.entity_types.get(entity_type)
            if et_config:
                return self.kg_path / et_config.directory / tier / "_registry.jsonl"
        return self.kg_path / entity_type / tier / "_registry.jsonl"

    def _is_jsonl_tier(self, tier: Optional[str]) -> bool:
        """Check if tier uses JSONL storage."""
        if not tier or not self.config:
            return False
        tier_config = self.config.tiers.get(tier)
        return tier_config and tier_config.storage_type == "jsonl"

    def write_entity(
        self, entity_type: str, entity_id: str, data: dict, tier: Optional[str] = None
    ) -> bool:
        """Write an entity to the filesystem."""
        if self._is_jsonl_tier(tier):
            return self.append_to_registry(entity_type, tier, {**data, "dir": entity_id})

        entity_dir = self._get_entity_dir(entity_type, entity_id, tier)
        entity_dir.mkdir(parents=True, exist_ok=True)

        # Write _meta.json
        meta_path = entity_dir / "_meta.json"
        meta_data = {
            "topic": data.get("name", entity_id),
            "created": data.get("created", datetime.now().strftime("%Y-%m-%d")),
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
            "sources": data.get("sources", []),
            "parent": f"{entity_type}/{tier}" if tier else entity_type,
            "children": [],
            **{k: v for k, v in data.items() if k not in ["name", "created", "sources"]},
        }

        with open(meta_path, "w") as f:
            json.dump(meta_data, f, indent=2)
            f.write("\n")

        # Write _summary.md
        summary_path = entity_dir / "_summary.md"
        summary = self._generate_summary(data, entity_id)
        with open(summary_path, "w") as f:
            f.write(summary)

        return True

    def _generate_summary(self, data: dict, entity_id: str) -> str:
        """Generate a markdown summary for an entity."""
        name = data.get("name", entity_id.replace("_", " ").title())
        lines = [f"# {name}", ""]

        if data.get("description"):
            lines.extend([data["description"], ""])

        # Add key fields
        key_fields = ["industry", "tier", "status", "location", "annual_revenue"]
        field_lines = []
        for field in key_fields:
            if field in data and data[field]:
                label = field.replace("_", " ").title()
                field_lines.append(f"- **{label}**: {data[field]}")

        if field_lines:
            lines.extend(field_lines)
            lines.append("")

        # Add contacts
        if data.get("contacts"):
            lines.append("## Contacts")
            lines.append("")
            for contact in data["contacts"]:
                contact_name = contact.get("name", "Unknown")
                contact_email = contact.get("email", "")
                contact_role = contact.get("role", "")
                if contact_role:
                    lines.append(f"- **{contact_name}** ({contact_role}): {contact_email}")
                else:
                    lines.append(f"- **{contact_name}**: {contact_email}")
            lines.append("")

        # Add sources
        if data.get("sources"):
            lines.append("## Sources")
            lines.append("")
            for source in data["sources"]:
                lines.append(f"- {source}")
            lines.append("")

        return "\n".join(lines)

    def read_entity(
        self, entity_type: str, entity_id: str, tier: Optional[str] = None
    ) -> Optional[dict]:
        """Read an entity from the filesystem."""
        if self._is_jsonl_tier(tier):
            return self._read_from_registry(entity_type, tier, entity_id)

        entity_dir = self._get_entity_dir(entity_type, entity_id, tier)
        meta_path = entity_dir / "_meta.json"

        if not meta_path.exists():
            return None

        with open(meta_path) as f:
            return json.load(f)

    def _read_from_registry(
        self, entity_type: str, tier: str, entity_id: str
    ) -> Optional[dict]:
        """Read an entity from a JSONL registry."""
        registry_path = self._get_registry_path(entity_type, tier)
        if not registry_path.exists():
            return None

        with open(registry_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("dir") == entity_id:
                    return entry

        return None

    def merge_entities(
        self,
        source_data: dict,
        target_type: str,
        target_id: str,
        target_tier: Optional[str] = None,
    ) -> bool:
        """Merge source data into an existing entity."""
        if self._is_jsonl_tier(target_tier):
            return self._merge_registry_entry(source_data, target_type, target_tier, target_id)

        existing = self.read_entity(target_type, target_id, target_tier)
        if not existing:
            return False

        # Merge contacts (dedupe by email)
        existing_contacts = existing.get("contacts", [])
        existing_emails = {c.get("email") for c in existing_contacts if c.get("email")}

        for contact in source_data.get("contacts", []):
            if contact.get("email") and contact["email"] not in existing_emails:
                existing_contacts.append(contact)
                existing_emails.add(contact["email"])

        existing["contacts"] = existing_contacts

        # Merge sources
        existing_sources = set(existing.get("sources", []))
        existing_sources.update(source_data.get("sources", []))
        existing["sources"] = sorted(existing_sources)

        # Add source name as alias
        if source_data.get("name"):
            existing_aliases = existing.get("aliases", [])
            if source_data["name"] not in existing_aliases:
                existing_aliases.append(source_data["name"])
            existing["aliases"] = existing_aliases

        # Update timestamp
        existing["last_updated"] = datetime.now().strftime("%Y-%m-%d")

        return self.write_entity(target_type, target_id, existing, target_tier)

    def _merge_registry_entry(
        self, source_data: dict, entity_type: str, tier: str, target_id: str
    ) -> bool:
        """Merge into a JSONL registry entry."""
        registry_path = self._get_registry_path(entity_type, tier)
        if not registry_path.exists():
            return False

        # Read all entries
        entries = []
        target_found = False
        with open(registry_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("dir") == target_id:
                    # Merge into this entry
                    for key, value in source_data.items():
                        if key not in entry or not entry[key]:
                            entry[key] = value
                    entry["last_activity"] = datetime.now().strftime("%Y-W%W")
                    target_found = True
                entries.append(entry)

        if not target_found:
            return False

        # Write back with lock
        with open(registry_path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        return True

    def list_entities(
        self, entity_type: str, tier: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List all entities of a type."""
        results = []

        if self.config:
            et_config = self.config.entity_types.get(entity_type)
            base = self.kg_path / et_config.directory if et_config else self.kg_path / entity_type
        else:
            base = self.kg_path / entity_type

        if not base.exists():
            return results

        # Determine which tiers to scan
        if tier:
            tiers_to_scan = [tier]
        elif self.config and self.config.tiers:
            tiers_to_scan = list(self.config.tiers.keys())
        else:
            # No tiers configured, scan base directory
            tiers_to_scan = [None]

        for scan_tier in tiers_to_scan:
            scan_path = base / scan_tier if scan_tier else base

            if not scan_path.exists():
                continue

            # Check for JSONL registry
            if self._is_jsonl_tier(scan_tier):
                registry_path = scan_path / "_registry.jsonl"
                if registry_path.exists():
                    with open(registry_path) as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            entry = json.loads(line)
                            results.append({
                                "id": entry.get("dir"),
                                "name": entry.get("name"),
                                "tier": scan_tier,
                                "path": str(registry_path),
                                **entry,
                            })
            else:
                # Scan directories
                for entity_dir in scan_path.iterdir():
                    if not entity_dir.is_dir() or entity_dir.name.startswith("_"):
                        continue

                    meta_path = entity_dir / "_meta.json"
                    if meta_path.exists():
                        with open(meta_path) as f:
                            meta = json.load(f)
                        results.append({
                            "id": entity_dir.name,
                            "name": meta.get("topic", entity_dir.name),
                            "tier": scan_tier,
                            "path": str(entity_dir),
                            **meta,
                        })

        return results

    def entity_exists(
        self, entity_type: str, entity_id: str, tier: Optional[str] = None
    ) -> bool:
        """Check if an entity exists."""
        if self._is_jsonl_tier(tier):
            return self._read_from_registry(entity_type, tier, entity_id) is not None

        entity_dir = self._get_entity_dir(entity_type, entity_id, tier)
        return (entity_dir / "_meta.json").exists()

    def append_to_registry(
        self, entity_type: str, tier: str, entry: dict
    ) -> bool:
        """Append an entry to a JSONL registry."""
        registry_path = self._get_registry_path(entity_type, tier)
        registry_path.parent.mkdir(parents=True, exist_ok=True)

        # Add timestamp if not present
        if "last_activity" not in entry:
            entry["last_activity"] = datetime.now().strftime("%Y-W%W")

        with open(registry_path, "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(entry) + "\n")
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        return True
