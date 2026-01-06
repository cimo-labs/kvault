"""
kgraph.adapters - Format adapters for integrating with external systems.

Adapters bridge the gap between kgraph's internal data models and
external application formats.

Available adapters:
- ProtecEntityAdapter: Convert between ProTec and kgraph entity formats
- ProtecItemAdapter: Convert ProTec emails to kgraph items
"""

from kgraph.adapters.protec import ProtecEntityAdapter, ProtecItemAdapter

__all__ = [
    "ProtecEntityAdapter",
    "ProtecItemAdapter",
]
