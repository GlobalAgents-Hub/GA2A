"""
MCP Communication Layer for GA2A Protocol.

This package implements the Model Context Protocol (MCP) as a structured capability
exchange layer on top of the existing GA2A peer-to-peer network. Agents in a zone
can expose tools, resources, and prompts via MCP servers and consume them via MCP
clients — all while maintaining backward compatibility with existing TCP socket
communication.

Modules:
    loop_runner  - AsyncLoopRunner for bridging threaded and asyncio code
    capabilities - Data models (CapabilityCard, ToolDescriptor, etc.)
    auth         - Zone-scoped token authentication (AuthProvider, AuthValidator)
    server       - MCPServerHost wrapping the official MCP SDK server
    client       - MCPClientPool managing connections to remote MCP servers
    registry     - ZoneRegistry maintaining per-zone capability cards
    bridge       - TransportBridge translating P2P discovery to MCP registrations
"""

from .loop_runner import AsyncLoopRunner
from .capabilities import (
    CapabilityCard,
    ToolDescriptor,
    ResourceDescriptor,
    PromptDescriptor,
    ToolResult,
    ResourceContent,
    VALID_TRANSPORT_TYPES,
)

from .auth import AuthProvider, AuthValidator, AuthToken
from .errors import (
    MCPError,
    MCPPortConflictError,
    MCPConnectionError,
    MCPAuthError,
    MCPToolError,
    MCPSchemaValidationError,
    MCPCapabilityCardError,
)
from .registry import ZoneRegistry

from .server import MCPServerHost
from .client import MCPClientPool, PromptResult

from .bridge import TransportBridge

__all__: list[str] = [
    "AsyncLoopRunner",
    "CapabilityCard",
    "ToolDescriptor",
    "ResourceDescriptor",
    "PromptDescriptor",
    "ToolResult",
    "ResourceContent",
    "VALID_TRANSPORT_TYPES",
    "AuthProvider",
    "AuthValidator",
    "AuthToken",
    "MCPError",
    "MCPPortConflictError",
    "MCPConnectionError",
    "MCPAuthError",
    "MCPToolError",
    "MCPSchemaValidationError",
    "MCPCapabilityCardError",
    "ZoneRegistry",
    "MCPServerHost",
    "MCPClientPool",
    "PromptResult",
    "TransportBridge",
]
