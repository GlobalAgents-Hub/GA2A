"""
MCP error hierarchy for the GA2A protocol.

Provides structured error types for all MCP-related failures including
port conflicts, connection issues, authentication failures, tool invocation
errors, schema validation, and capability card problems.

Requirements: 1.3, 6.3, 7.4
"""

from __future__ import annotations


class MCPError(Exception):
    """Base error for all MCP-related failures."""


class MCPPortConflictError(MCPError):
    """MCP server cannot bind to configured port."""

    def __init__(self, port: int) -> None:
        self.port = port
        super().__init__(f"MCP server cannot bind to port {port}: port already in use")


class MCPConnectionError(MCPError):
    """Cannot connect to remote MCP server."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        super().__init__(f"Cannot connect to remote MCP server at {endpoint}")


class MCPAuthError(MCPError):
    """Authentication failed for MCP connection."""

    def __init__(self, agent_name: str, zone_name: str) -> None:
        self.agent_name = agent_name
        self.zone_name = zone_name
        super().__init__(
            f"Authentication failed for agent '{agent_name}' in zone '{zone_name}'"
        )


class MCPToolError(MCPError):
    """Error invoking a remote tool."""

    def __init__(self, tool_name: str, detail: str) -> None:
        self.tool_name = tool_name
        self.detail = detail
        super().__init__(f"Error invoking tool '{tool_name}': {detail}")


class MCPSchemaValidationError(MCPError):
    """Input arguments do not match tool schema."""

    def __init__(self, tool_name: str, validation_errors: list[str]) -> None:
        self.tool_name = tool_name
        self.validation_errors = validation_errors
        errors_str = "; ".join(validation_errors)
        super().__init__(
            f"Schema validation failed for tool '{tool_name}': {errors_str}"
        )


class MCPCapabilityCardError(MCPError):
    """Invalid or malformed capability card."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Invalid capability card: {detail}")
