"""
Dimensional view generator for kgraph.

Generates views organized by configurable dimensions like tier, industry,
status, region, etc.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from kgraph.extensions.views.base import ViewGenerator, ViewConfig, EntityScanner


@dataclass
class DimensionSpec:
    """Specification for a view dimension.

    Attributes:
        name: Dimension name (e.g., "tier", "industry")
        values: Valid values for this dimension
        entity_field: Field name in entity data (default: same as name)
        view_subdir: Subdirectory for dimension views (default: "by_{name}")
        normalizer: Optional value normalizer function
        sort_order: Optional custom sort order for values
    """

    name: str
    values: List[str]
    entity_field: Optional[str] = None
    view_subdir: Optional[str] = None
    normalizer: Optional[Callable[[str], str]] = None
    sort_order: Optional[Dict[str, int]] = None

    def get_field(self) -> str:
        """Get the entity field name for this dimension."""
        return self.entity_field or self.name

    def get_subdir(self) -> str:
        """Get the view subdirectory for this dimension."""
        return self.view_subdir or f"by_{self.name}"

    def normalize(self, value: str) -> str:
        """Normalize a value using the dimension's normalizer."""
        if self.normalizer and value:
            return self.normalizer(value)
        return value or ""

    def get_sort_key(self, value: str) -> int:
        """Get sort key for a value."""
        if self.sort_order:
            return self.sort_order.get(value, 999)
        try:
            return self.values.index(value)
        except ValueError:
            return 999


