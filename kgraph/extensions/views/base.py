"""
Base classes for view generation.

ViewGenerator provides the abstract interface for generating markdown views
from knowledge graph data.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ViewConfig:
    """Configuration for view generation.

    Attributes:
        dimensions: List of dimension names (e.g., ["tier", "industry"])
        dimension_values: Mapping of dimension name to valid values
        entity_types: Entity types to include in views (default: ["customer"])
        views_subdir: Subdirectory for views (default: "views")
        meta_filename: Metadata filename (default: "_meta.json")
        registry_filename: Registry filename for JSONL storage (default: "_registry.jsonl")
        template_fn: Optional custom template function
        normalizers: Optional dimension value normalizers
    """

    dimensions: List[str] = field(default_factory=lambda: ["tier"])
    dimension_values: Dict[str, List[str]] = field(default_factory=dict)
    entity_types: List[str] = field(default_factory=lambda: ["customer"])
    views_subdir: str = "views"
    meta_filename: str = "_meta.json"
    registry_filename: str = "_registry.jsonl"
    template_fn: Optional[Callable[[str, str, List[Dict]], str]] = None
    normalizers: Dict[str, Callable[[str], str]] = field(default_factory=dict)

    def get_normalizer(self, dimension: str) -> Callable[[str], str]:
        """Get normalizer for a dimension, or identity function."""
        return self.normalizers.get(dimension, lambda x: x)


class ViewGenerator(ABC):
    """Abstract base class for knowledge graph view generation.

    ViewGenerators transform knowledge graph entity data into markdown
    views organized by various dimensions (tier, industry, status, etc.).

    Subclasses must implement:
    - regenerate_affected(): Efficiently update only affected views
    - regenerate_all(): Full regeneration of all views

    Example:
        class MyViewGenerator(ViewGenerator):
            def regenerate_affected(self, entities):
                # Determine which views need updating
                for entity in entities:
                    tier = entity.get("tier")
                    if tier:
                        self._rebuild_tier_view(tier)

            def regenerate_all(self):
                for tier in ["strategic", "key", "standard"]:
                    self._rebuild_tier_view(tier)
    """

    def __init__(
        self,
        kg_root: Path,
        config: Optional[ViewConfig] = None,
    ):
        """
        Initialize view generator.

        Args:
            kg_root: Path to knowledge graph root directory
            config: Optional view configuration
        """
        self.kg_root = Path(kg_root)
        self.config = config or ViewConfig()
        self.views_path = self.kg_root / self.config.views_subdir

    @abstractmethod
    def regenerate_affected(self, entities: List[Dict[str, Any]]) -> int:
        """
        Regenerate only views affected by the given entities.

        This method should efficiently determine which views need updating
        based on the entity data (e.g., tier, industry) and only regenerate
        those specific views.

        Args:
            entities: List of entity dictionaries with dimension attributes

        Returns:
            Number of views regenerated
        """
        pass

    @abstractmethod
    def regenerate_all(self) -> int:
        """
        Full regeneration of all views.

        This method should rebuild all views from scratch by scanning
        the knowledge graph.

        Returns:
            Number of views regenerated
        """
        pass

    def ensure_views_directory(self) -> None:
        """Ensure the views directory exists."""
        self.views_path.mkdir(parents=True, exist_ok=True)

    def get_view_path(self, *parts: str) -> Path:
        """
        Get path to a view file.

        Args:
            *parts: Path components (e.g., "by_tier", "strategic.md")

        Returns:
            Full path to view file
        """
        return self.views_path.joinpath(*parts)

    def write_view(self, path: Path, content: str) -> None:
        """
        Write content to a view file.

        Args:
            path: Path to view file
            content: Markdown content to write
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def today(self) -> str:
        """Get today's date as ISO string."""
        return date.today().isoformat()


class EntityScanner:
    """Utility class for scanning entities from the knowledge graph.

    Provides methods to scan entities from directory structures and
    JSONL registry files.
    """

    def __init__(self, kg_root: Path, config: ViewConfig):
        """
        Initialize entity scanner.

        Args:
            kg_root: Knowledge graph root path
            config: View configuration
        """
        self.kg_root = Path(kg_root)
        self.config = config

    def scan_directory(
        self,
        rel_path: str,
        include_hidden: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Scan entity directories and load their metadata.

        Args:
            rel_path: Relative path from kg_root (e.g., "customers/strategic")
            include_hidden: Whether to include directories starting with _

        Returns:
            List of entity metadata dictionaries
        """
        import json

        full_path = self.kg_root / rel_path
        if not full_path.exists():
            return []

        entities = []
        for item in full_path.iterdir():
            if not item.is_dir():
                continue
            if not include_hidden and item.name.startswith("_"):
                continue

            meta_path = item / self.config.meta_filename
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    meta["_dir"] = item.name
                    meta["_path"] = str(item.relative_to(self.kg_root))
                    entities.append(meta)
                except json.JSONDecodeError:
                    continue

        return entities

    def scan_registry(self, rel_path: str) -> List[Dict[str, Any]]:
        """
        Scan entities from a JSONL registry file.

        Args:
            rel_path: Relative path to registry file

        Returns:
            List of entity dictionaries
        """
        import json

        full_path = self.kg_root / rel_path
        if not full_path.exists():
            return []

        entities = []
        for line in full_path.read_text().splitlines():
            if line.strip():
                try:
                    record = json.loads(line)
                    entities.append(record)
                except json.JSONDecodeError:
                    continue

        return entities

    def count_directory(self, rel_path: str) -> int:
        """
        Count entity directories in a path.

        Args:
            rel_path: Relative path from kg_root

        Returns:
            Number of entity directories
        """
        full_path = self.kg_root / rel_path
        if not full_path.exists():
            return 0

        count = 0
        for item in full_path.iterdir():
            if item.is_dir() and not item.name.startswith("_"):
                count += 1
        return count

    def count_registry(self, rel_path: str) -> int:
        """
        Count entries in a JSONL registry file.

        Args:
            rel_path: Relative path to registry file

        Returns:
            Number of entries
        """
        full_path = self.kg_root / rel_path
        if not full_path.exists():
            return 0

        count = 0
        for line in full_path.read_text().splitlines():
            if line.strip():
                count += 1
        return count
