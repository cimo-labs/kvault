"""MCP compatibility server for kvault."""

from kvault.mcp.server import KVAULT_KB_ROOT_ENV, create_server, resolve_bound_root

__all__ = ["KVAULT_KB_ROOT_ENV", "create_server", "resolve_bound_root"]
