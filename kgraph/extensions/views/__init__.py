"""
View generation extensions for kgraph.

Provides classes for generating markdown views from knowledge graph data:
- ViewGenerator: Abstract base class
- DimensionalViewGenerator: Generate views organized by dimensions (tier, industry, etc.)

Usage:
    from kgraph.extensions.views import DimensionalViewGenerator, ViewConfig

    config = ViewConfig(
        dimensions=["tier", "industry"],
        dimension_values={
            "tier": ["strategic", "key", "standard"],
            "industry": ["robotics", "automotive", "medical"],
        },
    )

    generator = DimensionalViewGenerator(kg_root, config)
    generator.regenerate_affected([{"tier": "strategic", "industry": "robotics"}])
"""

from kgraph.extensions.views.base import ViewGenerator, ViewConfig, EntityScanner
from kgraph.extensions.views.dimensional import (
    DimensionalViewGenerator,
    DimensionSpec,
    create_tier_industry_generator,
)

__all__ = [
    "ViewGenerator",
    "ViewConfig",
    "EntityScanner",
    "DimensionalViewGenerator",
    "DimensionSpec",
    "create_tier_industry_generator",
]
