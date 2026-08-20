"""
Unit tests for MCPClientPool.

Tests connection management, tool invocation, schema validation,
resource reading, prompt retrieval, and error handling with exponential backoff.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

from a2a.mcp.client import (
    MCPClientPool,
    PromptResult,
    _AgentConnection,
    FAILURE_THRESHOLD,
    MAX_RETRIES,
)
from a2a.mcp.capabilities import (
    CapabilityCard,
    ResourceContent,
    ResourceDescriptor,
    ToolDescriptor,
    ToolResult,
)
from a2a.mcp.auth import AuthProvider
from a2a.mcp.errors import (
    MCPConnectionError,
    MCPSchemaValidationError,
    MCPToolError,
)
from a2a.mcp.loop_runner import AsyncLoopRunner


# --- Fixtures ---


def _make_capability_card(
    agent_name: str = "remote-agent",
    tools: list[ToolDescriptor] | None = None,
    resources: list[ResourceDescriptor] | None = None,
) -> CapabilityCard:
    """Helper to build a CapabilityCard for testing."""
    if tools is None:
        tools = [
            ToolDescriptor(
                name="search",
                description="Search for items",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            ),
            ToolDescriptor(
                name="echo",
                description="Echo input",
                input_schema={},
            ),
        ]
    if resources is None:
        resources = [
            ResourceDescriptor(
                uri="data://recent",
                name="Recent data",
                description="Recent items",
                mime_type="application/json",
            )
        ]
    return CapabilityCard(
        agent_name=agent_name,
        agent_role="assistant",
        endpoint="http://localhost:5100/mcp",
        transport_type="streamable-http",
        zone_name="test-zone",
        tools=tools,
        resources=resources,
    )


def _make_pool(auth_provider: AuthProvider | None = None) -> MCPClientPool:
    """Helper to create an MCPClientPool for testing."""
    loop_runner = MagicMock(spec=AsyncLoopRunner)
    return MCPClientPool(
        agent_name="test-agent",
        zone_name="test-zone",
        loop_runner=loop_runner,
        auth_provider=auth_provider,
        timeout=5.0,
    )


# --- Test Classes ---


class TestConnect:
    """Tests for connect/disconnect and availability tracking."""

    @pytest.mark.asyncio
    async def test_connect_stores_agent(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        assert pool.is_agent_available("remote-agent") is True
        assert "remote-agent" in pool.get_available_agents()

    @pytest.mark.asyncio
    async def test_disconnect_removes_agent(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)
        await pool.disconnect("remote-agent")

        assert pool.is_agent_available("remote-agent") is False
        assert "remote-agent" not in pool.get_available_agents()

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_agent_no_error(self):
        pool = _make_pool()
        # Should not raise
        await pool.disconnect("nonexistent")

    @pytest.mark.asyncio
    async def test_is_agent_available_unknown_agent(self):
        pool = _make_pool()
        assert pool.is_agent_available("unknown") is False

    @pytest.mark.asyncio
    async def test_get_available_agents_empty(self):
        pool = _make_pool()
        assert pool.get_available_agents() == []

    @pytest.mark.asyncio
    async def test_multiple_agents(self):
        pool = _make_pool()
        card1 = _make_capability_card(agent_name="agent-a")
        card2 = _make_capability_card(agent_name="agent-b")

        await pool.connect("http://localhost:5100/mcp", card1)
        await pool.connect("http://localhost:5200/mcp", card2)

        available = pool.get_available_agents()
        assert "agent-a" in available
        assert "agent-b" in available


class TestSchemaValidation:
    """Tests for tool argument schema validation."""

    @pytest.mark.asyncio
    async def test_missing_required_field_raises(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        with pytest.raises(MCPSchemaValidationError) as exc_info:
            await pool.call_tool("remote-agent", "search", {"limit": 10})

        assert "query" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_wrong_type_raises(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        with pytest.raises(MCPSchemaValidationError) as exc_info:
            await pool.call_tool("remote-agent", "search", {"query": 123})

        assert "query" in str(exc_info.value)
        assert "string" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_valid_arguments_pass_validation(self):
        """Valid arguments should not raise schema error (but may fail on connection)."""
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        # Will fail on connection since there's no real server, but should not
        # fail on schema validation
        with pytest.raises(MCPConnectionError):
            await pool.call_tool("remote-agent", "search", {"query": "test", "limit": 5})

    @pytest.mark.asyncio
    async def test_tool_not_in_card_raises(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        with pytest.raises(MCPToolError) as exc_info:
            await pool.call_tool("remote-agent", "nonexistent_tool", {})

        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_empty_schema_allows_any_arguments(self):
        """Tool with empty schema should allow any arguments."""
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        # "echo" has empty schema — should not raise schema error
        with pytest.raises(MCPConnectionError):
            await pool.call_tool("remote-agent", "echo", {"anything": "goes"})

    @pytest.mark.asyncio
    async def test_boolean_not_treated_as_integer(self):
        """Booleans should not pass integer type check."""
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        with pytest.raises(MCPSchemaValidationError) as exc_info:
            await pool.call_tool("remote-agent", "search", {"query": "test", "limit": True})

        assert "limit" in str(exc_info.value)


class TestCallTool:
    """Tests for call_tool with mocked HTTP responses."""

    @pytest.mark.asyncio
    async def test_successful_tool_call(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        mock_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "found 3 results"}],
                "isError": False,
            },
        }

        with patch.object(pool, "_send_request", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = mock_response
            result = await pool.call_tool("remote-agent", "search", {"query": "test"})

        assert isinstance(result, ToolResult)
        assert result.content == [{"type": "text", "text": "found 3 results"}]
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_tool_call_error_response(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        mock_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32000, "message": "Tool execution failed"},
        }

        with patch.object(pool, "_send_request", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = mock_response
            with pytest.raises(MCPToolError) as exc_info:
                await pool.call_tool("remote-agent", "search", {"query": "test"})

        assert "Tool execution failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_connection_not_found_raises(self):
        pool = _make_pool()

        with pytest.raises(MCPConnectionError):
            await pool.call_tool("nonexistent", "search", {"query": "test"})


class TestListTools:
    """Tests for list_tools."""

    @pytest.mark.asyncio
    async def test_list_tools_success(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        mock_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {
                        "name": "search",
                        "description": "Search for items",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                    }
                ]
            },
        }

        with patch.object(pool, "_send_request", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = mock_response
            tools = await pool.list_tools("remote-agent")

        assert len(tools) == 1
        assert tools[0].name == "search"
        assert tools[0].description == "Search for items"


class TestReadResource:
    """Tests for read_resource."""

    @pytest.mark.asyncio
    async def test_read_resource_success(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        mock_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "contents": [
                    {
                        "uri": "data://recent",
                        "mimeType": "application/json",
                        "text": '{"items": []}',
                    }
                ]
            },
        }

        with patch.object(pool, "_send_request", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = mock_response
            content = await pool.read_resource("remote-agent", "data://recent")

        assert isinstance(content, ResourceContent)
        assert content.uri == "data://recent"
        assert content.mime_type == "application/json"
        assert content.text == '{"items": []}'

    @pytest.mark.asyncio
    async def test_read_resource_empty_contents(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        mock_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"contents": []},
        }

        with patch.object(pool, "_send_request", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = mock_response
            content = await pool.read_resource("remote-agent", "data://missing")

        assert content.uri == "data://missing"
        assert content.text == ""


class TestListResources:
    """Tests for list_resources."""

    @pytest.mark.asyncio
    async def test_list_resources_success(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        mock_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "resources": [
                    {
                        "uri": "data://recent",
                        "name": "Recent",
                        "description": "Recent data",
                        "mimeType": "application/json",
                    }
                ]
            },
        }

        with patch.object(pool, "_send_request", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = mock_response
            resources = await pool.list_resources("remote-agent")

        assert len(resources) == 1
        assert resources[0].uri == "data://recent"
        assert resources[0].mime_type == "application/json"


class TestGetPrompt:
    """Tests for get_prompt."""

    @pytest.mark.asyncio
    async def test_get_prompt_success(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        mock_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "description": "Summarize paper",
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": "Summarize DOI:123"}}
                ],
            },
        }

        with patch.object(pool, "_send_request", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = mock_response
            result = await pool.get_prompt("remote-agent", "summarize", {"doi": "123"})

        assert isinstance(result, PromptResult)
        assert result.description == "Summarize paper"
        assert len(result.messages) == 1

    @pytest.mark.asyncio
    async def test_get_prompt_error(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        mock_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "Prompt not found"},
        }

        with patch.object(pool, "_send_request", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = mock_response
            with pytest.raises(MCPToolError) as exc_info:
                await pool.get_prompt("remote-agent", "unknown", {})

        assert "Prompt not found" in str(exc_info.value)


class TestExponentialBackoff:
    """Tests for retry behavior with exponential backoff."""

    @pytest.mark.asyncio
    async def test_retries_on_connection_error(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        with patch.object(pool, "_send_request", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = httpx.ConnectError("Connection refused")

            with pytest.raises(MCPConnectionError):
                await pool.call_tool("remote-agent", "search", {"query": "test"})

        # Should have retried MAX_RETRIES times
        assert mock_send.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_retries_on_timeout(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        with patch.object(pool, "_send_request", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = httpx.TimeoutException("Timeout")

            with pytest.raises(MCPConnectionError):
                await pool.call_tool("remote-agent", "search", {"query": "test"})

        assert mock_send.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_success_on_second_attempt(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        mock_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [], "isError": False},
        }

        with patch.object(pool, "_send_request", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = [
                httpx.ConnectError("Connection refused"),
                mock_response,
            ]
            result = await pool.call_tool("remote-agent", "search", {"query": "test"})

        assert mock_send.call_count == 2
        assert isinstance(result, ToolResult)

    @pytest.mark.asyncio
    async def test_marks_unavailable_after_threshold(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        with patch.object(pool, "_send_request", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = httpx.ConnectError("Connection refused")

            with pytest.raises(MCPConnectionError):
                await pool.call_tool("remote-agent", "search", {"query": "test"})

        # After MAX_RETRIES (3) consecutive failures >= FAILURE_THRESHOLD (3)
        assert pool.is_agent_available("remote-agent") is False

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        pool = _make_pool()
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        mock_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [], "isError": False},
        }

        # First call fails twice then succeeds
        with patch.object(pool, "_send_request", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = [
                httpx.ConnectError("Connection refused"),
                mock_response,
            ]
            await pool.call_tool("remote-agent", "search", {"query": "test"})

        # Agent should still be available
        assert pool.is_agent_available("remote-agent") is True


class TestAuthHeaders:
    """Tests for authentication header generation."""

    @pytest.mark.asyncio
    async def test_auth_header_included_when_provider_set(self):
        auth_provider = AuthProvider(
            agent_name="test-agent",
            zone_secrets={"test-zone": "secret123"},
        )
        pool = _make_pool(auth_provider=auth_provider)
        card = _make_capability_card()
        await pool.connect("http://localhost:5100/mcp", card)

        headers = pool._get_auth_headers()
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")

    @pytest.mark.asyncio
    async def test_no_auth_header_without_provider(self):
        pool = _make_pool()
        headers = pool._get_auth_headers()
        assert headers == {}


class TestPromptResult:
    """Tests for PromptResult data class."""

    def test_to_dict(self):
        pr = PromptResult(
            description="Test prompt",
            messages=[{"role": "user", "content": {"type": "text", "text": "Hello"}}],
        )
        d = pr.to_dict()
        assert d["description"] == "Test prompt"
        assert len(d["messages"]) == 1

    def test_from_dict(self):
        data = {
            "description": "Test",
            "messages": [{"role": "assistant", "content": {"type": "text", "text": "Hi"}}],
        }
        pr = PromptResult.from_dict(data)
        assert pr.description == "Test"
        assert pr.messages[0]["role"] == "assistant"

    def test_from_dict_defaults(self):
        pr = PromptResult.from_dict({})
        assert pr.description == ""
        assert pr.messages == []
