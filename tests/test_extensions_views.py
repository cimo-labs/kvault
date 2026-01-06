"""Tests for the extensions.views module."""

import json
import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from kgraph.extensions.views import (
    ViewGenerator,
    ViewConfig,
    DimensionalViewGenerator,
)
from kgraph.extensions.views.base import EntityScanner
from kgraph.extensions.views.dimensional import DimensionSpec, create_tier_industry_generator


class TestViewConfig:
    """Tests for ViewConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = ViewConfig()

        assert config.dimensions == ["tier"]
        assert config.dimension_values == {}
        assert config.entity_types == ["customer"]
        assert config.views_subdir == "views"
        assert config.meta_filename == "_meta.json"

    def test_custom_config(self):
        """Test custom configuration."""
        config = ViewConfig(
            dimensions=["tier", "industry"],
            dimension_values={
                "tier": ["strategic", "key"],
                "industry": ["robotics", "automotive"],
            },
            views_subdir="custom_views",
        )

        assert config.dimensions == ["tier", "industry"]
        assert "tier" in config.dimension_values
        assert config.views_subdir == "custom_views"

    def test_normalizer(self):
        """Test dimension value normalizer."""
        normalizer = lambda x: x.lower().replace(" ", "_")
        config = ViewConfig(normalizers={"industry": normalizer})

        assert config.get_normalizer("industry")("Medical Devices") == "medical_devices"
        assert config.get_normalizer("tier")("strategic") == "strategic"  # Identity


class TestDimensionSpec:
    """Tests for DimensionSpec dataclass."""

    def test_basic_spec(self):
        """Test basic dimension specification."""
        spec = DimensionSpec(
            name="tier",
            values=["strategic", "key", "standard"],
        )

        assert spec.name == "tier"
        assert spec.get_field() == "tier"
        assert spec.get_subdir() == "by_tier"

    def test_custom_field(self):
        """Test custom entity field."""
        spec = DimensionSpec(
            name="tier",
            values=["strategic", "key"],
            entity_field="attributes.tier",
        )

        assert spec.get_field() == "attributes.tier"

    def test_custom_subdir(self):
        """Test custom view subdirectory."""
        spec = DimensionSpec(
            name="status",
            values=["active", "inactive"],
            view_subdir="status_views",
        )

        assert spec.get_subdir() == "status_views"

    def test_normalizer(self):
        """Test dimension value normalizer."""
        def normalize(value: str) -> str:
            return value.lower().replace("_", "-")

        spec = DimensionSpec(
            name="industry",
            values=["robotics", "automotive"],
            normalizer=normalize,
        )

        assert spec.normalize("ROBOTICS") == "robotics"
        assert spec.normalize("Machine_Tools") == "machine-tools"

    def test_sort_order(self):
        """Test custom sort order."""
        spec = DimensionSpec(
            name="tier",
            values=["strategic", "key", "standard"],
            sort_order={"strategic": 0, "key": 1, "standard": 2},
        )

        assert spec.get_sort_key("strategic") == 0
        assert spec.get_sort_key("key") == 1
        assert spec.get_sort_key("unknown") == 999

    def test_default_sort_by_values_order(self):
        """Test default sorting by values list order."""
        spec = DimensionSpec(
            name="tier",
            values=["strategic", "key", "standard"],
        )

        assert spec.get_sort_key("strategic") == 0
        assert spec.get_sort_key("key") == 1
        assert spec.get_sort_key("standard") == 2


class TestEntityScanner:
    """Tests for EntityScanner utility class."""

    def test_scan_directory(self):
        """Test scanning entity directories."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)
            config = ViewConfig()

            # Create entity directories
            (kg_root / "customers" / "strategic" / "acme_corp").mkdir(parents=True)
            (kg_root / "customers" / "strategic" / "acme_corp" / "_meta.json").write_text(
                json.dumps({"name": "Acme Corp", "industry": "robotics"})
            )

            (kg_root / "customers" / "strategic" / "beta_inc").mkdir(parents=True)
            (kg_root / "customers" / "strategic" / "beta_inc" / "_meta.json").write_text(
                json.dumps({"name": "Beta Inc", "industry": "automotive"})
            )

            scanner = EntityScanner(kg_root, config)
            entities = scanner.scan_directory("customers/strategic")

            assert len(entities) == 2
            names = {e["name"] for e in entities}
            assert "Acme Corp" in names
            assert "Beta Inc" in names

    def test_scan_directory_adds_path(self):
        """Test that scan adds _dir and _path to entities."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)
            config = ViewConfig()

            (kg_root / "customers" / "strategic" / "acme_corp").mkdir(parents=True)
            (kg_root / "customers" / "strategic" / "acme_corp" / "_meta.json").write_text(
                json.dumps({"name": "Acme Corp"})
            )

            scanner = EntityScanner(kg_root, config)
            entities = scanner.scan_directory("customers/strategic")

            assert len(entities) == 1
            assert entities[0]["_dir"] == "acme_corp"
            assert entities[0]["_path"] == "customers/strategic/acme_corp"

    def test_scan_registry(self):
        """Test scanning JSONL registry files."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)
            config = ViewConfig()

            # Create registry file
            (kg_root / "customers" / "prospects").mkdir(parents=True)
            registry = kg_root / "customers" / "prospects" / "_registry.jsonl"
            registry.write_text(
                '{"name": "Prospect A", "industry": "robotics"}\n'
                '{"name": "Prospect B", "industry": "medical"}\n'
            )

            scanner = EntityScanner(kg_root, config)
            entities = scanner.scan_registry("customers/prospects/_registry.jsonl")

            assert len(entities) == 2
            names = {e["name"] for e in entities}
            assert "Prospect A" in names
            assert "Prospect B" in names

    def test_count_directory(self):
        """Test counting entity directories."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)
            config = ViewConfig()

            # Create entity directories
            (kg_root / "customers" / "strategic" / "acme_corp").mkdir(parents=True)
            (kg_root / "customers" / "strategic" / "beta_inc").mkdir(parents=True)
            (kg_root / "customers" / "strategic" / "_hidden").mkdir(parents=True)

            scanner = EntityScanner(kg_root, config)
            count = scanner.count_directory("customers/strategic")

            assert count == 2  # Excludes _hidden

    def test_count_registry(self):
        """Test counting registry entries."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)
            config = ViewConfig()

            (kg_root / "prospects").mkdir(parents=True)
            registry = kg_root / "prospects" / "_registry.jsonl"
            registry.write_text('{"name": "A"}\n{"name": "B"}\n{"name": "C"}\n')

            scanner = EntityScanner(kg_root, config)
            count = scanner.count_registry("prospects/_registry.jsonl")

            assert count == 3


