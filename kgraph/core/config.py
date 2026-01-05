"""
Configuration system for kgraph.

Loads YAML configuration files and provides typed access to settings.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


@dataclass
class ConfidenceConfig:
    """Confidence thresholds for auto-decisions."""

    auto_merge: float = 0.95  # Score above which to auto-merge
    auto_update: float = 0.90  # Score above which to auto-update
    auto_create: float = 0.50  # Score below which to auto-create
    llm_min: float = 0.50  # Min score requiring LLM decision
    llm_max: float = 0.95  # Max score requiring LLM decision

    def requires_llm(self, score: float) -> bool:
        """Check if score falls in ambiguous range requiring LLM."""
        return self.llm_min <= score < self.llm_max


@dataclass
class MatchingConfig:
    """Configuration for entity matching."""

    strategies: List[str] = field(default_factory=lambda: ["alias", "fuzzy_name", "email_domain"])
    fuzzy_threshold: float = 0.85
    generic_domains: List[str] = field(
        default_factory=lambda: [
            "gmail.com",
            "yahoo.com",
            "hotmail.com",
            "outlook.com",
            "aol.com",
            "icloud.com",
        ]
    )


@dataclass
class TierConfig:
    """Configuration for an entity tier."""

    name: str
    storage_type: str = "directory"  # "directory" or "jsonl"
    criteria: Dict[str, Any] = field(default_factory=dict)
    review_frequency: Optional[str] = None

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


@dataclass
class FieldConfig:
    """Configuration for an entity field."""

    type: str  # "string", "enum", "array", "object", "number", "boolean"
    values: Optional[List[str]] = None  # For enum types
    required: bool = False
    default: Any = None
    items: Optional[Dict[str, Any]] = None  # For array types
    properties: Optional[Dict[str, Any]] = None  # For object types


@dataclass
class EntityTypeConfig:
    """Configuration for an entity type (e.g., customer, supplier, person)."""

    name: str
    directory: str
    tier_field: Optional[str] = None  # Field that determines tier (e.g., "tier")
    required_fields: List[str] = field(default_factory=list)
    fields: Dict[str, FieldConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "EntityTypeConfig":
        """Create from dictionary (loaded from YAML)."""
        fields = {}
        for field_name, field_data in data.get("fields", {}).items():
            if isinstance(field_data, dict):
                fields[field_name] = FieldConfig(
                    type=field_data.get("type", "string"),
                    values=field_data.get("values"),
                    required=field_data.get("required", False),
                    default=field_data.get("default"),
                    items=field_data.get("items"),
                    properties=field_data.get("properties"),
                )
            else:
                fields[field_name] = FieldConfig(type="string")

        return cls(
            name=name,
            directory=data.get("directory", name + "s"),
            tier_field=data.get("tier_field"),
            required_fields=data.get("required_fields", []),
            fields=fields,
        )


@dataclass
class AgentConfig:
    """Configuration for LLM agent."""

    provider: str = "claude"  # "claude", "openai", "local"
    model: Optional[str] = None
    timeout: int = 120  # seconds


@dataclass
class ProcessingConfig:
    """Configuration for batch processing."""

    batch_size: int = 500
    objective_interval: int = 5  # Batches between objective checks
    max_pending_questions: int = 500


@dataclass
class KGraphConfig:
    """Central configuration object for a kgraph project."""

    project_name: str
    data_path: Path
    kg_path: Path

    # Entity configuration
    entity_types: Dict[str, EntityTypeConfig] = field(default_factory=dict)
    tiers: Dict[str, TierConfig] = field(default_factory=dict)

    # Processing configuration
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    confidence: ConfidenceConfig = field(default_factory=ConfidenceConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

    # Paths
    prompts_path: Optional[Path] = None
    aliases_path: Optional[Path] = None

    @classmethod
    def from_yaml(cls, path: Path) -> "KGraphConfig":
        """Load configuration from YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        return cls.from_dict(data, base_path=path.parent)

    @classmethod
    def from_dict(cls, data: dict, base_path: Optional[Path] = None) -> "KGraphConfig":
        """Create from dictionary."""
        base_path = base_path or Path(".")

        project = data.get("project", {})

        # Parse entity types
        entity_types = {}
        for name, et_data in data.get("entity_types", {}).items():
            entity_types[name] = EntityTypeConfig.from_dict(name, et_data)

        # Parse tiers
        tiers = {}
        for name, tier_data in data.get("tiers", {}).items():
            tiers[name] = TierConfig(
                name=name,
                storage_type=tier_data.get("storage_type", "directory"),
                criteria=tier_data.get("criteria", {}),
                review_frequency=tier_data.get("review_frequency"),
            )

        # Parse processing config
        proc_data = data.get("processing", {})
        processing = ProcessingConfig(
            batch_size=proc_data.get("batch_size", 500),
            objective_interval=proc_data.get("objective_interval", 5),
            max_pending_questions=proc_data.get("max_pending_questions", 500),
        )

        # Parse confidence config
        conf_data = data.get("confidence", {})
        confidence = ConfidenceConfig(
            auto_merge=conf_data.get("auto_merge", 0.95),
            auto_update=conf_data.get("auto_update", 0.90),
            auto_create=conf_data.get("auto_create", 0.50),
            llm_min=conf_data.get("llm_required", [0.50, 0.95])[0]
            if isinstance(conf_data.get("llm_required"), list)
            else 0.50,
            llm_max=conf_data.get("llm_required", [0.50, 0.95])[1]
            if isinstance(conf_data.get("llm_required"), list)
            else 0.95,
        )

        # Parse matching config
        match_data = data.get("matching", {})
        matching = MatchingConfig(
            strategies=match_data.get("strategies", ["alias", "fuzzy_name", "email_domain"]),
            fuzzy_threshold=match_data.get("fuzzy_threshold", 0.85),
            generic_domains=match_data.get("generic_domains", []),
        )

        # Parse agent config
        agent_data = data.get("agent", {})
        agent = AgentConfig(
            provider=agent_data.get("provider", "claude"),
            model=agent_data.get("model"),
            timeout=agent_data.get("timeout", 120),
        )

        return cls(
            project_name=project.get("name", "Knowledge Graph"),
            data_path=base_path / project.get("data_path", "data"),
            kg_path=base_path / project.get("kg_path", "knowledge_graph"),
            entity_types=entity_types,
            tiers=tiers,
            processing=processing,
            confidence=confidence,
            matching=matching,
            agent=agent,
            prompts_path=base_path / project.get("prompts_path", "prompts")
            if project.get("prompts_path")
            else None,
            aliases_path=base_path / project.get("aliases_path", "data/entity_aliases.json")
            if project.get("aliases_path")
            else None,
        )

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
