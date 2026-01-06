"""Tests for config hierarchy system."""

import os
import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from kgraph.core.config import (
    load_config,
    _extract_env_config,
    _convert_env_value,
    _deep_merge,
    _find_config_file,
    KGraphConfig,
)


class TestConvertEnvValue:
    """Tests for environment value type conversion."""

    def test_convert_integer(self):
        """Test integer conversion."""
        assert _convert_env_value("100") == 100
        assert _convert_env_value("-42") == -42
        assert _convert_env_value("0") == False  # 0 is treated as boolean

    def test_convert_float(self):
        """Test float conversion."""
        assert _convert_env_value("3.14") == 3.14
        assert _convert_env_value("0.85") == 0.85
        assert _convert_env_value("-2.5") == -2.5

    def test_convert_boolean_true(self):
        """Test boolean true conversion."""
        for value in ["true", "True", "TRUE", "yes", "YES", "1", "on", "ON"]:
            assert _convert_env_value(value) is True

    def test_convert_boolean_false(self):
        """Test boolean false conversion."""
        for value in ["false", "False", "FALSE", "no", "NO", "0", "off", "OFF"]:
            assert _convert_env_value(value) is False

    def test_convert_list(self):
        """Test comma-separated list conversion."""
        assert _convert_env_value("a,b,c") == ["a", "b", "c"]
        assert _convert_env_value("alias, fuzzy_name, email_domain") == [
            "alias",
            "fuzzy_name",
            "email_domain",
        ]
        assert _convert_env_value("one, , two") == ["one", "two"]  # Empty items removed

    def test_convert_string(self):
        """Test string passthrough."""
        assert _convert_env_value("hello") == "hello"
        assert _convert_env_value("My Project") == "My Project"

    def test_empty_string(self):
        """Test empty string passthrough."""
        assert _convert_env_value("") == ""


class TestDeepMerge:
    """Tests for deep dictionary merging."""

    def test_simple_merge(self):
        """Test simple key merging."""
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        _deep_merge(base, override)

        assert base == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        """Test nested dictionary merging."""
        base = {"a": {"b": 1, "c": 2}, "d": 3}
        override = {"a": {"b": 10}, "e": 5}
        _deep_merge(base, override)

        assert base == {"a": {"b": 10, "c": 2}, "d": 3, "e": 5}

    def test_deeply_nested_merge(self):
        """Test deeply nested merging."""
        base = {"l1": {"l2": {"l3": {"a": 1, "b": 2}}}}
        override = {"l1": {"l2": {"l3": {"b": 20, "c": 30}}}}
        _deep_merge(base, override)

        assert base == {"l1": {"l2": {"l3": {"a": 1, "b": 20, "c": 30}}}}

    def test_override_dict_with_scalar(self):
        """Test that scalar overrides dict completely."""
        base = {"a": {"b": 1}}
        override = {"a": "replaced"}
        _deep_merge(base, override)

        assert base == {"a": "replaced"}

    def test_override_scalar_with_dict(self):
        """Test that dict overrides scalar completely."""
        base = {"a": "old"}
        override = {"a": {"b": 1}}
        _deep_merge(base, override)

        assert base == {"a": {"b": 1}}

    def test_empty_override(self):
        """Test merging empty override."""
        base = {"a": 1}
        _deep_merge(base, {})

        assert base == {"a": 1}

    def test_empty_base(self):
        """Test merging into empty base."""
        base = {}
        _deep_merge(base, {"a": 1, "b": {"c": 2}})

        assert base == {"a": 1, "b": {"c": 2}}


class TestExtractEnvConfig:
    """Tests for environment variable extraction."""

    def test_extract_processing_config(self, monkeypatch):
        """Test extracting processing config from env."""
        monkeypatch.setenv("KGRAPH_PROCESSING_BATCH_SIZE", "200")
        monkeypatch.setenv("KGRAPH_PROCESSING_MAX_PENDING_QUESTIONS", "1000")

        config = _extract_env_config("KGRAPH_")

        assert config == {
            "processing": {
                "batch_size": 200,
                "max_pending_questions": 1000,
            }
        }

    def test_extract_confidence_config(self, monkeypatch):
        """Test extracting confidence config from env."""
        monkeypatch.setenv("KGRAPH_CONFIDENCE_AUTO_MERGE", "0.9")
        monkeypatch.setenv("KGRAPH_CONFIDENCE_AUTO_CREATE", "0.6")

        config = _extract_env_config("KGRAPH_")

        assert config == {
            "confidence": {
                "auto_merge": 0.9,
                "auto_create": 0.6,
            }
        }

    def test_extract_matching_config(self, monkeypatch):
        """Test extracting matching config with list."""
        monkeypatch.setenv("KGRAPH_MATCHING_STRATEGIES", "alias,fuzzy_name")
        monkeypatch.setenv("KGRAPH_MATCHING_FUZZY_THRESHOLD", "0.85")

        config = _extract_env_config("KGRAPH_")

        assert config == {
            "matching": {
                "strategies": ["alias", "fuzzy_name"],
                "fuzzy_threshold": 0.85,
            }
        }

    def test_extract_top_level_config(self, monkeypatch):
        """Test extracting top-level config."""
        monkeypatch.setenv("KGRAPH_PROJECT_NAME", "My Project")

        config = _extract_env_config("KGRAPH_")

        # PROJECT_NAME maps to project.name section
        assert config.get("project", {}).get("name") == "My Project"

    def test_custom_prefix(self, monkeypatch):
        """Test custom environment variable prefix."""
        monkeypatch.setenv("MYAPP_PROCESSING_BATCH_SIZE", "300")

        config = _extract_env_config("MYAPP_")

        assert config == {"processing": {"batch_size": 300}}

    def test_ignores_other_env_vars(self, monkeypatch):
        """Test that non-matching vars are ignored."""
        monkeypatch.setenv("OTHER_VAR", "ignored")
        monkeypatch.setenv("KGRAPH_PROCESSING_BATCH_SIZE", "100")

        config = _extract_env_config("KGRAPH_")

        assert "other_var" not in config
        assert config == {"processing": {"batch_size": 100}}