class TestDimensionalViewGenerator:
    """Tests for DimensionalViewGenerator."""

    def test_basic_view_generation(self):
        """Test generating a basic view."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)

            # Create entity
            (kg_root / "customers" / "strategic" / "acme_corp").mkdir(parents=True)
            (kg_root / "customers" / "strategic" / "acme_corp" / "_meta.json").write_text(
                json.dumps({
                    "name": "Acme Corp",
                    "tier": "strategic",
                    "industry": "robotics",
                })
            )

            # Create generator
            generator = DimensionalViewGenerator(
                kg_root=kg_root,
                dimensions=[
                    DimensionSpec(name="tier", values=["strategic", "key"]),
                ],
                entity_paths={"customer": ["customers/strategic", "customers/key"]},
            )

            # Generate view
            count = generator.regenerate_all()

            assert count == 2  # strategic.md and key.md

            # Check strategic view was created
            view_path = kg_root / "views" / "by_tier" / "strategic.md"
            assert view_path.exists()
            content = view_path.read_text()
            assert "Acme Corp" in content

    def test_regenerate_affected_single_dimension(self):
        """Test regenerating only affected views."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)

            # Create entities
            for tier in ["strategic", "key"]:
                (kg_root / "customers" / tier).mkdir(parents=True)

            (kg_root / "customers" / "strategic" / "acme").mkdir()
            (kg_root / "customers" / "strategic" / "acme" / "_meta.json").write_text(
                json.dumps({"name": "Acme", "tier": "strategic"})
            )

            generator = DimensionalViewGenerator(
                kg_root=kg_root,
                dimensions=[
                    DimensionSpec(name="tier", values=["strategic", "key"]),
                ],
                entity_paths={"customer": ["customers/strategic", "customers/key"]},
            )

            # Regenerate only affected
            count = generator.regenerate_affected([{"tier": "strategic"}])

            assert count == 1  # Only strategic

            # Only strategic view should exist
            assert (kg_root / "views" / "by_tier" / "strategic.md").exists()
            assert not (kg_root / "views" / "by_tier" / "key.md").exists()

    def test_multiple_dimensions(self):
        """Test view generation with multiple dimensions."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)

            # Create entity with multiple dimensions
            (kg_root / "customers" / "strategic" / "acme").mkdir(parents=True)
            (kg_root / "customers" / "strategic" / "acme" / "_meta.json").write_text(
                json.dumps({
                    "name": "Acme",
                    "tier": "strategic",
                    "industry": "robotics",
                })
            )

            generator = DimensionalViewGenerator(
                kg_root=kg_root,
                dimensions=[
                    DimensionSpec(name="tier", values=["strategic"]),
                    DimensionSpec(name="industry", values=["robotics", "automotive"]),
                ],
                entity_paths={"customer": ["customers/strategic"]},
            )

            # Regenerate affected
            count = generator.regenerate_affected([
                {"tier": "strategic", "industry": "robotics"}
            ])

            assert count == 2  # tier:strategic + industry:robotics

            assert (kg_root / "views" / "by_tier" / "strategic.md").exists()
            assert (kg_root / "views" / "by_industry" / "robotics.md").exists()

    def test_nested_entity_field(self):
        """Test accessing nested entity fields."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)

            # Create entity with nested attributes
            (kg_root / "customers" / "strategic" / "acme").mkdir(parents=True)
            (kg_root / "customers" / "strategic" / "acme" / "_meta.json").write_text(
                json.dumps({
                    "name": "Acme",
                    "attributes": {
                        "tier": "strategic",
                        "industry": "robotics",
                    },
                })
            )

            generator = DimensionalViewGenerator(
                kg_root=kg_root,
                dimensions=[
                    DimensionSpec(
                        name="tier",
                        values=["strategic"],
                        entity_field="attributes.tier",
                    ),
                ],
                entity_paths={"customer": ["customers/strategic"]},
            )

            count = generator.regenerate_affected([
                {"attributes": {"tier": "strategic"}}
            ])

            assert count == 1

    def test_custom_template(self):
        """Test custom template function."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)

            (kg_root / "customers" / "strategic" / "acme").mkdir(parents=True)
            (kg_root / "customers" / "strategic" / "acme" / "_meta.json").write_text(
                json.dumps({"name": "Acme", "tier": "strategic"})
            )

            def custom_template(dimension, value, entities):
                return f"# Custom View: {dimension}={value}\n\nEntities: {len(entities)}"

            generator = DimensionalViewGenerator(
                kg_root=kg_root,
                dimensions=[DimensionSpec(name="tier", values=["strategic"])],
                entity_paths={"customer": ["customers/strategic"]},
                template_fn=custom_template,
            )

            generator.regenerate_all()

            content = (kg_root / "views" / "by_tier" / "strategic.md").read_text()
            assert "Custom View: tier=strategic" in content
            assert "Entities: 1" in content

    def test_normalizer_in_regenerate_affected(self):
        """Test that normalizer is applied when matching entities."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)

            (kg_root / "customers" / "strategic" / "acme").mkdir(parents=True)
            (kg_root / "customers" / "strategic" / "acme" / "_meta.json").write_text(
                json.dumps({
                    "name": "Acme",
                    "industry": "Medical Devices",
                })
            )

            def normalizer(value: str) -> str:
                return value.lower().replace(" ", "_")

            generator = DimensionalViewGenerator(
                kg_root=kg_root,
                dimensions=[
                    DimensionSpec(
                        name="industry",
                        values=["medical_devices", "robotics"],
                        normalizer=normalizer,
                    ),
                ],
                entity_paths={"customer": ["customers/strategic"]},
            )

            # Input uses original case, should be normalized
            count = generator.regenerate_affected([{"industry": "Medical Devices"}])

            assert count == 1
            assert (kg_root / "views" / "by_industry" / "medical_devices.md").exists()


class TestCreateTierIndustryGenerator:
    """Tests for the factory function."""

    def test_creates_default_generator(self):
        """Test factory creates a working generator."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)

            generator = create_tier_industry_generator(kg_root)

            assert "tier" in generator.dimensions
            assert "industry" in generator.dimensions
            assert generator.dimensions["tier"].values == ["strategic", "key", "standard"]

    def test_custom_tiers(self):
        """Test factory with custom tiers."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)

            generator = create_tier_industry_generator(
                kg_root,
                tiers=["premium", "standard", "basic"],
            )

            assert generator.dimensions["tier"].values == ["premium", "standard", "basic"]

    def test_custom_industry_normalizer(self):
        """Test factory with custom industry normalizer."""
        with TemporaryDirectory() as tmpdir:
            kg_root = Path(tmpdir)

            def my_normalizer(value: str) -> str:
                return value.lower()

            generator = create_tier_industry_generator(
                kg_root,
                industry_normalizer=my_normalizer,
            )

            assert generator.dimensions["industry"].normalizer is my_normalizer
