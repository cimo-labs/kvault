"""Core abstractions for kgraph."""

from kgraph.core.config import (
    KGraphConfig,
    EntityTypeConfig,
    TierConfig,
    ConfidenceConfig,
    MatchingConfig,
    AgentConfig,
    ProcessingConfig,
    FieldConfig,
    load_config,
)
from kgraph.core.storage import StorageInterface, FilesystemStorage, normalize_entity_id

__all__ = [
    "KGraphConfig",
    "EntityTypeConfig",
    "TierConfig",
    "ConfidenceConfig",
    "MatchingConfig",
    "AgentConfig",
    "ProcessingConfig",
    "FieldConfig",
    "load_config",
    "StorageInterface",
    "FilesystemStorage",
    "normalize_entity_id",
]
