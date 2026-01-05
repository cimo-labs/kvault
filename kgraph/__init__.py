"""
kgraph - Config-driven knowledge graph framework

Build knowledge graphs from unstructured data (emails, documents, etc.)
using LLM-powered entity extraction and fuzzy deduplication.
"""

__version__ = "0.1.0"

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
from kgraph.core.storage import StorageInterface, FilesystemStorage

__all__ = [
    # Config
    "KGraphConfig",
    "EntityTypeConfig",
    "TierConfig",
    "ConfidenceConfig",
    "MatchingConfig",
    "AgentConfig",
    "ProcessingConfig",
    "FieldConfig",
    "load_config",
    # Storage
    "StorageInterface",
    "FilesystemStorage",
]
