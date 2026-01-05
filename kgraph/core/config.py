"""
Configuration system for kgraph.

Loads YAML configuration files and provides typed access to settings.
Uses Pydantic v2 for validation and immutable config objects.
"""

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfidenceConfig(BaseModel):
    """Confidence thresholds for auto-decisions."""

    model_config = ConfigDict(frozen=True)

    auto_merge: float = Field(default=0.95, ge=0.0, le=1.0, description="Score above which to auto-merge")
    auto_update: float = Field(default=0.90, ge=0.0, le=1.0, description="Score above which to auto-update")
    auto_create: float = Field(default=0.50, ge=0.0, le=1.0, description="Score below which to auto-create")
    llm_min: float = Field(default=0.50, ge=0.0, le=1.0, description="Min score requiring LLM decision")
    llm_max: float = Field(default=0.95, ge=0.0, le=1.0, description="Max score requiring LLM decision")

    @model_validator(mode="after")
    def validate_llm_range(self) -> "ConfidenceConfig":
        if self.llm_min >= self.llm_max:
            raise ValueError(f"llm_min ({self.llm_min}) must be < llm_max ({self.llm_max})")
        return self

    def requires_llm(self, score: float) -> bool:
        """Check if score falls in ambiguous range requiring LLM."""
        return self.llm_min <= score < self.llm_max


class MatchingConfig(BaseModel):
    """Configuration for entity matching."""

    model_config = ConfigDict(frozen=True)

    strategies: list[str] = Field(
        default=["alias", "fuzzy_name", "email_domain"],
        description="Matching strategies to use",
    )
    fuzzy_threshold: float = Field(default=0.85, ge=0.0, le=1.0, description="Minimum fuzzy match score")
    generic_domains: list[str] = Field(
        default_factory=lambda: [
            "gmail.com",
            "yahoo.com",
            "hotmail.com",
            "outlook.com",
            "aol.com",
            "icloud.com",
        ],
        description="Email domains to ignore in matching",
    )

    @field_validator("strategies")
    @classmethod
    def validate_strategies(cls, v: list[str]) -> list[str]:
        valid = {"alias", "fuzzy_name", "email_domain", "semantic"}
        invalid = set(v) - valid
        if invalid:
            raise ValueError(f"Invalid strategies: {invalid}. Valid: {valid}")
        return v


class TierConfig(BaseModel):
    """Configuration for an entity tier."""

    model_config = ConfigDict(frozen=True)

    name: str
    storage_type: Literal["directory", "jsonl"] = Field(
        default="directory",
        description="Storage format: 'directory' for full entity dirs, 'jsonl' for registry",
    )
    criteria: dict[str, Any] = Field(default_factory=dict, description="Criteria for tier matching")
    review_frequency: Optional[str] = Field(default=None, description="How often to review entities in this tier")

    def matches(self, entity_data: dict) -> bool:
        """Check if entity matches this tier's criteria."""
        for key, value in self.criteria.items():
            entity_value = entity_data.get(key)
            if entity_value is None:
                continue

            # Handle range criteria
            if key.endswith("_min"):
                base_key = key[:-4]
                if entity_data.get(base_key, 0) < value:
                    return False
            elif key.endswith("_max"):
                base_key = key[:-4]
                if entity_data.get(base_key, 0) > value:
                    return False
            # Handle exact match
            elif entity_value != value:
                return False

        return True


class FieldConfig(BaseModel):
    """Configuration for an entity field."""

    model_config = ConfigDict(frozen=True)

    type: Literal["string", "enum", "array", "object", "number", "boolean"] = Field(
        default="string",
        description="Field data type",
    )
    values: Optional[List[str]] = Field(default=None, description="Allowed values for enum type")
    required: bool = Field(default=False, description="Whether field is required")
    default: Any = Field(default=None, description="Default value")
    items: Optional[Dict[str, Any]] = Field(default=None, description="Schema for array items")
    properties: Optional[Dict[str, Any]] = Field(default=None, description="Schema for object properties")

    @model_validator(mode="after")
    def validate_enum_values(self) -> "FieldConfig":
        if self.type == "enum" and not self.values:
            raise ValueError("enum type requires 'values' list")
        return self


class EntityTypeConfig(BaseModel):
    """Configuration for an entity type (e.g., customer, supplier, person)."""

    model_config = ConfigDict(frozen=True)

    name: str
    directory: str
    tier_field: Optional[str] = Field(default=None, description="Field that determines tier")
    required_fields: list[str] = Field(default_factory=list, description="Required fields for this entity type")
    fields: dict[str, FieldConfig] = Field(default_factory=dict, description="Field schemas")


class AgentConfig(BaseModel):
    """Configuration for LLM agent."""

    model_config = ConfigDict(frozen=True)

    provider: Literal["claude", "openai", "local"] = Field(default="claude", description="LLM provider")
    model: Optional[str] = Field(default=None, description="Specific model to use")
    timeout: int = Field(default=120, gt=0, description="Timeout in seconds")


