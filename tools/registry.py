"""Tool registry — maps tool names to functions and provides Anthropic tool_use schemas.

This module serves two purposes:
1. Maps tool name strings to callable Python functions (for the agent to dispatch)
2. Provides Anthropic-compatible tool_use JSON schemas (sent in API requests)
"""

import logging
from typing import Any, Callable

from tools.kb_search import kb_search
from tools.log_search import log_search
from tools.server_metrics import server_metrics
from tools.status_check import status_check
from tools.create_ticket import create_ticket

logger = logging.getLogger(__name__)

# Type alias for tool functions
ToolFunction = Callable[..., dict[str, Any]]

# Registry mapping tool names to their callable implementations
TOOL_REGISTRY: dict[str, ToolFunction] = {
    "kb_search": kb_search,
    "log_search": log_search,
    "server_metrics": server_metrics,
    "status_check": status_check,
    "create_ticket": create_ticket,
}

# Anthropic tool_use compatible JSON schemas
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "kb_search",
        "description": (
            "Search the IT knowledge base and runbooks for troubleshooting procedures, "
            "diagnosis steps, and resolution guides. Use this when you need to look up "
            "how to diagnose or resolve a specific IT issue like VPN problems, disk usage, "
            "email sync failures, or service restarts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query describing the IT issue or topic to look up",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of runbook sections to return (default 3)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "log_search",
        "description": (
            "Search application and system logs for error messages, warnings, and events. "
            "Use this to investigate what happened on a specific service, find error patterns, "
            "or check for recent issues. Supports filtering by service name, severity level, "
            "and time window."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in log messages (case-insensitive)",
                },
                "service": {
                    "type": "string",
                    "description": (
                        "Filter by service name. Available services: web-server, app-server, "
                        "database-server, vpn-gateway, email-server, smtp-relay, file-server, "
                        "monitoring-server"
                    ),
                },
                "level": {
                    "type": "string",
                    "enum": ["INFO", "WARNING", "ERROR", "CRITICAL"],
                    "description": "Filter by log severity level",
                },
                "hours": {
                    "type": "integer",
                    "description": "How many hours back to search (default 24)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "server_metrics",
        "description": (
            "Fetch current resource metrics (CPU, memory, disk, network) for servers. "
            "Use this to check if a server is under resource pressure, identify bottlenecks, "
            "or verify resource usage after taking corrective action. Automatically generates "
            "alerts for values exceeding critical thresholds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hostname": {
                    "type": "string",
                    "description": (
                        "Server hostname to check. Available: web-server, app-server, "
                        "database-server, vpn-gateway, email-server, smtp-relay, file-server, "
                        "monitoring-server. Omit to get metrics for all servers."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "status_check",
        "description": (
            "Check the operational status of IT services. Use this as a first diagnostic "
            "step to quickly see which services are healthy, degraded, warning, or down. "
            "This is the most reliable tool — use it to get an overview before diving deeper "
            "with other tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_name": {
                    "type": "string",
                    "description": (
                        "Specific service to check. Available: web-server, app-server, "
                        "database-server, vpn-gateway, email-server, smtp-relay, file-server, "
                        "monitoring-server. Omit to get status of all services."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "create_ticket",
        "description": (
            "Create an IT support ticket for an incident that needs further attention, "
            "escalation, or tracking. IMPORTANT: This action requires user confirmation. "
            "First call with confirmed=false to get a preview, then call again with "
            "confirmed=true only after the user approves."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short descriptive title for the ticket",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Detailed description including the issue, diagnosis performed, "
                        "and recommended next steps"
                    ),
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "Ticket priority level based on impact and urgency",
                },
                "category": {
                    "type": "string",
                    "enum": ["network", "server", "application", "security", "other"],
                    "description": "Issue category for routing to the appropriate team",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": (
                        "Set to false (default) to get a preview. Set to true only after "
                        "user has confirmed they want to create the ticket."
                    ),
                },
            },
            "required": ["title", "description", "priority", "category"],
        },
    },
]


def get_tool_schemas() -> list[dict[str, Any]]:
    """Return all tool schemas formatted for the Anthropic API tools parameter.

    These schemas are passed directly to the Anthropic API in the 'tools'
    parameter of a messages.create() call.

    Returns:
        List of tool schema dicts compatible with Anthropic's tool_use format.
    """
    return TOOL_SCHEMAS


def get_tool(name: str) -> ToolFunction:
    """Look up a tool function by name.

    Args:
        name: The tool name as it appears in TOOL_REGISTRY.

    Returns:
        The callable tool function.

    Raises:
        KeyError: If the tool name is not registered.
    """
    if name not in TOOL_REGISTRY:
        raise KeyError(
            f"Unknown tool: '{name}'. Available tools: {sorted(TOOL_REGISTRY.keys())}"
        )
    return TOOL_REGISTRY[name]


def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool by name with the given arguments.

    This is the main dispatch function called by the agent loop. It looks up
    the tool function and calls it with the provided arguments.

    Args:
        name: Tool name from Claude's tool_use response block.
        arguments: Dict of arguments from Claude's tool_use input field.

    Returns:
        The tool's structured return dict.

    Raises:
        KeyError: If tool name is unknown.
        Exception: Any exception raised by the tool function itself
            (LogSearchError, MetricsConnectionError, TicketCreationError, etc.)
    """
    logger.info("Executing tool: %s with args: %s", name, list(arguments.keys()))
    tool_fn = get_tool(name)
    result = tool_fn(**arguments)
    logger.info("Tool %s completed successfully", name)
    return result
