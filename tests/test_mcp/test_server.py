"""
Tests for MCPServerHost — verifies registration, deregistration,
capability card generation, port conflict detection, and JSON-RPC handling.

Requirements: 1.1, 1.2, 1.3, 1.4, 9.1, 9.2, 9.4
"""

import json
import time
import threading

import pytest
import httpx

from a2a.mcp.server import MCPServerHost
from a2a.mcp.loop_runner import AsyncLoopRunner
from a2a.mcp.capabilities import CapabilityCard, ToolDescriptor
from a2a.mcp.errors import MCPPortConflictError
from a2a.mcp.auth import AuthProvider, AuthValidator


@pytest.fixture
def loop_runner():
    """Provide a running AsyncLoopRunner for tests."""
    runner = AsyncLoopRunner()
    runner.start()
    yield runner
    runner.stop()


@pytest.fixture
def free_port():
    """Find a free port for testing."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestMCPServerHostRegistration:
    """Test tool/resource/prompt registration and deregistration."""

    def test_register_tool(self, loop_runner):
        """Tools can be registered and appear in capability card."""
        server = MCPServerHost("test-agent", 9999, loop_runner)

        def my_tool(x: int) -> int:
            return x * 2

        server.register_tool("multiply", my_tool, "Multiplies by 2", {"type": "object"})
        card = server.get_capability_card()

        assert len(card.tools) == 1
        assert card.tools[0].name == "multiply"
        assert card.tools[0].description == "Multiplies by 2"

    def test_deregister_tool(self, loop_runner):
        """Deregistered tools no longer appear in capability card."""
        server = MCPServerHost("test-agent", 9999, loop_runner)
        server.register_tool("tool1", lambda: None, "Tool 1")
        server.register_tool("tool2", lambda: None, "Tool 2")

        server.deregister_tool("tool1")
        card = server.get_capability_card()

        assert len(card.tools) == 1
        assert card.tools[0].name == "tool2"

    def test_register_resource(self, loop_runner):
        """Resources can be registered and appear in capability card."""
        server = MCPServerHost("test-agent", 9999, loop_runner)

        server.register_resource(
            "file:///data/info.txt",
            lambda: "content",
            "A text file",
            "text/plain",
        )
        card = server.get_capability_card()

        assert len(card.resources) == 1
        assert card.resources[0].uri == "file:///data/info.txt"
        assert card.resources[0].mime_type == "text/plain"

    def test_deregister_resource(self, loop_runner):
        """Deregistered resources no longer appear in capability card."""
        server = MCPServerHost("test-agent", 9999, loop_runner)
        server.register_resource("res://a", lambda: "a")
        server.register_resource("res://b", lambda: "b")

        server.deregister_resource("res://a")
        card = server.get_capability_card()

        assert len(card.resources) == 1
        assert card.resources[0].uri == "res://b"

    def test_register_prompt(self, loop_runner):
        """Prompts can be registered and appear in capability card."""
        server = MCPServerHost("test-agent", 9999, loop_runner)

        server.register_prompt(
            "greeting",
            lambda name: f"Hello, {name}!",
            "A greeting prompt",
            [{"name": "name", "description": "The name to greet"}],
        )
        card = server.get_capability_card()

        assert len(card.prompts) == 1
        assert card.prompts[0].name == "greeting"
        assert len(card.prompts[0].arguments) == 1

    def test_deregister_prompt(self, loop_runner):
        """Deregistered prompts no longer appear in capability card."""
        server = MCPServerHost("test-agent", 9999, loop_runner)
        server.register_prompt("p1", lambda: "p1")
        server.register_prompt("p2", lambda: "p2")

        server.deregister_prompt("p1")
        card = server.get_capability_card()

        assert len(card.prompts) == 1
        assert card.prompts[0].name == "p2"


class TestMCPServerHostCapabilityCard:
    """Test capability card generation."""

    def test_capability_card_structure(self, loop_runner, free_port):
        """Capability card has correct structure and metadata."""
        server = MCPServerHost("my-agent", free_port, loop_runner)
        server.register_tool("tool1", lambda: None, "A tool")

        card = server.get_capability_card()

        assert isinstance(card, CapabilityCard)
        assert card.agent_name == "my-agent"
        assert card.transport_type == "streamable-http"
        assert f":{free_port}/mcp" in card.endpoint
        assert card.published_at != ""

    def test_empty_capability_card(self, loop_runner):
        """Capability card works with no registrations."""
        server = MCPServerHost("empty-agent", 9999, loop_runner)
        card = server.get_capability_card()

        assert card.agent_name == "empty-agent"
        assert card.tools == []
        assert card.resources == []
        assert card.prompts == []


class TestMCPServerHostProperties:
    """Test endpoint and is_running properties."""

    def test_endpoint_format(self, loop_runner, free_port):
        """Endpoint property returns correct URL format."""
        server = MCPServerHost("agent", free_port, loop_runner)
        assert server.endpoint == f"http://localhost:{free_port}/mcp"

    def test_is_running_initially_false(self, loop_runner):
        """Server is not running before start() is called."""
        server = MCPServerHost("agent", 9999, loop_runner)
        assert server.is_running is False


class TestMCPServerHostPortConflict:
    """Test port conflict detection."""

    def test_port_conflict_raises_error(self, loop_runner):
        """Starting on an occupied port raises MCPPortConflictError."""
        import socket
        # Bind to a port to create a conflict
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", 0))
        occupied_port = s.getsockname()[1]
        s.listen(1)

        try:
            server = MCPServerHost("agent", occupied_port, loop_runner)
            with pytest.raises(MCPPortConflictError) as exc_info:
                server.start()
            assert exc_info.value.port == occupied_port
        finally:
            s.close()


class TestMCPServerHostLifecycle:
    """Test server start and stop lifecycle."""

    def test_start_and_stop(self, loop_runner, free_port):
        """Server can start and stop cleanly."""
        server = MCPServerHost("lifecycle-agent", free_port, loop_runner)
        server.register_tool("echo", lambda msg="": msg, "Echo tool")

        server.start()
        assert server.is_running is True

        server.stop()
        assert server.is_running is False

    def test_start_idempotent(self, loop_runner, free_port):
        """Calling start() when already running is a no-op."""
        server = MCPServerHost("agent", free_port, loop_runner)
        server.start()
        server.start()  # Should not raise
        assert server.is_running is True
        server.stop()

    def test_stop_when_not_running(self, loop_runner):
        """Calling stop() when not running is safe."""
        server = MCPServerHost("agent", 9999, loop_runner)
        server.stop()  # Should not raise


class TestMCPServerHostHTTP:
    """Test the JSON-RPC 2.0 HTTP interface."""

    @pytest.fixture
    def running_server(self, loop_runner, free_port):
        """Provide a running MCP server with a registered tool."""
        server = MCPServerHost("http-agent", free_port, loop_runner)
        server.register_tool(
            "add",
            lambda a=0, b=0: a + b,
            "Adds two numbers",
            {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}},
        )
        server.register_resource(
            "file:///test.txt",
            lambda: "Hello from resource",
            "Test resource",
            "text/plain",
        )
        server.register_prompt(
            "greet",
            lambda name="world": f"Hello, {name}!",
            "Greeting prompt",
        )
        server.start()
        yield server, free_port
        server.stop()

    def test_initialize(self, running_server):
        """Initialize method returns server capabilities."""
        server, port = running_server
        response = httpx.post(
            f"http://localhost:{port}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert "capabilities" in data["result"]
        assert data["result"]["serverInfo"]["name"] == "http-agent"

    def test_tools_list(self, running_server):
        """tools/list returns registered tools."""
        server, port = running_server
        response = httpx.post(
            f"http://localhost:{port}/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        data = response.json()
        assert len(data["result"]["tools"]) == 1
        assert data["result"]["tools"][0]["name"] == "add"

    def test_tools_call(self, running_server):
        """tools/call invokes a tool and returns the result."""
        server, port = running_server
        response = httpx.post(
            f"http://localhost:{port}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "add", "arguments": {"a": 3, "b": 4}},
            },
        )
        data = response.json()
        assert data["result"]["isError"] is False
        content = data["result"]["content"][0]
        assert content["type"] == "text"
        assert "7" in content["text"]

    def test_tools_call_not_found(self, running_server):
        """tools/call for non-existent tool returns error."""
        server, port = running_server
        response = httpx.post(
            f"http://localhost:{port}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "nonexistent", "arguments": {}},
            },
        )
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32601

    def test_resources_list(self, running_server):
        """resources/list returns registered resources."""
        server, port = running_server
        response = httpx.post(
            f"http://localhost:{port}/mcp",
            json={"jsonrpc": "2.0", "id": 5, "method": "resources/list", "params": {}},
        )
        data = response.json()
        assert len(data["result"]["resources"]) == 1
        assert data["result"]["resources"][0]["uri"] == "file:///test.txt"

    def test_resources_read(self, running_server):
        """resources/read returns resource content."""
        server, port = running_server
        response = httpx.post(
            f"http://localhost:{port}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 6,
                "method": "resources/read",
                "params": {"uri": "file:///test.txt"},
            },
        )
        data = response.json()
        assert data["result"]["contents"][0]["text"] == "Hello from resource"

    def test_prompts_list(self, running_server):
        """prompts/list returns registered prompts."""
        server, port = running_server
        response = httpx.post(
            f"http://localhost:{port}/mcp",
            json={"jsonrpc": "2.0", "id": 7, "method": "prompts/list", "params": {}},
        )
        data = response.json()
        assert len(data["result"]["prompts"]) == 1
        assert data["result"]["prompts"][0]["name"] == "greet"

    def test_prompts_get(self, running_server):
        """prompts/get invokes the prompt handler."""
        server, port = running_server
        response = httpx.post(
            f"http://localhost:{port}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 8,
                "method": "prompts/get",
                "params": {"name": "greet", "arguments": {"name": "Alice"}},
            },
        )
        data = response.json()
        assert "messages" in data["result"]
        assert "Alice" in data["result"]["messages"][0]["content"]["text"]

    def test_invalid_json(self, running_server):
        """Invalid JSON returns parse error."""
        server, port = running_server
        response = httpx.post(
            f"http://localhost:{port}/mcp",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        data = response.json()
        assert data["error"]["code"] == -32700

    def test_unknown_method(self, running_server):
        """Unknown method returns method not found error."""
        server, port = running_server
        response = httpx.post(
            f"http://localhost:{port}/mcp",
            json={"jsonrpc": "2.0", "id": 9, "method": "unknown/method", "params": {}},
        )
        data = response.json()
        assert data["error"]["code"] == -32601

    def test_404_for_wrong_path(self, running_server):
        """Non-/mcp paths return 404."""
        server, port = running_server
        response = httpx.post(f"http://localhost:{port}/other")
        assert response.status_code == 404

    def test_dynamic_deregistration_returns_error(self, running_server):
        """After deregistering a tool, calling it returns method not found."""
        server, port = running_server
        # First verify tool works
        response = httpx.post(
            f"http://localhost:{port}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "add", "arguments": {"a": 1, "b": 2}},
            },
        )
        assert "result" in response.json()

        # Deregister
        server.deregister_tool("add")

        # Verify it's gone
        response = httpx.post(
            f"http://localhost:{port}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "add", "arguments": {"a": 1, "b": 2}},
            },
        )
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32601


class TestMCPServerHostAuth:
    """Test authentication validation on the server."""

    def test_auth_required_when_validator_set(self, loop_runner, free_port):
        """Server rejects requests without auth when validator is configured."""
        validator = AuthValidator("test-zone", "secret123")
        server = MCPServerHost("auth-agent", free_port, loop_runner, auth_validator=validator)
        server.register_tool("ping", lambda: "pong", "Ping tool")
        server.start()

        try:
            # Request without auth
            response = httpx.post(
                f"http://localhost:{free_port}/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            assert response.status_code == 401
        finally:
            server.stop()

    def test_auth_accepted_with_valid_token(self, loop_runner, free_port):
        """Server accepts requests with a valid auth token."""
        zone_name = "test-zone"
        zone_secret = "secret123"
        validator = AuthValidator(zone_name, zone_secret)
        provider = AuthProvider("client-agent", {zone_name: zone_secret})

        server = MCPServerHost("auth-agent", free_port, loop_runner, auth_validator=validator)
        server.register_tool("ping", lambda: "pong", "Ping tool")
        server.start()

        try:
            token = provider.generate_token(zone_name)
            response = httpx.post(
                f"http://localhost:{free_port}/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "result" in data
        finally:
            server.stop()