class TestFindConfigFile:
    """Tests for config file discovery."""

    def test_explicit_path(self):
        """Test finding explicit path."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "custom.yaml"
            config_path.write_text("project:\n  name: Test")

            found = _find_config_file(config_path)
            assert found == config_path

    def test_explicit_path_not_found(self):
        """Test explicit path that doesn't exist."""
        found = _find_config_file(Path("/nonexistent/config.yaml"))
        assert found is None

    def test_no_config_file(self):
        """Test when no config file exists."""
        with TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            found = _find_config_file()
            assert found is None


class TestLoadConfigHierarchy:
    """Tests for full config loading hierarchy."""

    def test_load_with_defaults_only(self):
        """Test loading with no config file."""
        with TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            config = load_config(use_env=False)

            assert config.project_name == "Knowledge Graph"
            assert config.processing.batch_size == 500  # Pydantic default

    def test_load_yaml_overrides_defaults(self):
        """Test that YAML overrides Pydantic defaults."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "kgraph.yaml"
            config_path.write_text("""
project:
  name: YAML Project
processing:
  batch_size: 100
""")
            config = load_config(path=config_path, use_env=False)

            assert config.project_name == "YAML Project"
            assert config.processing.batch_size == 100

    def test_env_overrides_yaml(self, monkeypatch):
        """Test that env vars override YAML."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "kgraph.yaml"
            config_path.write_text("""
project:
  name: YAML Project
processing:
  batch_size: 100
""")
            monkeypatch.setenv("KGRAPH_PROCESSING_BATCH_SIZE", "200")

            config = load_config(path=config_path)

            assert config.project_name == "YAML Project"  # From YAML
            assert config.processing.batch_size == 200  # Overridden by env

    def test_cli_overrides_env(self, monkeypatch):
        """Test that CLI args override env vars."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "kgraph.yaml"
            config_path.write_text("""
project:
  name: YAML Project
processing:
  batch_size: 100
""")
            monkeypatch.setenv("KGRAPH_PROCESSING_BATCH_SIZE", "200")

            config = load_config(
                path=config_path,
                cli_overrides={"processing": {"batch_size": 300}},
            )

            assert config.processing.batch_size == 300  # CLI wins

    def test_full_hierarchy(self, monkeypatch):
        """Test full hierarchy: defaults → YAML → env → CLI."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "kgraph.yaml"
            config_path.write_text("""
project:
  name: YAML Project
processing:
  batch_size: 100
  objective_interval: 10
confidence:
  auto_merge: 0.9
""")
            # Env overrides some values
            monkeypatch.setenv("KGRAPH_PROCESSING_BATCH_SIZE", "200")
            monkeypatch.setenv("KGRAPH_CONFIDENCE_AUTO_CREATE", "0.7")

            # CLI overrides batch_size again
            config = load_config(
                path=config_path,
                cli_overrides={
                    "processing": {"batch_size": 300},
                },
            )

            # CLI overrides env
            assert config.processing.batch_size == 300
            # YAML value (not overridden)
            assert config.processing.objective_interval == 10
            # Env override
            assert config.confidence.auto_create == 0.7
            # YAML value
            assert config.confidence.auto_merge == 0.9
            # Default (max_pending_questions not in YAML/env/CLI)
            assert config.processing.max_pending_questions == 500

    def test_disable_env_vars(self, monkeypatch):
        """Test that use_env=False disables env var loading."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "kgraph.yaml"
            config_path.write_text("""
project:
  name: YAML Project
processing:
  batch_size: 100
""")
            monkeypatch.setenv("KGRAPH_PROCESSING_BATCH_SIZE", "999")

            config = load_config(path=config_path, use_env=False)

            assert config.processing.batch_size == 100  # Env var ignored

    def test_nested_cli_override(self):
        """Test deeply nested CLI overrides."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "kgraph.yaml"
            config_path.write_text("""
project:
  name: Test
matching:
  strategies:
    - alias
    - fuzzy_name
  fuzzy_threshold: 0.85
""")
            config = load_config(
                path=config_path,
                use_env=False,
                cli_overrides={
                    "matching": {
                        "fuzzy_threshold": 0.9,
                    }
                },
            )

            assert config.matching.fuzzy_threshold == 0.9
            # Strategies preserved from YAML
            assert config.matching.strategies == ["alias", "fuzzy_name"]


class TestLoadConfigEdgeCases:
    """Edge case tests for config loading."""

    def test_empty_yaml_file(self):
        """Test handling empty YAML file."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "kgraph.yaml"
            config_path.write_text("")

            config = load_config(path=config_path, use_env=False)

            # Should return defaults
            assert config.project_name == "Knowledge Graph"

    def test_yaml_only_comments(self):
        """Test YAML file with only comments."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "kgraph.yaml"
            config_path.write_text("# Just a comment\n# Another comment")

            config = load_config(path=config_path, use_env=False)

            # Should return defaults
            assert config.project_name == "Knowledge Graph"

    def test_custom_env_prefix(self, monkeypatch):
        """Test custom environment variable prefix."""
        monkeypatch.setenv("MYKG_PROCESSING_BATCH_SIZE", "999")

        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "kgraph.yaml"
            config_path.write_text("project:\n  name: Test")

            config = load_config(
                path=config_path,
                env_prefix="MYKG_",
            )

            assert config.processing.batch_size == 999
