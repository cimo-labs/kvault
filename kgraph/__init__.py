"""
kgraph - Config-driven knowledge graph framework

Build knowledge graphs from unstructured data (emails, documents, etc.)
using LLM-powered entity extraction and fuzzy deduplication.
"""

__version__ = "0.1.0"

from kgraph.core.config import KGraphConfig, load_config
from kgraph.core.storage import StorageInterface, FilesystemStorage

__all__ = [
    "KGraphConfig",
    "load_config",
    "StorageInterface",
    "FilesystemStorage",
]
