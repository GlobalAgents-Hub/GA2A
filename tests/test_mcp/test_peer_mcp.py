"""
Tests for Peer MCP integration.

Validates that the Peer class correctly supports optional MCP capabilities
while maintaining full backward compatibility with non-MCP peers.

Requirements: 8.1, 8.2, 8.3, 8.4, 9.1, 9.2, 9.3, 9.4
"""

import pytest
from unittest.mock import patch, MagicMock

from a2a.agents import Peer, AgentBuilder


# --- Backward Compatibility Tests (Requirement 8.1, 8.2) ---


class TestPeerBackwardCompatibility:
    """Verify that non-MCP Peers work identically to the original implementation."""

    def test_peer_without_mcp_port_has_mcp_disabled(self):
        """A Peer created without mcp_port should have mcp_enabled=False."""
        peer = Peer(name="basic", role="worker")
        assert peer.mcp_enabled is False

    def test_peer_without_mcp_has_no_server(self):
        """A non-MCP Peer should have mcp_server=None."""
        peer = Peer(name="basic", role="worker")
        assert peer.mcp_server is None

    def test_peer_without_mcp_has_no_clients(self):
        """A non-MCP Peer should have mcp_clients=None."""
        peer = Peer(name="basic", role="worker")
        assert peer.mcp_clients is None

    def test_peer_creation_with_existing_fields(self):
        """Creating a Peer with only existing fields works as before."""
        peer = Peer(
            name="agent-1",
            role="researcher",
            port=6000,
            zone="science",
            capabilities=["search", "summarize"],
        )
        assert peer.name == "agent-1"
        assert peer.role == "researcher"
        assert peer.port == 6000
        assert peer.zone == "science"
        assert peer.capabilities == ["search", "summarize"]

    def test_peer_send_unchanged(self):
        """The send() method remains functional for non-MCP peers."""
        peer = Peer(name="sender", role="worker")
        # send() to a non-listening port returns False (no connection)
        assert peer.send({"hello": "world"}, target_port=59999) is False

    def test_peer_event_handlers_unchanged(self):
        """Event handler registration and triggering work as before."""
        peer = Peer(name="evented", role="worker")
        received = []

        @peer.on("message_received")
        def handler(data):
            received.append(data)

        peer._trigger_event("message_received", {"msg": "test"})
        assert received == [{"msg": "test"}]

    def test_start_mcp_raises_on_non_mcp_peer(self):
        """start_mcp() raises RuntimeError if mcp_port is not configured."""
        peer = Peer(name="no-mcp", role="worker")
        with pytest.raises(RuntimeError, match="mcp_port is not configured"):
            peer.start_mcp()


# --- MCP Configuration Tests ---


class TestPeerMCPConfiguration:
    """Verify MCP configuration fields work correctly."""

    def test_peer_with_mcp_port_has_mcp_enabled(self):
        """A Peer with mcp_port set should have mcp_enabled=True."""
        peer = Peer(name="mcp-agent", role="worker", mcp_port=8100)
        assert peer.mcp_enabled is True

    def test_peer_mcp_tools_defaults_to_empty(self):
        """mcp_tools defaults to an empty list."""
        peer = Peer(name="agent", role="worker", mcp_port=8100)
        assert peer.mcp_tools == []

    def test_peer_mcp_resources_defaults_to_empty(self):
        """mcp_resources defaults to an empty list."""
        peer = Peer(name="agent", role="worker", mcp_port=8100)
        assert peer.mcp_resources == []

    def test_peer_mcp_prompts_defaults_to_empty(self):
        """mcp_prompts defaults to an empty list."""
        peer = Peer(name="agent", role="worker", mcp_port=8100)
        assert peer.mcp_prompts == []

    def test_peer_with_tool_config(self):
        """Peer can be created with tool configurations."""
        def my_tool():
            return "result"

        tools = [{"name": "search", "handler": my_tool, "description": "Search tool"}]
        peer = Peer(name="agent", role="worker", mcp_port=8100, mcp_tools=tools)
        assert len(peer.mcp_tools) == 1
        assert peer.mcp_tools[0]["name"] == "search"


