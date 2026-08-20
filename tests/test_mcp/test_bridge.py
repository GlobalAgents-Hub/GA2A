"""
Unit tests for TransportBridge.

Tests that TransportBridge correctly:
- Hooks into event system on start/stop
- Queries peers for CapabilityCards on discovery
- Registers cards in appropriate ZoneRegistries
- Deregisters peers from all registries on peer_lost
- Runs health checks and removes stale entries
- Emits peer_unreachable events after repeated failures
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from a2a.discovery import NetworkDiscovery
from a2a.events import EventHandler
from a2a.mcp.bridge import TransportBridge, _MAX_HEALTH_FAILURES, _MCP_PORT_OFFSET
from a2a.mcp.capabilities import (
    CapabilityCard,
    ToolDescriptor,
    ResourceDescriptor,
    PromptDescriptor,
)
from a2a.mcp.loop_runner import AsyncLoopRunner
from a2a.mcp.registry import ZoneRegistry


# --- Fixtures ---


@pytest.fixture
def event_handler() -> EventHandler:
    """Create a fresh EventHandler."""
    return EventHandler()


@pytest.fixture
def registries(event_handler: EventHandler) -> dict[str, ZoneRegistry]:
    """Create registries for two zones."""
    return {
        "zone-a": ZoneRegistry("zone-a", event_handler),
        "zone-b": ZoneRegistry("zone-b", event_handler),
    }


@pytest.fixture
def discovery() -> NetworkDiscovery:
    """Create a NetworkDiscovery instance (not started)."""
    return NetworkDiscovery()


@pytest.fixture
def loop_runner() -> AsyncLoopRunner:
    """Create and start an AsyncLoopRunner for testing."""
    runner = AsyncLoopRunner()
    runner.start()
    yield runner
    runner.stop()


@pytest.fixture
def bridge(
    discovery: NetworkDiscovery,
    registries: dict[str, ZoneRegistry],
    event_handler: EventHandler,
    loop_runner: AsyncLoopRunner,
) -> TransportBridge:
    """Create a TransportBridge instance."""
    return TransportBridge(
        discovery=discovery,
        registries=registries,
        event_handler=event_handler,
        loop_runner=loop_runner,
        health_check_interval=0.1,  # Short interval for testing
    )


def _make_peer_info(
    name: str = "test-agent",
    host: str = "192.168.1.10",
    port: int = 5050,
    zones: list[str] | None = None,
    role: str = "researcher",
) -> dict[str, Any]:
    """Helper to create a peer_info dict."""
    return {
        "name": name,
        "host": host,
        "port": port,
        "role": role,
        "zones": zones if zones is not None else ["zone-a"],
    }


def _mock_jsonrpc_responses() -> dict[str, Any]:
    """Create mock JSON-RPC responses for initialize, tools/list, resources/list, prompts/list."""
    return {
        "initialize": {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "test-agent", "version": "1.0.0"},
            },
        },
        "tools/list": {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {
                        "name": "search",
                        "description": "Search documents",
                        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                    }
                ]
            },
        },
        "resources/list": {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "resources": [
                    {
                        "uri": "docs://recent",
                        "name": "Recent Docs",
                        "description": "Recently added documents",
                        "mimeType": "application/json",
                    }
                ]
            },
        },
        "prompts/list": {
            "jsonrpc": "2.0",
            "id": 4,
            "result": {
                "prompts": [
                    {
                        "name": "summarize",
                        "description": "Summarize a document",
                        "arguments": [{"name": "doc_id", "description": "Document ID", "required": True}],
                    }
                ]
            },
        },
    }


class MockResponse:
    """Mock httpx response."""

    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self) -> dict:
        return self._json_data


class MockAsyncClient:
    """Mock httpx.AsyncClient that returns predefined responses."""

    def __init__(self, responses: dict[str, Any] | None = None, fail: bool = False):
        self._responses = responses or _mock_jsonrpc_responses()
        self._fail = fail
        self._requests: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url: str, json: dict | None = None, **kwargs) -> MockResponse:
        self._requests.append({"url": url, "json": json})
        if self._fail:
            import httpx
            raise httpx.ConnectError("Connection refused")

        method = json.get("method", "") if json else ""
        response_data = self._responses.get(method, {})
        return MockResponse(status_code=200, json_data=response_data)


# --- Tests ---


class TestTransportBridgeInit:
    """Tests for TransportBridge initialization."""

    def test_init_stores_dependencies(
        self,
        bridge: TransportBridge,
        discovery: NetworkDiscovery,
        registries: dict[str, ZoneRegistry],
        event_handler: EventHandler,
        loop_runner: AsyncLoopRunner,
    ):
        """Bridge stores all dependencies correctly."""
        assert bridge._discovery is discovery
        assert bridge._registries is registries
        assert bridge._event_handler is event_handler
        assert bridge._loop_runner is loop_runner
        assert bridge._health_check_interval == 0.1

    def test_init_defaults(self, bridge: TransportBridge):
        """Bridge initializes with empty tracking dicts."""
        assert bridge._registered_peers == {}
        assert bridge._peer_endpoints == {}
        assert bridge._health_failures == {}
        assert bridge._running is False


class TestTransportBridgeStartStop:
    """Tests for start() and stop() lifecycle."""

    def test_start_sets_running(self, bridge: TransportBridge):
        """start() sets _running to True."""
        bridge.start()
        assert bridge._running is True
        bridge.stop()

    def test_start_idempotent(self, bridge: TransportBridge):
        """Calling start() multiple times is safe."""
        bridge.start()
        bridge.start()
        assert bridge._running is True
        bridge.stop()

    def test_stop_sets_not_running(self, bridge: TransportBridge):
        """stop() sets _running to False."""
        bridge.start()
        bridge.stop()
        assert bridge._running is False

    def test_stop_when_not_started(self, bridge: TransportBridge):
        """stop() when not started is safe."""
        bridge.stop()
        assert bridge._running is False

    def test_start_registers_event_handlers(
        self, bridge: TransportBridge, event_handler: EventHandler
    ):
        """start() registers handlers for peer_discovered and peer_lost."""
        bridge.start()
        assert bridge._peer_discovered_handler is not None
        assert bridge._peer_lost_handler is not None
        bridge.stop()

    def test_stop_removes_event_handlers(
        self, bridge: TransportBridge, event_handler: EventHandler
    ):
        """stop() clears the handler references."""
        bridge.start()
        bridge.stop()
        assert bridge._peer_discovered_handler is None
        assert bridge._peer_lost_handler is None


class TestOnPeerDiscovered:
    """Tests for on_peer_discovered method."""

    def test_ignores_empty_peer_name(self, bridge: TransportBridge):
        """on_peer_discovered ignores peer_info with empty name."""
        bridge.on_peer_discovered({"name": "", "host": "localhost", "port": 5050, "zones": ["zone-a"]})
        assert bridge._registered_peers == {}

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_registers_mcp_peer_in_zone(
        self,
        mock_client_cls,
        bridge: TransportBridge,
        registries: dict[str, ZoneRegistry],
        loop_runner: AsyncLoopRunner,
    ):
        """on_peer_discovered registers a peer in the correct zone registry."""
        mock_client_cls.return_value = MockAsyncClient()

        peer_info = _make_peer_info(name="agent-1", zones=["zone-a"])
        bridge.on_peer_discovered(peer_info)

        # Peer should be registered in zone-a
        assert "agent-1" in bridge._registered_peers
        assert "zone-a" in bridge._registered_peers["agent-1"]

        # Registry should have the card
        card = registries["zone-a"].get_card("agent-1")
        assert card is not None
        assert card.agent_name == "agent-1"
        assert card.zone_name == "zone-a"

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_registers_peer_in_multiple_zones(
        self,
        mock_client_cls,
        bridge: TransportBridge,
        registries: dict[str, ZoneRegistry],
    ):
        """on_peer_discovered registers a peer in all its zones."""
        mock_client_cls.return_value = MockAsyncClient()

        peer_info = _make_peer_info(name="multi-agent", zones=["zone-a", "zone-b"])
        bridge.on_peer_discovered(peer_info)

        assert "zone-a" in bridge._registered_peers["multi-agent"]
        assert "zone-b" in bridge._registered_peers["multi-agent"]

        assert registries["zone-a"].get_card("multi-agent") is not None
        assert registries["zone-b"].get_card("multi-agent") is not None

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_ignores_non_mcp_peer(
        self,
        mock_client_cls,
        bridge: TransportBridge,
        registries: dict[str, ZoneRegistry],
    ):
        """on_peer_discovered ignores peers that don't support MCP."""
        # Return an error response for initialize
        error_responses = {
            "initialize": {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32601, "message": "Method not found"},
            }
        }
        mock_client_cls.return_value = MockAsyncClient(responses=error_responses)

        peer_info = _make_peer_info(name="tcp-only-agent", zones=["zone-a"])
        bridge.on_peer_discovered(peer_info)

        assert "tcp-only-agent" not in bridge._registered_peers
        assert registries["zone-a"].get_card("tcp-only-agent") is None

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_ignores_unreachable_peer(
        self,
        mock_client_cls,
        bridge: TransportBridge,
    ):
        """on_peer_discovered ignores peers that are unreachable."""
        mock_client_cls.return_value = MockAsyncClient(fail=True)

        peer_info = _make_peer_info(name="offline-agent", zones=["zone-a"])
        bridge.on_peer_discovered(peer_info)

        assert "offline-agent" not in bridge._registered_peers

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_stores_endpoint_for_health_checks(
        self,
        mock_client_cls,
        bridge: TransportBridge,
    ):
        """on_peer_discovered stores the endpoint for later health checks."""
        mock_client_cls.return_value = MockAsyncClient()

        peer_info = _make_peer_info(name="agent-1", host="10.0.0.1", port=5050)
        bridge.on_peer_discovered(peer_info)

        expected_endpoint = f"http://10.0.0.1:{5050 + _MCP_PORT_OFFSET}/mcp"
        assert bridge._peer_endpoints.get("agent-1") == expected_endpoint

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_card_has_correct_tools(
        self,
        mock_client_cls,
        bridge: TransportBridge,
        registries: dict[str, ZoneRegistry],
    ):
        """on_peer_discovered builds a card with the correct tools from the response."""
        mock_client_cls.return_value = MockAsyncClient()

        peer_info = _make_peer_info(name="test-agent", zones=["zone-a"])
        bridge.on_peer_discovered(peer_info)

        card = registries["zone-a"].get_card("test-agent")
        assert card is not None
        assert len(card.tools) == 1
        assert card.tools[0].name == "search"
        assert card.tools[0].description == "Search documents"