class DimensionalViewGenerator(ViewGenerator):
    """
    Generate views organized by configurable dimensions.

    This generator creates markdown views grouped by dimensions like
    tier, industry, status, etc. Each dimension gets its own subdirectory
    with one view file per dimension value.

    Example structure:
        views/
        ├── by_tier/
        │   ├── strategic.md
        │   ├── key.md
        │   └── standard.md
        └── by_industry/
            ├── robotics.md
            ├── automotive.md
            └── medical.md

    Usage:
        generator = DimensionalViewGenerator(
            kg_root=Path("/path/to/kg"),
            dimensions=[
                DimensionSpec(
                    name="tier",
                    values=["strategic", "key", "standard"],
                    sort_order={"strategic": 0, "key": 1, "standard": 2},
                ),
                DimensionSpec(
                    name="industry",
                    values=["robotics", "automotive", "medical"],
                    normalizer=normalize_industry,
                ),
            ],
            entity_paths={
                "customer": ["customers/strategic", "customers/key", "customers/standard"],
            },
        )

        # Regenerate views affected by an entity change
        generator.regenerate_affected([
            {"tier": "strategic", "industry": "robotics", "name": "Acme Corp"}
        ])

        # Full regeneration
        generator.regenerate_all()
    """

    def __init__(
        self,
        kg_root: Path,
        dimensions: List[DimensionSpec],
        entity_paths: Dict[str, List[str]],
        registry_paths: Optional[Dict[str, str]] = None,
        config: Optional[ViewConfig] = None,
        template_fn: Optional[Callable[[str, str, List[Dict]], str]] = None,
    ):
        """
        Initialize dimensional view generator.

        Args:
            kg_root: Path to knowledge graph root
            dimensions: List of dimension specifications
            entity_paths: Mapping of entity type to list of paths to scan
            registry_paths: Optional mapping of entity type to JSONL registry path
            config: Optional view configuration
            template_fn: Optional custom template function(dimension, value, entities) -> markdown
        """
        super().__init__(kg_root, config)

        self.dimensions = {d.name: d for d in dimensions}
        self.entity_paths = entity_paths
        self.registry_paths = registry_paths or {}
        self.template_fn = template_fn or self._default_template
        self.scanner = EntityScanner(kg_root, self.config)

    def regenerate_affected(self, entities: List[Dict[str, Any]]) -> int:
        """
        Regenerate only views affected by the given entities.

        Examines each entity's dimension values and regenerates
        only the corresponding views.

        Args:
            entities: List of entity dictionaries

        Returns:
            Number of views regenerated
        """
        affected: Dict[str, Set[str]] = {name: set() for name in self.dimensions}

        # Collect affected dimension values
        for entity in entities:
            for dim_name, dim_spec in self.dimensions.items():
                field = dim_spec.get_field()

                # Try to get value from entity, handling nested attributes
                value = self._get_entity_field(entity, field)
                if value:
                    normalized = dim_spec.normalize(value)
                    if normalized in dim_spec.values:
                        affected[dim_name].add(normalized)

        # Regenerate affected views
        count = 0
        for dim_name, values in affected.items():
            for value in values:
                self._regenerate_dimension_view(dim_name, value)
                count += 1

        return count

    def regenerate_all(self) -> int:
        """
        Full regeneration of all dimension views.

        Returns:
            Number of views regenerated
        """
        count = 0

        for dim_name, dim_spec in self.dimensions.items():
            for value in dim_spec.values:
                self._regenerate_dimension_view(dim_name, value)
                count += 1

        return count

    def regenerate_dimension(self, dimension: str) -> int:
        """
        Regenerate all views for a specific dimension.

        Args:
            dimension: Dimension name (e.g., "tier")

        Returns:
            Number of views regenerated
        """
        if dimension not in self.dimensions:
            raise ValueError(f"Unknown dimension: {dimension}")

        dim_spec = self.dimensions[dimension]
        count = 0

        for value in dim_spec.values:
            self._regenerate_dimension_view(dimension, value)
            count += 1

        return count

    def _regenerate_dimension_view(self, dimension: str, value: str) -> None:
        """
        Regenerate a single dimension view.

        Args:
            dimension: Dimension name
            value: Dimension value
        """
        dim_spec = self.dimensions[dimension]
        view_path = self.get_view_path(dim_spec.get_subdir(), f"{value}.md")

        # Collect entities matching this dimension value
        entities = self._collect_entities_for_dimension(dimension, value)

        # Generate content
        content = self.template_fn(dimension, value, entities)

        # Write view
        self.write_view(view_path, content)

    def _collect_entities_for_dimension(
        self,
        dimension: str,
        value: str,
    ) -> List[Dict[str, Any]]:
        """
        Collect all entities matching a dimension value.

        Args:
            dimension: Dimension name
            value: Dimension value to match

        Returns:
            List of matching entity dictionaries
        """
        dim_spec = self.dimensions[dimension]
        field = dim_spec.get_field()
        entities = []

        # Scan directory-based entities
        for entity_type, paths in self.entity_paths.items():
            for path in paths:
                scanned = self.scanner.scan_directory(path)
                for entity in scanned:
                    entity_value = self._get_entity_field(entity, field)
                    if entity_value:
                        normalized = dim_spec.normalize(entity_value)
                        if normalized == value:
                            entity["_entity_type"] = entity_type
                            entities.append(entity)

        # Scan registry-based entities
        for entity_type, registry_path in self.registry_paths.items():
            scanned = self.scanner.scan_registry(registry_path)
            for entity in scanned:
                entity_value = self._get_entity_field(entity, field)
                if entity_value:
                    normalized = dim_spec.normalize(entity_value)
                    if normalized == value:
                        entity["_entity_type"] = entity_type
                        entities.append(entity)

        # Sort entities
        entities.sort(key=lambda e: self._entity_sort_key(e, dimension))

        return entities

    def _get_entity_field(self, entity: Dict[str, Any], field: str) -> Optional[str]:
        """
        Get a field value from an entity, handling nested paths.

        Supports:
        - Direct field: "tier" -> entity["tier"]
        - Nested field: "attributes.tier" -> entity["attributes"]["tier"]

        Args:
            entity: Entity dictionary
            field: Field name or dot-separated path

        Returns:
            Field value or None
        """
        if "." in field:
            parts = field.split(".")
            value = entity
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    return None
            return value
        return entity.get(field)

    def _entity_sort_key(
        self,
        entity: Dict[str, Any],
        primary_dimension: str,
    ) -> tuple:
        """
        Generate a sort key for an entity.

        Sorts by:
        1. Primary dimension value order
        2. Entity name (case-insensitive)

        Args:
            entity: Entity dictionary
            primary_dimension: Primary dimension being viewed

        Returns:
            Tuple sort key
        """
        dim_spec = self.dimensions[primary_dimension]
        field = dim_spec.get_field()
        value = self._get_entity_field(entity, field) or ""
        normalized = dim_spec.normalize(value)

        name = entity.get("name") or entity.get("topic") or ""

        return (dim_spec.get_sort_key(normalized), name.lower())

    def _default_template(
        self,
        dimension: str,
        value: str,
        entities: List[Dict[str, Any]],
    ) -> str:
        """
        Default template for generating dimension views.

        Args:
            dimension: Dimension name
            value: Dimension value
            entities: List of matching entities

        Returns:
            Markdown content
        """
        lines = [
            f"# {value.title()} {dimension.title()}",
            "",
            f"**Last Updated:** {self.today()}",
            f"**Count:** {len(entities)}",
            "",
            "---",
            "",
            "| Name | Type | Details |",
            "|------|------|---------|",
        ]

        for entity in entities:
            name = entity.get("name") or entity.get("topic") or "Unknown"
            entity_type = entity.get("_entity_type", "entity")
            path = entity.get("_path", "")

            # Get some details
            details_parts = []
            if entity.get("industry"):
                details_parts.append(entity["industry"])
            if entity.get("status"):
                details_parts.append(entity["status"])
            details = ", ".join(details_parts) or "-"

            lines.append(f"| **{name}** | {entity_type} | {details} |")

        lines.extend([
            "",
            "---",
            "",
            f"*Generated view for {dimension}={value}*",
        ])

        return "\n".join(lines)


def create_tier_industry_generator(
    kg_root: Path,
    tiers: Optional[List[str]] = None,
    industries: Optional[List[str]] = None,
    industry_normalizer: Optional[Callable[[str], str]] = None,
) -> DimensionalViewGenerator:
    """
    Factory function to create a typical tier/industry view generator.

    Args:
        kg_root: Knowledge graph root path
        tiers: List of tier values (default: strategic, key, standard)
        industries: List of industry values
        industry_normalizer: Optional industry value normalizer

    Returns:
        Configured DimensionalViewGenerator
    """
    tiers = tiers or ["strategic", "key", "standard"]
    industries = industries or ["robotics", "automotive", "medical", "industrial"]

    dimensions = [
        DimensionSpec(
            name="tier",
            values=tiers,
            sort_order={t: i for i, t in enumerate(tiers)},
        ),
    ]

    if industries:
        dimensions.append(
            DimensionSpec(
                name="industry",
                values=industries,
                normalizer=industry_normalizer,
            )
        )

    # Default entity paths for customers
    entity_paths = {
        "customer": [f"customers/{tier}" for tier in tiers],
    }

    # Default registry path for prospects
    registry_paths = {
        "prospect": "customers/prospects/_registry.jsonl",
    }

    return DimensionalViewGenerator(
        kg_root=kg_root,
        dimensions=dimensions,
        entity_paths=entity_paths,
        registry_paths=registry_paths,
    )
