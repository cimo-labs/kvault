"""Core abstractions for kgraph."""

from kgraph.core.config import (
    KGraphConfig,
    EntityTypeConfig,
    TierConfig,
    ConfidenceConfig,
    MatchingConfig,
    load_config,
)
from kgraph.core.storage import StorageInterface, FilesystemStorage

__all__ = [
    "KGraphConfig",
    "EntityTypeConfig",
    "TierConfig",
    "ConfidenceConfig",
    "MatchingConfig",
    "load_config",
    "StorageInterface",
    "FilesystemStorage",
]
