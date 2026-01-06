"""
kgraph.extensions - Optional extension modules for kgraph.

Extensions provide additional functionality beyond the core framework:
- Views: Generate markdown views of knowledge graph data
- Summaries: Auto-generate entity summaries
- Hierarchy: Parent-child relationship tracking

Extensions are optional - the core kgraph framework works without them.

Usage:
    from kgraph.extensions.views import ViewGenerator, DimensionalViewGenerator
"""

from kgraph.extensions.views import (
    ViewGenerator,
    ViewConfig,
    EntityScanner,
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
