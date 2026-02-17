"""Canonical MCP tool manifest for kvault.

This file is the single source of truth for tool names, schemas, and workflow
categories exposed by the kvault MCP server.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

TOOL_MANIFEST_VERSION = "2026-02-17"

_TOOL_MANIFEST: List[Dict[str, Any]] = [
    {
        "name": "kvault_init",
        "category": "init",
        "stage": "init",
        "description": "Initialize kvault and return context (hierarchy, root summary, entity count). Call this first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kg_root": {
                    "type": "string",
                    "description": "Path to knowledge graph root directory",
                },
            },
            "required": ["kg_root"],
        },
    },
    {
        "name": "kvault_status",
        "category": "status",
        "stage": "status",
        "description": "Return server health/workflow state and canonical tool manifest metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Optional workflow session id to inspect in detail",
                },
                "tool_prefix": {
                    "type": "string",
                    "description": "Optional namespace prefix (e.g., 'personal') to return prefixed names",
                },
            },
        },
    },
    {
        "name": "kvault_log_phase",
        "category": "workflow",
        "stage": "log",
        "description": "Write a structured observability phase log entry into .kvault/logs.db.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phase": {"type": "string", "description": "Log phase name"},
                "data": {
                    "type": "object",
                    "description": "Structured phase payload to store",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional workflow session id for traceability",
                },
            },
            "required": ["phase"],
        },
    },
    {
        "name": "kvault_read_entity",
        "category": "entity",
        "stage": "research",
        "description": "Read entity with YAML frontmatter. Returns meta, content, and parent summary (sibling context).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Entity path (e.g., 'people/contacts/john_doe')",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "kvault_write_entity",
        "category": "entity",
        "stage": "execute",
        "description": "Write entity with YAML frontmatter. Returns ancestor summaries for propagation and optional auto-journal info.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Entity path"},
                "meta": {
                    "type": "object",
                    "description": "Optional frontmatter metadata. If omitted, kvault reuses existing meta or applies safe defaults.",
                },
                "content": {
                    "type": "string",
                    "description": "Markdown content (without frontmatter)",
                },
                "create": {
                    "type": "boolean",
                    "description": "True to create new, False to update",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why this entity is being created/updated. If provided, auto-logs a journal entry.",
                },
                "journal_source": {
                    "type": "string",
                    "description": "Source for journal entry (defaults to meta.source)",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session ID for workflow tracking",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "kvault_list_entities",
        "category": "entity",
        "stage": "research",
        "description": "List entities, optionally filtered by category.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Optional category filter"},
            },
        },
    },
    {
        "name": "kvault_delete_entity",
        "category": "entity",
        "stage": "execute",
        "description": "Delete an entity. WARNING: This is destructive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Entity path to delete"},
                "session_id": {
                    "type": "string",
                    "description": "Session ID for workflow tracking",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "kvault_move_entity",
        "category": "entity",
        "stage": "execute",
        "description": "Move an entity to a new path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Current entity path"},
                "target_path": {"type": "string", "description": "New entity path"},
                "session_id": {
                    "type": "string",
                    "description": "Session ID for workflow tracking",
                },
            },
            "required": ["source_path", "target_path"],
        },
    },
    {
        "name": "kvault_read_summary",
        "category": "summary",
        "stage": "research",
        "description": "Read a summary file (_summary.md) from a path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to directory containing _summary.md",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "kvault_write_summary",
        "category": "summary",
        "stage": "propagate",
        "description": "Write a summary file. Used for category summaries and propagation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to directory for _summary.md",
                },
                "content": {"type": "string", "description": "Markdown content"},
                "meta": {"type": "object", "description": "Optional frontmatter"},
                "session_id": {
                    "type": "string",
                    "description": "Session ID for workflow tracking",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "kvault_update_summaries",
        "category": "summary",
        "stage": "propagate",
        "description": "Batch-update multiple ancestor summaries in one call. Use after kvault_write_entity to propagate changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to directory for _summary.md",
                            },
                            "content": {
                                "type": "string",
                                "description": "Updated markdown content",
                            },
                            "meta": {
                                "type": "object",
                                "description": "Optional frontmatter",
                            },
                        },
                        "required": ["path", "content"],
                    },
                    "description": "List of summary updates",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session ID for workflow tracking",
                },
            },
            "required": ["updates"],
        },
    },
    {
        "name": "kvault_get_parent_summaries",
        "category": "summary",
        "stage": "propagate",
        "description": "Get ancestor summaries for propagation. Returns parent -> root summaries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Entity or category path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "kvault_write_journal",
        "category": "workflow",
        "stage": "log",
        "description": "Write a journal entry for actions taken.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action_type": {"type": "string"},
                            "path": {"type": "string"},
                            "reasoning": {"type": "string"},
                        },
                    },
                    "description": "List of actions taken",
                },
                "source": {"type": "string", "description": "Source identifier"},
                "date": {"type": "string", "description": "Optional date (YYYY-MM-DD)"},
                "session_id": {
                    "type": "string",
                    "description": "Session ID for workflow tracking",
                },
            },
            "required": ["actions", "source"],
        },
    },
    {
        "name": "kvault_propagate_all",
        "category": "summary",
        "stage": "propagate",
        "description": "Get all ancestor summaries for propagation. Returns ancestors with current content for agents to update.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Entity or category path to propagate from",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session ID for workflow tracking",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "kvault_generate_daily_artifact",
        "category": "artifacts",
        "stage": "artifact",
        "description": "Generate a daily summary artifact from root/people/projects summaries and recent journal entries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Optional artifact date (YYYY-MM-DD). Defaults to today.",
                },
                "force": {
                    "type": "boolean",
                    "description": "Overwrite existing artifact for the same date.",
                },
            },
        },
    },
    {
        "name": "kvault_validate_kb",
        "category": "validation",
        "stage": "validate",
        "description": "Check KB integrity: incomplete entities, missing frontmatter.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


def get_tool_manifest() -> List[Dict[str, Any]]:
    """Return a deep copy of the canonical tool manifest."""
    return deepcopy(_TOOL_MANIFEST)


def get_tool_spec(tool_name: str) -> Optional[Dict[str, Any]]:
    """Return a deep copy of a single tool spec by name, if present."""
    for spec in _TOOL_MANIFEST:
        if spec["name"] == tool_name:
            return deepcopy(spec)
    return None


def get_tool_names() -> List[str]:
    """Return canonical tool names in declaration order."""
    return [spec["name"] for spec in _TOOL_MANIFEST]


def get_prefixed_tool_names(prefix: str) -> List[str]:
    """Return canonical tool names with a namespace prefix.

    Example:
        prefix='personal' -> personal_kvault_init, personal_kvault_read_entity, ...
    """
    normalized = prefix.strip().strip("_")
    if not normalized:
        return get_tool_names()
    return [f"{normalized}_{name}" for name in get_tool_names()]

