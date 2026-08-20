"""
MCP Client Pool for managing connections to remote MCP servers.

Provides MCPClientPool which manages httpx-based connections to multiple
remote MCP servers within a zone. Supports tool invocation, resource reading,
prompt retrieval, schema validation, and exponential backoff on failures.

Requirements: 2.1, 2.2, 2.3, 2.4, 6.1, 6.2, 6.3, 6.4, 10.2, 10.3, 10.4
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from a2a.mcp.auth import AuthProvider
from a2a.mcp.capabilities import (
    CapabilityCard,
    ResourceContent,
    ResourceDescriptor,
    ToolDescriptor,
    ToolResult,
)
from a2a.mcp.errors import (
    MCPConnectionError,
    MCPSchemaValidationError,
    MCPToolError,
)
from a2a.mcp.loop_runner import AsyncLoopRunner

logger = logging.getLogger(__name__)

# Default request timeout in seconds
DEFAULT_TIMEOUT: float = 30.0

# Exponential backoff settings
MAX_RETRIES: int = 3
INITIAL_BACKOFF: float = 1.0
BACKOFF_MULTIPLIER: float = 2.0

# Consecutive failures before marking agent unavailable
FAILURE_THRESHOLD: int = 3


@dataclass
class PromptResult:
    """Result from a remote prompt invocation."""

    description: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "description": self.description,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromptResult:
        """Deserialize from a dictionary."""
        return cls(
            description=data.get("description", ""),
            messages=data.get("messages", []),
        )


@dataclass
class _AgentConnection:
    """Internal tracking for a connected agent."""

    endpoint: str
    capability_card: CapabilityCard
    consecutive_failures: int = 0
    available: bool = True


class MCPClientPool:
    """Pool of MCP client connections to remote agents' MCP servers.

    Manages connections to multiple remote MCP servers within a zone.
    Uses httpx for HTTP requests, sends JSON-RPC 2.0 messages, validates
    input against tool schemas, and handles connection failures with
    exponential backoff.
    """

    def __init__(
        self,
        agent_name: str,
        zone_name: str,
        loop_runner: AsyncLoopRunner,
        auth_provider: AuthProvider | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the client pool.

        Args:
            agent_name: Name of this agent (the client).
            zone_name: The zone this pool operates within.
            loop_runner: AsyncLoopRunner for bridging async/sync contexts.
            auth_provider: Optional auth provider for generating request tokens.
            timeout: Request timeout in seconds (default 30s).
        """
        self._agent_name = agent_name
        self._zone_name = zone_name
        self._loop_runner = loop_runner
        self._auth_provider = auth_provider
        self._timeout = timeout
        self._connections: dict[str, _AgentConnection] = {}
        self._request_id: int = 0

    def _next_request_id(self) -> int:
        """Generate a monotonically increasing JSON-RPC request ID."""
        self._request_id += 1
        return self._request_id

    def _get_auth_headers(self) -> dict[str, str]:
        """Build authorization headers if auth_provider is configured."""
        if self._auth_provider is None:
            return {}
        token = self._auth_provider.generate_token(self._zone_name)
        return {"Authorization": f"Bearer {token}"}

    def _get_connection(self, agent_name: str) -> _AgentConnection:
        """Get a connection or raise MCPConnectionError if not found."""
        conn = self._connections.get(agent_name)
        if conn is None:
            raise MCPConnectionError(
                f"agent://{agent_name}"
            )
        return conn

    # --- Public API ---

    async def connect(self, endpoint: str, capability_card: CapabilityCard) -> None:
        """Connect to a remote agent's MCP server.

        Stores the endpoint and capability card for future requests.
        Optionally sends an initialize handshake to verify connectivity.

        Args:
            endpoint: The HTTP endpoint of the remote MCP server.
            capability_card: The agent's published capability card.
        """
        agent_name = capability_card.agent_name
        self._connections[agent_name] = _AgentConnection(
            endpoint=endpoint,
            capability_card=capability_card,
            consecutive_failures=0,
            available=True,
        )
        logger.info(
            "Connected to agent '%s' at %s", agent_name, endpoint
        )

    async def disconnect(self, agent_name: str) -> None:
        """Disconnect from a remote agent's MCP server.

        Removes the agent from the connection pool.

        Args:
            agent_name: Name of the agent to disconnect from.
        """
        if agent_name in self._connections:
            del self._connections[agent_name]
            logger.info("Disconnected from agent '%s'", agent_name)

    async def call_tool(
        self,
        agent_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Invoke a tool on a remote MCP server.

        Validates input arguments against the tool's schema before sending.
        Uses exponential backoff on connection failures.

        Args:
            agent_name: Name of the remote agent hosting the tool.
            tool_name: Name of the tool to invoke.
            arguments: Input arguments for the tool.

        Returns:
            ToolResult with the tool's response.

        Raises:
            MCPConnectionError: If the remote server is unreachable.
            MCPSchemaValidationError: If arguments don't match the tool schema.
            MCPToolError: If the remote server returns an error response.
        """
        conn = self._get_connection(agent_name)

        # Validate input against schema
        self._validate_tool_arguments(conn.capability_card, tool_name, arguments)

        # Build JSON-RPC 2.0 request
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

        response_data = await self._send_with_retry(agent_name, conn, payload)

        # Parse result
        if "error" in response_data:
            error = response_data["error"]
            raise MCPToolError(
                tool_name,
                error.get("message", "Unknown error"),
            )

        result = response_data.get("result", {})
        return ToolResult(
            content=result.get("content", []),
            structured_content=result.get("structuredContent"),
            is_error=result.get("isError", False),
        )

    async def list_tools(self, agent_name: str) -> list[ToolDescriptor]:
        """List tools available on a remote MCP server.

        Args:
            agent_name: Name of the remote agent.

        Returns:
            List of ToolDescriptors from the remote server.

        Raises:
            MCPConnectionError: If the remote server is unreachable.
        """
        conn = self._get_connection(agent_name)

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "tools/list",
            "params": {},
        }

        response_data = await self._send_with_retry(agent_name, conn, payload)

        if "error" in response_data:
            error = response_data["error"]
            raise MCPToolError("tools/list", error.get("message", "Unknown error"))

        result = response_data.get("result", {})
        tools_data = result.get("tools", [])
        return [
            ToolDescriptor(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", t.get("input_schema", {})),
            )
            for t in tools_data
        ]

    async def read_resource(self, agent_name: str, uri: str) -> ResourceContent:
        """Read a resource from a remote MCP server.

        Args:
            agent_name: Name of the remote agent hosting the resource.
            uri: URI of the resource to read.

        Returns:
            ResourceContent with the resource data.

        Raises:
            MCPConnectionError: If the remote server is unreachable.
            MCPToolError: If the remote server returns an error.
        """
        conn = self._get_connection(agent_name)

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "resources/read",
            "params": {"uri": uri},
        }

        response_data = await self._send_with_retry(agent_name, conn, payload)

        if "error" in response_data:
            error = response_data["error"]
            raise MCPToolError("resources/read", error.get("message", "Unknown error"))

        result = response_data.get("result", {})
        contents = result.get("contents", [])
        if contents:
            content = contents[0]
            return ResourceContent(
                uri=content.get("uri", uri),
                mime_type=content.get("mimeType", "text/plain"),
                text=content.get("text"),
                blob=content.get("blob"),
            )

        return ResourceContent(uri=uri, mime_type="text/plain", text="")

    async def list_resources(self, agent_name: str) -> list[ResourceDescriptor]:
        """List resources available on a remote MCP server.

        Args:
            agent_name: Name of the remote agent.

        Returns:
            List of ResourceDescriptors from the remote server.

        Raises:
            MCPConnectionError: If the remote server is unreachable.
        """
        conn = self._get_connection(agent_name)

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "resources/list",
            "params": {},
        }

        response_data = await self._send_with_retry(agent_name, conn, payload)

        if "error" in response_data:
            error = response_data["error"]
            raise MCPToolError(
                "resources/list", error.get("message", "Unknown error")
            )

        result = response_data.get("result", {})
        resources_data = result.get("resources", [])
        return [
            ResourceDescriptor(
                uri=r["uri"],
                name=r.get("name", ""),
                description=r.get("description", ""),
                mime_type=r.get("mimeType", r.get("mime_type", "text/plain")),
            )
            for r in resources_data
        ]

    async def get_prompt(
        self,
        agent_name: str,
        prompt_name: str,
        arguments: dict[str, str],
    ) -> PromptResult:
        """Get a prompt from a remote MCP server.

        Args:
            agent_name: Name of the remote agent hosting the prompt.
            prompt_name: Name of the prompt to retrieve.
            arguments: Arguments to pass to the prompt template.

        Returns:
            PromptResult with the prompt's messages.

        Raises:
            MCPConnectionError: If the remote server is unreachable.
            MCPToolError: If the remote server returns an error.
        """
        conn = self._get_connection(agent_name)

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "prompts/get",
            "params": {"name": prompt_name, "arguments": arguments},
        }

        response_data = await self._send_with_retry(agent_name, conn, payload)

        if "error" in response_data:
            error = response_data["error"]
            raise MCPToolError(
                f"prompts/get:{prompt_name}",
                error.get("message", "Unknown error"),
            )

        result = response_data.get("result", {})
        return PromptResult(
            description=result.get("description", ""),
            messages=result.get("messages", []),
        )

    def get_available_agents(self) -> list[str]:
        """Get list of agent names currently connected and available.

        Returns:
            List of agent names that are connected and marked available.
        """
        return [
            name
            for name, conn in self._connections.items()
            if conn.available
        ]

    def is_agent_available(self, agent_name: str) -> bool:
        """Check if a specific agent is connected and available.

        Args:
            agent_name: Name of the agent to check.

        Returns:
            True if the agent is connected and available, False otherwise.
        """
        conn = self._connections.get(agent_name)
        return conn is not None and conn.available

    # --- Private helpers ---

    def _validate_tool_arguments(
        self,
        capability_card: CapabilityCard,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        """Validate tool arguments against the tool's input_schema.

        Performs basic JSON Schema validation:
        - Checks required fields are present
        - Checks basic type matching for top-level properties

        Args:
            capability_card: The remote agent's capability card.
            tool_name: Name of the tool being invoked.
            arguments: The arguments to validate.

        Raises:
            MCPSchemaValidationError: If validation fails.
            MCPToolError: If the tool is not found in the capability card.
        """
        # Find the tool in the capability card
        tool_descriptor = None
        for tool in capability_card.tools:
            if tool.name == tool_name:
                tool_descriptor = tool
                break

        if tool_descriptor is None:
            raise MCPToolError(
                tool_name,
                f"Tool '{tool_name}' not found in capability card for "
                f"agent '{capability_card.agent_name}'",
            )

        schema = tool_descriptor.input_schema
        if not schema:
            return  # No schema to validate against

        errors: list[str] = []

        # Check required fields
        required_fields = schema.get("required", [])
        for req_field in required_fields:
            if req_field not in arguments:
                errors.append(f"Missing required field: '{req_field}'")

        # Check type matching for provided arguments
        properties = schema.get("properties", {})
        for arg_name, arg_value in arguments.items():
            if arg_name in properties:
                prop_schema = properties[arg_name]
                expected_type = prop_schema.get("type")
                if expected_type and not self._check_type(arg_value, expected_type):
                    errors.append(
                        f"Field '{arg_name}' expected type '{expected_type}', "
                        f"got '{type(arg_value).__name__}'"
                    )

        if errors:
            raise MCPSchemaValidationError(tool_name, errors)

    @staticmethod
    def _check_type(value: Any, expected_type: str) -> bool:
        """Check if a value matches a JSON Schema type.

        Args:
            value: The value to check.
            expected_type: The expected JSON Schema type string.

        Returns:
            True if the value matches the expected type.
        """
        type_map: dict[str, tuple[type, ...]] = {
            "string": (str,),
            "integer": (int,),
            "number": (int, float),
            "boolean": (bool,),
            "array": (list,),
            "object": (dict,),
            "null": (type(None),),
        }

        allowed_types = type_map.get(expected_type)
        if allowed_types is None:
            # Unknown type, allow it
            return True

        # Special case: booleans should not match integer/number
        if expected_type in ("integer", "number") and isinstance(value, bool):
            return False

        return isinstance(value, allowed_types)

    async def _send_with_retry(
        self,
        agent_name: str,
        conn: _AgentConnection,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a JSON-RPC request with exponential backoff on failure.

        Retries up to MAX_RETRIES times with exponential backoff.
        If all retries fail, marks the agent as unavailable.

        Args:
            agent_name: Name of the remote agent.
            conn: The connection tracking object.
            payload: The JSON-RPC 2.0 request payload.

        Returns:
            The parsed JSON-RPC 2.0 response as a dict.

        Raises:
            MCPConnectionError: If all retries are exhausted.
        """
        backoff = INITIAL_BACKOFF

        for attempt in range(MAX_RETRIES):
            try:
                response_data = await self._send_request(conn.endpoint, payload)
                # Success — reset failure counter
                conn.consecutive_failures = 0
                conn.available = True
                return response_data
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError, OSError) as exc:
                conn.consecutive_failures += 1
                logger.warning(
                    "Request to agent '%s' failed (attempt %d/%d): %s",
                    agent_name,
                    attempt + 1,
                    MAX_RETRIES,
                    exc,
                )

                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(backoff)
                    backoff *= BACKOFF_MULTIPLIER

        # All retries exhausted — mark unavailable if threshold reached
        if conn.consecutive_failures >= FAILURE_THRESHOLD:
            conn.available = False
            logger.error(
                "Agent '%s' marked as unavailable after %d consecutive failures",
                agent_name,
                conn.consecutive_failures,
            )

        raise MCPConnectionError(conn.endpoint)

    async def _send_request(
        self,
        endpoint: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a single JSON-RPC 2.0 request via httpx.

        Args:
            endpoint: The remote server URL.
            payload: The JSON-RPC 2.0 payload to send.

        Returns:
            The parsed JSON response body.

        Raises:
            httpx.ConnectError: On connection failure.
            httpx.TimeoutException: On request timeout.
            httpx.HTTPStatusError: On non-2xx response status.
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        headers.update(self._get_auth_headers())

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