# --- MCP Lifecycle Tests ---


class TestPeerMCPLifecycle:
    """Test start_mcp() and stop_mcp() lifecycle management."""

    @patch("a2a.mcp.client.MCPClientPool", autospec=False)
    @patch("a2a.mcp.server.MCPServerHost", autospec=False)
    @patch("a2a.mcp.loop_runner.AsyncLoopRunner", autospec=False)
    def test_start_mcp_initializes_components(
        self, MockRunner, MockServer, MockPool
    ):
        """start_mcp() creates loop runner, server, and client pool."""
        mock_runner_instance = MagicMock()
        MockRunner.return_value = mock_runner_instance
        mock_server_instance = MagicMock()
        MockServer.return_value = mock_server_instance
        mock_pool_instance = MagicMock()
        MockPool.return_value = mock_pool_instance

        peer = Peer(name="mcp-agent", role="worker", mcp_port=8100, zone="test-zone")
        peer.start_mcp()

        # Verify loop runner was started
        mock_runner_instance.start.assert_called_once()

        # Verify server was created and started
        assert peer.mcp_server is mock_server_instance
        mock_server_instance.start.assert_called_once()

        # Verify client pool was created
        assert peer.mcp_clients is mock_pool_instance

    @patch("a2a.mcp.client.MCPClientPool", autospec=False)
    @patch("a2a.mcp.server.MCPServerHost", autospec=False)
    @patch("a2a.mcp.loop_runner.AsyncLoopRunner", autospec=False)
    def test_start_mcp_registers_preconfigured_tools(
        self, MockRunner, MockServer, MockPool
    ):
        """start_mcp() registers tools from mcp_tools config."""
        mock_runner_instance = MagicMock()
        MockRunner.return_value = mock_runner_instance
        mock_server_instance = MagicMock()
        MockServer.return_value = mock_server_instance
        mock_pool_instance = MagicMock()
        MockPool.return_value = mock_pool_instance

        def search_handler(query: str):
            return f"results for {query}"

        tools = [
            {
                "name": "search",
                "handler": search_handler,
                "description": "Search tool",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
            }
        ]

        peer = Peer(name="agent", role="worker", mcp_port=8100, mcp_tools=tools)
        peer.start_mcp()

        mock_server_instance.register_tool.assert_called_once_with(
            name="search",
            handler=search_handler,
            description="Search tool",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        )

    @patch("a2a.mcp.client.MCPClientPool", autospec=False)
    @patch("a2a.mcp.server.MCPServerHost", autospec=False)
    @patch("a2a.mcp.loop_runner.AsyncLoopRunner", autospec=False)
    def test_start_mcp_registers_preconfigured_resources(
        self, MockRunner, MockServer, MockPool
    ):
        """start_mcp() registers resources from mcp_resources config."""
        mock_runner_instance = MagicMock()
        MockRunner.return_value = mock_runner_instance
        mock_server_instance = MagicMock()
        MockServer.return_value = mock_server_instance
        mock_pool_instance = MagicMock()
        MockPool.return_value = mock_pool_instance

        def data_handler():
            return "some data"

        resources = [
            {
                "uri": "data://status",
                "handler": data_handler,
                "description": "Status data",
                "mime_type": "application/json",
            }
        ]

        peer = Peer(name="agent", role="worker", mcp_port=8100, mcp_resources=resources)
        peer.start_mcp()

        mock_server_instance.register_resource.assert_called_once_with(
            uri="data://status",
            handler=data_handler,
            description="Status data",
            mime_type="application/json",
        )

    @patch("a2a.mcp.client.MCPClientPool", autospec=False)
    @patch("a2a.mcp.server.MCPServerHost", autospec=False)
    @patch("a2a.mcp.loop_runner.AsyncLoopRunner", autospec=False)
    def test_start_mcp_registers_preconfigured_prompts(
        self, MockRunner, MockServer, MockPool
    ):
        """start_mcp() registers prompts from mcp_prompts config."""
        mock_runner_instance = MagicMock()
        MockRunner.return_value = mock_runner_instance
        mock_server_instance = MagicMock()
        MockServer.return_value = mock_server_instance
        mock_pool_instance = MagicMock()
        MockPool.return_value = mock_pool_instance

        def prompt_handler(topic: str):
            return f"Write about {topic}"

        prompts = [
            {
                "name": "summarize",
                "handler": prompt_handler,
                "description": "Summarize prompt",
                "arguments": [{"name": "topic", "required": True}],
            }
        ]

        peer = Peer(name="agent", role="worker", mcp_port=8100, mcp_prompts=prompts)
        peer.start_mcp()

        mock_server_instance.register_prompt.assert_called_once_with(
            name="summarize",
            handler=prompt_handler,
            description="Summarize prompt",
            arguments=[{"name": "topic", "required": True}],
        )

    def test_stop_mcp_when_not_started(self):
        """stop_mcp() is safe to call when MCP was never started."""
        peer = Peer(name="agent", role="worker", mcp_port=8100)
        # Should not raise
        peer.stop_mcp()
        assert peer.mcp_server is None
        assert peer.mcp_clients is None

    @patch("a2a.mcp.client.MCPClientPool", autospec=False)
    @patch("a2a.mcp.server.MCPServerHost", autospec=False)
    @patch("a2a.mcp.loop_runner.AsyncLoopRunner", autospec=False)
    def test_stop_mcp_stops_components(self, MockRunner, MockServer, MockPool):
        """stop_mcp() stops server and loop runner."""
        mock_runner_instance = MagicMock()
        MockRunner.return_value = mock_runner_instance
        mock_server_instance = MagicMock()
        MockServer.return_value = mock_server_instance
        mock_pool_instance = MagicMock()
        MockPool.return_value = mock_pool_instance

        peer = Peer(name="agent", role="worker", mcp_port=8100)
        peer.start_mcp()
        peer.stop_mcp()

        mock_server_instance.stop.assert_called_once()
        mock_runner_instance.stop.assert_called_once()
        assert peer.mcp_server is None
        assert peer.mcp_clients is None


