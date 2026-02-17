"""kvault MCP Server - Claude Code integration via Model Context Protocol."""

from kvault.mcp.manifest import TOOL_MANIFEST_VERSION, get_prefixed_tool_names, get_tool_manifest
from kvault.mcp.server import create_server, run_server

__all__ = [
    "TOOL_MANIFEST_VERSION",
    "get_tool_manifest",
    "get_prefixed_tool_names",
    "create_server",
    "run_server",
]