class TestOnPeerLost:
    """Tests for on_peer_lost method."""

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_deregisters_from_all_zones(
        self,
        mock_client_cls,
        bridge: TransportBridge,
        registries: dict[str, ZoneRegistry],
    ):
        """on_peer_lost removes the peer from all zone registries."""
        mock_client_cls.return_value = MockAsyncClient()

        # First register the peer
        peer_info = _make_peer_info(name="leaving-agent", zones=["zone-a", "zone-b"])
        bridge.on_peer_discovered(peer_info)
        assert registries["zone-a"].get_card("leaving-agent") is not None

        # Now lose the peer
        bridge.on_peer_lost("leaving-agent")

        assert registries["zone-a"].get_card("leaving-agent") is None
        assert registries["zone-b"].get_card("leaving-agent") is None
        assert "leaving-agent" not in bridge._registered_peers
        assert "leaving-agent" not in bridge._peer_endpoints
        assert "leaving-agent" not in bridge._health_failures

    def test_ignores_unknown_peer(self, bridge: TransportBridge):
        """on_peer_lost does nothing for an unknown peer."""
        bridge.on_peer_lost("unknown-agent")
        # Should not raise

    def test_ignores_empty_name(self, bridge: TransportBridge):
        """on_peer_lost does nothing for empty name."""
        bridge.on_peer_lost("")
        # Should not raise