# --- MCP Convenience Method Tests ---


class TestPeerMCPConvenienceMethods:
    """Test register_tool, deregister_tool, invoke_remote_tool, read_remote_resource."""

    def test_register_tool_raises_when_not_started(self):
        """register_tool() raises RuntimeError if MCP not started."""
        peer = Peer(name="agent", role="worker", mcp_port=8100)
        with pytest.raises(RuntimeError, match="MCP server not started"):
            peer.register_tool("test", lambda: None)

    def test_deregister_tool_raises_when_not_started(self):
        """deregister_tool() raises RuntimeError if MCP not started."""
        peer = Peer(name="agent", role="worker", mcp_port=8100)
        with pytest.raises(RuntimeError, match="MCP server not started"):
            peer.deregister_tool("test")

    def test_invoke_remote_tool_raises_when_not_started(self):
        """invoke_remote_tool() raises RuntimeError if MCP not started."""
        peer = Peer(name="agent", role="worker", mcp_port=8100)
        with pytest.raises(RuntimeError, match="MCP not started"):
            peer.invoke_remote_tool("other", "tool", {})

    def test_read_remote_resource_raises_when_not_started(self):
        """read_remote_resource() raises RuntimeError if MCP not started."""
        peer = Peer(name="agent", role="worker", mcp_port=8100)
        with pytest.raises(RuntimeError, match="MCP not started"):
            peer.read_remote_resource("other", "data://test")

    @patch("a2a.mcp.client.MCPClientPool", autospec=False)
    @patch("a2a.mcp.server.MCPServerHost", autospec=False)
    @patch("a2a.mcp.loop_runner.AsyncLoopRunner", autospec=False)
    def test_register_tool_delegates_to_server(self, MockRunner, MockServer, MockPool):
        """register_tool() delegates to MCPServerHost.register_tool()."""
        mock_runner_instance = MagicMock()
        MockRunner.return_value = mock_runner_instance
        mock_server_instance = MagicMock()
        MockServer.return_value = mock_server_instance
        mock_pool_instance = MagicMock()
        MockPool.return_value = mock_pool_instance

        peer = Peer(name="agent", role="worker", mcp_port=8100)
        peer.start_mcp()

        handler = lambda: "result"
        peer.register_tool("my_tool", handler, description="A tool")

        mock_server_instance.register_tool.assert_called_with(
            name="my_tool",
            handler=handler,
            description="A tool",
            input_schema=None,
        )

    @patch("a2a.mcp.client.MCPClientPool", autospec=False)
    @patch("a2a.mcp.server.MCPServerHost", autospec=False)
    @patch("a2a.mcp.loop_runner.AsyncLoopRunner", autospec=False)
    def test_deregister_tool_delegates_to_server(self, MockRunner, MockServer, MockPool):
        """deregister_tool() delegates to MCPServerHost.deregister_tool()."""
        mock_runner_instance = MagicMock()
        MockRunner.return_value = mock_runner_instance
        mock_server_instance = MagicMock()
        MockServer.return_value = mock_server_instance
        mock_pool_instance = MagicMock()
        MockPool.return_value = mock_pool_instance

        peer = Peer(name="agent", role="worker", mcp_port=8100)
        peer.start_mcp()

        peer.deregister_tool("my_tool")
        mock_server_instance.deregister_tool.assert_called_with("my_tool")

    @patch("a2a.mcp.client.MCPClientPool", autospec=False)
    @patch("a2a.mcp.server.MCPServerHost", autospec=False)
    @patch("a2a.mcp.loop_runner.AsyncLoopRunner", autospec=False)
    def test_invoke_remote_tool_delegates_to_client_pool(
        self, MockRunner, MockServer, MockPool
    ):
        """invoke_remote_tool() uses loop_runner.run_coroutine() with client pool."""
        mock_runner_instance = MagicMock()
        MockRunner.return_value = mock_runner_instance
        mock_server_instance = MagicMock()
        MockServer.return_value = mock_server_instance
        mock_pool_instance = MagicMock()
        MockPool.return_value = mock_pool_instance

        # Simulate run_coroutine returning a result
        mock_runner_instance.run_coroutine.return_value = "tool_result"

        peer = Peer(name="agent", role="worker", mcp_port=8100)
        peer.start_mcp()

        result = peer.invoke_remote_tool("remote-agent", "search", {"query": "test"})
        assert result == "tool_result"
        mock_runner_instance.run_coroutine.assert_called_once()

    @patch("a2a.mcp.client.MCPClientPool", autospec=False)
    @patch("a2a.mcp.server.MCPServerHost", autospec=False)
    @patch("a2a.mcp.loop_runner.AsyncLoopRunner", autospec=False)
    def test_read_remote_resource_delegates_to_client_pool(
        self, MockRunner, MockServer, MockPool
    ):
        """read_remote_resource() uses loop_runner.run_coroutine() with client pool."""
        mock_runner_instance = MagicMock()
        MockRunner.return_value = mock_runner_instance
        mock_server_instance = MagicMock()
        MockServer.return_value = mock_server_instance
        mock_pool_instance = MagicMock()
        MockPool.return_value = mock_pool_instance

        mock_runner_instance.run_coroutine.return_value = "resource_content"

        peer = Peer(name="agent", role="worker", mcp_port=8100)
        peer.start_mcp()

        result = peer.read_remote_resource("remote-agent", "data://test")
        assert result == "resource_content"
        mock_runner_instance.run_coroutine.assert_called_once()