class ProcessingConfig(BaseModel):
    """Configuration for batch processing."""

    model_config = ConfigDict(frozen=True)

    batch_size: int = Field(default=500, gt=0, description="Items per batch")
    objective_interval: int = Field(default=5, gt=0, description="Batches between objective checks")
    max_pending_questions: int = Field(default=500, gt=0, description="Max questions in queue")


class KGraphConfig(BaseModel):
    """Central configuration object for a kgraph project."""

    model_config = ConfigDict(frozen=True)

    project_name: str = Field(description="Project name")
    data_path: Path = Field(description="Path to data directory")
    kg_path: Path = Field(description="Path to knowledge graph directory")

    # Entity configuration
    entity_types: dict[str, EntityTypeConfig] = Field(default_factory=dict, description="Entity type definitions")
    tiers: dict[str, TierConfig] = Field(default_factory=dict, description="Tier definitions")

    # Processing configuration
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    confidence: ConfidenceConfig = Field(default_factory=ConfidenceConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)

    # Paths
    prompts_path: Optional[Path] = Field(default=None, description="Path to prompts directory")
    aliases_path: Optional[Path] = Field(default=None, description="Path to entity aliases file")

    @classmethod
    def from_yaml(cls, path: Path) -> "KGraphConfig":
        """Load configuration from YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        return cls.from_dict(data, base_path=path.parent)

    @classmethod
    def from_dict(cls, data: dict, base_path: Optional[Path] = None) -> "KGraphConfig":
        """Create from dictionary with path resolution."""
        base_path = base_path or Path(".")
        project = data.get("project", {})

        # Parse confidence config, handling llm_required list format
        confidence_data = cls._parse_confidence(data.get("confidence", {}))

        # Build entity_types with name injected
        entity_types = {
            name: {"name": name, **et_data}
            for name, et_data in data.get("entity_types", {}).items()
        }

        # Build tiers with name injected
        tiers = {
            name: {"name": name, **tier_data}
            for name, tier_data in data.get("tiers", {}).items()
        }

        return cls.model_validate({
            "project_name": project.get("name", "Knowledge Graph"),
            "data_path": base_path / project.get("data_path", "data"),
            "kg_path": base_path / project.get("kg_path", "knowledge_graph"),
            "entity_types": entity_types,
            "tiers": tiers,
            "processing": data.get("processing", {}),
            "confidence": confidence_data,
            "matching": data.get("matching", {}),
            "agent": data.get("agent", {}),
            "prompts_path": base_path / project["prompts_path"] if project.get("prompts_path") else None,
            "aliases_path": base_path / project["aliases_path"] if project.get("aliases_path") else None,
        })

    @staticmethod
    def _parse_confidence(data: dict) -> dict:
        """Parse confidence config, handling llm_required list format."""
        result = {k: v for k, v in data.items() if k != "llm_required"}
        if "llm_required" in data and isinstance(data["llm_required"], list):
            result["llm_min"] = data["llm_required"][0]
            result["llm_max"] = data["llm_required"][1]
        return result

    def get_tier_for_entity(self, entity_data: dict) -> Optional[str]:
        """Determine which tier an entity belongs to based on criteria."""
        # Sort tiers by specificity (more criteria = more specific)
        sorted_tiers = sorted(
            self.tiers.items(), key=lambda x: len(x[1].criteria), reverse=True
        )

        for tier_name, tier_config in sorted_tiers:
            if tier_config.matches(entity_data):
                return tier_name

        return None

    def get_entity_path(self, entity_type: str, entity_id: str, tier: Optional[str] = None) -> Path:
        """Get the filesystem path for an entity."""
        et_config = self.entity_types.get(entity_type)
        if not et_config:
            raise ValueError(f"Unknown entity type: {entity_type}")

        base = self.kg_path / et_config.directory

        if tier and et_config.tier_field:
            tier_config = self.tiers.get(tier)
            if tier_config and tier_config.storage_type == "jsonl":
                # JSONL storage - return registry path
                return base / tier / "_registry.jsonl"
            elif tier_config:
                # Directory storage
                return base / tier / entity_id
        else:
            # No tiers for this entity type
            return base / entity_id

        return base / entity_id


def load_config(path: Optional[Path] = None) -> KGraphConfig:
    """Load configuration from file or use defaults.

    Searches for config in this order:
    1. Provided path
    2. ./kgraph.yaml
    3. ./config.yaml
    4. Default configuration
    """
    if path and path.exists():
        return KGraphConfig.from_yaml(path)

    # Search for config file
    for filename in ["kgraph.yaml", "config.yaml"]:
        config_path = Path(filename)
        if config_path.exists():
            return KGraphConfig.from_yaml(config_path)

    # Return default configuration
    return KGraphConfig(
        project_name="Knowledge Graph",
        data_path=Path("data"),
        kg_path=Path("knowledge_graph"),
    )