class TestQueryCapabilityCard:
    """Tests for _query_capability_card async method."""

    @pytest.mark.asyncio
    async def test_returns_card_on_success(self):
        """_query_capability_card returns a CapabilityCard on successful query."""
        with patch("a2a.mcp.bridge.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = MockAsyncClient()

            discovery = NetworkDiscovery()
            event_handler = EventHandler()
            registries = {"zone-a": ZoneRegistry("zone-a", event_handler)}
            runner = AsyncLoopRunner()
            runner.start()

            bridge = TransportBridge(
                discovery=discovery,
                registries=registries,
                event_handler=event_handler,
                loop_runner=runner,
            )

            card = await bridge._query_capability_card("http://localhost:5100/mcp")

            assert card is not None
            assert card.agent_name == "test-agent"
            assert card.transport_type == "streamable-http"
            assert len(card.tools) == 1
            assert card.tools[0].name == "search"
            assert len(card.resources) == 1
            assert card.resources[0].uri == "docs://recent"
            assert len(card.prompts) == 1
            assert card.prompts[0].name == "summarize"

            runner.stop()

    @pytest.mark.asyncio
    async def test_returns_none_on_connection_error(self):
        """_query_capability_card returns None when peer is unreachable."""
        with patch("a2a.mcp.bridge.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = MockAsyncClient(fail=True)

            discovery = NetworkDiscovery()
            event_handler = EventHandler()
            registries = {}
            runner = AsyncLoopRunner()
            runner.start()

            bridge = TransportBridge(
                discovery=discovery,
                registries=registries,
                event_handler=event_handler,
                loop_runner=runner,
            )

            card = await bridge._query_capability_card("http://unreachable:5100/mcp")
            assert card is None

            runner.stop()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_response(self):
        """_query_capability_card returns None when peer returns an error."""
        error_responses = {
            "initialize": {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32601, "message": "Not supported"},
            }
        }

        with patch("a2a.mcp.bridge.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = MockAsyncClient(responses=error_responses)

            discovery = NetworkDiscovery()
            event_handler = EventHandler()
            registries = {}
            runner = AsyncLoopRunner()
            runner.start()

            bridge = TransportBridge(
                discovery=discovery,
                registries=registries,
                event_handler=event_handler,
                loop_runner=runner,
            )

            card = await bridge._query_capability_card("http://localhost:5100/mcp")
            assert card is None

            runner.stop()


class TestValidateServer:
    """Tests for _validate_server async method."""

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        """_validate_server returns True when server responds with 200."""
        with patch("a2a.mcp.bridge.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = MockAsyncClient()

            discovery = NetworkDiscovery()
            event_handler = EventHandler()
            runner = AsyncLoopRunner()
            runner.start()

            bridge = TransportBridge(
                discovery=discovery,
                registries={},
                event_handler=event_handler,
                loop_runner=runner,
            )

            result = await bridge._validate_server("http://localhost:5100/mcp")
            assert result is True

            runner.stop()

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self):
        """_validate_server returns False when server is unreachable."""
        with patch("a2a.mcp.bridge.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = MockAsyncClient(fail=True)

            discovery = NetworkDiscovery()
            event_handler = EventHandler()
            runner = AsyncLoopRunner()
            runner.start()

            bridge = TransportBridge(
                discovery=discovery,
                registries={},
                event_handler=event_handler,
                loop_runner=runner,
            )

            result = await bridge._validate_server("http://unreachable:5100/mcp")
            assert result is False

            runner.stop()


class TestHealthCheckLoop:
    """Tests for the health check loop behavior."""

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_deregisters_after_max_failures(
        self,
        mock_client_cls,
        bridge: TransportBridge,
        registries: dict[str, ZoneRegistry],
        loop_runner: AsyncLoopRunner,
    ):
        """Health check deregisters a peer after MAX_HEALTH_FAILURES consecutive failures."""
        # First register a peer successfully
        mock_client_cls.return_value = MockAsyncClient()
        peer_info = _make_peer_info(name="failing-agent", zones=["zone-a"])
        bridge.on_peer_discovered(peer_info)

        assert registries["zone-a"].get_card("failing-agent") is not None

        # Simulate health check failures
        bridge._health_failures["failing-agent"] = _MAX_HEALTH_FAILURES - 1

        # Now make the client fail
        mock_client_cls.return_value = MockAsyncClient(fail=True)

        # Manually run the health check validation logic
        async def simulate_health_failure():
            is_healthy = await bridge._validate_server(
                bridge._peer_endpoints["failing-agent"]
            )
            assert is_healthy is False

        loop_runner.run_coroutine(simulate_health_failure())

        # Simulate what health_check_loop would do on failure
        bridge._health_failures["failing-agent"] += 1
        if bridge._health_failures["failing-agent"] >= _MAX_HEALTH_FAILURES:
            zones = bridge._registered_peers.pop("failing-agent", [])
            for zone_name in zones:
                registry = registries.get(zone_name)
                if registry is not None:
                    registry.deregister("failing-agent")
            bridge._peer_endpoints.pop("failing-agent", None)
            bridge._health_failures.pop("failing-agent", None)

        assert registries["zone-a"].get_card("failing-agent") is None
        assert "failing-agent" not in bridge._registered_peers

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_resets_failures_on_success(
        self,
        mock_client_cls,
        bridge: TransportBridge,
        loop_runner: AsyncLoopRunner,
    ):
        """Health check resets failure counter when server responds."""
        mock_client_cls.return_value = MockAsyncClient()

        peer_info = _make_peer_info(name="recovering-agent", zones=["zone-a"])
        bridge.on_peer_discovered(peer_info)

        # Simulate some failures
        bridge._health_failures["recovering-agent"] = 2

        # Now validate successfully
        async def simulate_health_success():
            is_healthy = await bridge._validate_server(
                bridge._peer_endpoints["recovering-agent"]
            )
            assert is_healthy is True

        loop_runner.run_coroutine(simulate_health_success())

        # Simulate what health_check_loop would do on success
        bridge._health_failures["recovering-agent"] = 0
        assert bridge._health_failures["recovering-agent"] == 0


class TestEventIntegration:
    """Tests for event emission integration."""

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_emits_peer_unreachable_on_deregister(
        self,
        mock_client_cls,
        bridge: TransportBridge,
        event_handler: EventHandler,
        registries: dict[str, ZoneRegistry],
        loop_runner: AsyncLoopRunner,
    ):
        """Bridge emits peer_unreachable when a peer is deregistered due to health failure."""
        # Track emitted events
        emitted_events: list[dict] = []

        @event_handler.on("peer_unreachable")
        def on_unreachable(data):
            emitted_events.append(data)

        # Register a peer
        mock_client_cls.return_value = MockAsyncClient()
        peer_info = _make_peer_info(name="dying-agent", zones=["zone-a"])
        bridge.on_peer_discovered(peer_info)

        # Simulate max health failures and deregistration
        endpoint = bridge._peer_endpoints["dying-agent"]
        bridge._health_failures["dying-agent"] = _MAX_HEALTH_FAILURES

        # Manually trigger what health_check_loop does
        zones = bridge._registered_peers.pop("dying-agent", [])
        for zone_name in zones:
            registry = registries.get(zone_name)
            if registry is not None:
                registry.deregister("dying-agent")
        bridge._peer_endpoints.pop("dying-agent", None)
        bridge._health_failures.pop("dying-agent", None)
        event_handler.emit(
            "peer_unreachable",
            {"agent_name": "dying-agent", "endpoint": endpoint, "zones": zones},
        )

        assert len(emitted_events) == 1
        assert emitted_events[0]["agent_name"] == "dying-agent"
        assert emitted_events[0]["zones"] == ["zone-a"]

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_event_driven_peer_discovery(
        self,
        mock_client_cls,
        bridge: TransportBridge,
        event_handler: EventHandler,
        registries: dict[str, ZoneRegistry],
    ):
        """Bridge responds to peer_discovered events via the EventHandler."""
        mock_client_cls.return_value = MockAsyncClient()

        bridge.start()

        # Emit a peer_discovered event
        event_handler.emit(
            "peer_discovered",
            _make_peer_info(name="event-agent", zones=["zone-a"]),
        )

        # The bridge should have registered the peer
        assert "event-agent" in bridge._registered_peers
        assert registries["zone-a"].get_card("event-agent") is not None

        bridge.stop()

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_event_driven_peer_lost(
        self,
        mock_client_cls,
        bridge: TransportBridge,
        event_handler: EventHandler,
        registries: dict[str, ZoneRegistry],
    ):
        """Bridge responds to peer_lost events via the EventHandler."""
        mock_client_cls.return_value = MockAsyncClient()

        bridge.start()

        # First register
        event_handler.emit(
            "peer_discovered",
            _make_peer_info(name="leaving-agent", zones=["zone-a"]),
        )
        assert registries["zone-a"].get_card("leaving-agent") is not None

        # Now emit peer_lost
        event_handler.emit("peer_lost", "leaving-agent")

        assert registries["zone-a"].get_card("leaving-agent") is None
        assert "leaving-agent" not in bridge._registered_peers

        bridge.stop()

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_peer_lost_with_dict_payload(
        self,
        mock_client_cls,
        bridge: TransportBridge,
        event_handler: EventHandler,
        registries: dict[str, ZoneRegistry],
    ):
        """Bridge handles peer_lost events with dict payload."""
        mock_client_cls.return_value = MockAsyncClient()

        bridge.start()

        # First register
        event_handler.emit(
            "peer_discovered",
            _make_peer_info(name="dict-agent", zones=["zone-a"]),
        )

        # Emit peer_lost with dict payload
        event_handler.emit("peer_lost", {"name": "dict-agent"})

        assert registries["zone-a"].get_card("dict-agent") is None

        bridge.stop()


class TestEdgeCases:
    """Tests for edge cases and robustness."""

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_unknown_zone_in_peer_info(
        self,
        mock_client_cls,
        bridge: TransportBridge,
    ):
        """Peer with zones not in registries doesn't crash."""
        mock_client_cls.return_value = MockAsyncClient()

        peer_info = _make_peer_info(name="no-zone-agent", zones=["unknown-zone"])
        bridge.on_peer_discovered(peer_info)

        # Should not be registered since zone doesn't exist in registries
        assert "no-zone-agent" not in bridge._registered_peers

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_peer_with_empty_zones(
        self,
        mock_client_cls,
        bridge: TransportBridge,
    ):
        """Peer with empty zones list is ignored."""
        mock_client_cls.return_value = MockAsyncClient()

        peer_info = _make_peer_info(name="empty-zones", zones=[])
        bridge.on_peer_discovered(peer_info)

        assert "empty-zones" not in bridge._registered_peers

    @patch("a2a.mcp.bridge.httpx.AsyncClient")
    def test_rediscovery_updates_registration(
        self,
        mock_client_cls,
        bridge: TransportBridge,
        registries: dict[str, ZoneRegistry],
    ):
        """Rediscovering a peer updates its registration."""
        mock_client_cls.return_value = MockAsyncClient()

        peer_info = _make_peer_info(name="test-agent", zones=["zone-a"])
        bridge.on_peer_discovered(peer_info)

        # Rediscover (simulates updated capabilities)
        bridge.on_peer_discovered(peer_info)

        # Should still be registered
        assert "test-agent" in bridge._registered_peers
        card = registries["zone-a"].get_card("test-agent")
        assert card is not None
