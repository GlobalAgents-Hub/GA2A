"""
A2A Protocol - A decentralized protocol for agent communication
"""

from .core import A2A
from .agents import Peer, AgentBuilder
from .zones import ZoneManager
from .events import EventHandler
from .discovery import NetworkDiscovery

from .mcp import (
    AsyncLoopRunner,
    CapabilityCard,
    ToolDescriptor,
    ResourceDescriptor,
    PromptDescriptor,
    ToolResult,
    ResourceContent,
    AuthProvider,
    AuthValidator,
    AuthToken,
    MCPError,
    MCPPortConflictError,
    MCPConnectionError,
    MCPAuthError,
    MCPToolError,
    MCPSchemaValidationError,
    MCPCapabilityCardError,
    ZoneRegistry,
    MCPServerHost,
    MCPClientPool,
    PromptResult,
    TransportBridge,
)

__version__ = "0.1.0"
__all__ = [
    # Core
    'A2A',
    'Peer',
    'AgentBuilder',
    'ZoneManager',
    'EventHandler',
    'NetworkDiscovery',
    # MCP - Infrastructure
    'AsyncLoopRunner',
    # MCP - Data models
    'CapabilityCard',
    'ToolDescriptor',
    'ResourceDescriptor',
    'PromptDescriptor',
    'ToolResult',
    'ResourceContent',
    # MCP - Auth
    'AuthProvider',
    'AuthValidator',
    'AuthToken',
    # MCP - Errors
    'MCPError',
    'MCPPortConflictError',
    'MCPConnectionError',
    'MCPAuthError',
    'MCPToolError',
    'MCPSchemaValidationError',
    'MCPCapabilityCardError',
    # MCP - Components
    'ZoneRegistry',
    'MCPServerHost',
    'MCPClientPool',
    'PromptResult',
    'TransportBridge',
]