"""
Transport Bridge — translates P2P discovery events into MCP registrations.

Hooks into the existing NetworkDiscovery event system to detect new peers,
queries them for their CapabilityCard via JSON-RPC 2.0 over HTTP, and registers
them in the appropriate ZoneRegistries. Periodically validates registered servers
and removes stale entries.

Requirements: 5.1, 5.2, 5.3, 5.4, 11.3
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from a2a.discovery import NetworkDiscovery
from a2a.events import EventHandler
from a2a.mcp.capabilities import CapabilityCard
from a2a.mcp.loop_runner import AsyncLoopRunner
from a2a.mcp.registry import ZoneRegistry

logger = logging.getLogger(__name__)

# Default offset added to a peer's base port to find its MCP endpoint.
_MCP_PORT_OFFSET = 50

# Maximum consecutive health-check failures before deregistering a peer.
_MAX_HEALTH_FAILURES = 3

# Timeout for HTTP requests to MCP endpoints.
_REQUEST_TIMEOUT = 5.0


class TransportBridge:
    """Bridges NetworkDiscovery events to ZoneRegistry operations.

    When the P2P discovery layer detects a new peer, the bridge queries
    that peer for its MCP CapabilityCard and — if successful — registers
    it in the appropriate zone registries. When a peer disappears, the
    bridge deregisters it from all registries.

    A background health-check loop periodically validates that registered
    MCP servers are still reachable and removes stale entries.
    """

    def __init__(
        self,
        discovery: NetworkDiscovery,
        registries: dict[str, ZoneRegistry],
        event_handler: EventHandler,
        loop_runner: AsyncLoopRunner,
        health_check_interval: float = 30.0,
    ) -> None:
        """Initialize the TransportBridge.

        Args:
            discovery: The NetworkDiscovery instance to hook into.
            registries: Dict mapping zone_name -> ZoneRegistry for registration.
            event_handler: EventHandler for emitting lifecycle events.
            loop_runner: AsyncLoopRunner for scheduling async operations.
            health_check_interval: Seconds between health check cycles.
        """
        self._discovery = discovery
        self._registries = registries
        self._event_handler = event_handler
        self._loop_runner = loop_runner
        self._health_check_interval = health_check_interval

        # Tracks registered peers: peer_name -> list of zone_names
        self._registered_peers: dict[str, list[str]] = {}

        # Tracks peer endpoints for health checks: peer_name -> endpoint
        self._peer_endpoints: dict[str, str] = {}

        # Health check failure counters: peer_name -> consecutive failure count
        self._health_failures: dict[str, int] = {}

        # Event handler references for cleanup
        self._peer_discovered_handler: Any = None
        self._peer_lost_handler: Any = None

        # Health check loop task
        self._health_check_task: asyncio.Task[None] | None = None
        self._running = False

    def start(self) -> None:
        """Start the transport bridge.

        Hooks into NetworkDiscovery's event system to listen for
        peer_discovered and peer_lost events. Starts the background
        health check loop in the AsyncLoopRunner.
        """
        if self._running:
            return

        self._running = True

        # Register event handlers with the discovery's event handler
        @self._event_handler.on("peer_discovered")
        def _on_discovered(data: Any) -> None:
            self.on_peer_discovered(data)

        @self._event_handler.on("peer_lost")
        def _on_lost(data: Any) -> None:
            if isinstance(data, str):
                self.on_peer_lost(data)
            elif isinstance(data, dict):
                self.on_peer_lost(data.get("name", ""))

        self._peer_discovered_handler = _on_discovered
        self._peer_lost_handler = _on_lost

        # Start the health check loop
        self._health_check_task = self._loop_runner.schedule(
            self._health_check_loop()
        )

        logger.info("TransportBridge started")

    def stop(self) -> None:
        """Stop the transport bridge.

        Removes event hooks and cancels the health check loop.
        """
        if not self._running:
            return

        self._running = False

        # Remove event handlers
        if self._peer_discovered_handler is not None:
            self._event_handler.remove_handler(
                "peer_discovered", self._peer_discovered_handler
            )
            self._peer_discovered_handler = None

        if self._peer_lost_handler is not None:
            self._event_handler.remove_handler(
                "peer_lost", self._peer_lost_handler
            )
            self._peer_lost_handler = None

        # Cancel the health check loop
        if self._health_check_task is not None:
            self._health_check_task.cancel()
            self._health_check_task = None

        logger.info("TransportBridge stopped")

    def on_peer_discovered(self, peer_info: dict[str, Any]) -> None:
        """Handle a newly discovered peer.

        Queries the peer's MCP endpoint for its CapabilityCard. If successful,
        registers the card in each ZoneRegistry the peer belongs to.

        Args:
            peer_info: Dict with keys: name, role, port, host, zones (list).
        """
        peer_name = peer_info.get("name", "")
        if not peer_name:
            return

        host = peer_info.get("host", "localhost")
        port = peer_info.get("port", 5050)
        zones = peer_info.get("zones", [])

        # Build the MCP endpoint from peer's base port + offset
        mcp_port = port + _MCP_PORT_OFFSET
        endpoint = f"http://{host}:{mcp_port}/mcp"

        # Query the peer asynchronously for its capability card
        try:
            card = self._loop_runner.run_coroutine(
                self._query_capability_card(endpoint)
            )
        except Exception as e:
            logger.debug(
                "Failed to query capability card from peer '%s' at %s: %s",
                peer_name,
                endpoint,
                e,
            )
            return

        if card is None:
            # Non-MCP peer — ignore silently
            return

        # Register in each zone the peer belongs to.
        # Use the peer's discovery name as the canonical agent_name
        # (Requirement 5.3: translate between GA2A peer identity and MCP metadata).
        registered_zones: list[str] = []
        for zone_name in zones:
            registry = self._registries.get(zone_name)
            if registry is not None:
                zone_card = CapabilityCard(
                    agent_name=peer_name,
                    agent_role=card.agent_role,
                    endpoint=card.endpoint,
                    transport_type=card.transport_type,
                    zone_name=zone_name,
                    tools=card.tools,
                    resources=card.resources,
                    prompts=card.prompts,
                    published_at=card.published_at,
                    version=card.version,
                )
                registry.register(zone_card)
                registered_zones.append(zone_name)

        if registered_zones:
            self._registered_peers[peer_name] = registered_zones
            self._peer_endpoints[peer_name] = endpoint
            self._health_failures[peer_name] = 0
            logger.info(
                "Registered MCP peer '%s' in zones: %s",
                peer_name,
                registered_zones,
            )

    def on_peer_lost(self, peer_name: str) -> None:
        """Handle a peer leaving the network.

        Deregisters the peer from all ZoneRegistries where it was registered.

        Args:
            peer_name: Name of the peer that left.
        """
        if not peer_name:
            return

        zones = self._registered_peers.pop(peer_name, [])
        for zone_name in zones:
            registry = self._registries.get(zone_name)
            if registry is not None:
                registry.deregister(peer_name)

        self._peer_endpoints.pop(peer_name, None)
        self._health_failures.pop(peer_name, None)

        if zones:
            logger.info(
                "Deregistered peer '%s' from zones: %s", peer_name, zones
            )

    async def _query_capability_card(
        self, peer_endpoint: str
    ) -> CapabilityCard | None:
        """Query a peer's MCP endpoint for its CapabilityCard.

        Sends a JSON-RPC 2.0 initialize request to the peer and parses
        the response to build a CapabilityCard. Returns None if the peer
        is unreachable or does not support MCP.

        Args:
            peer_endpoint: The full URL of the peer's MCP endpoint.

        Returns:
            A CapabilityCard if the peer supports MCP, None otherwise.
        """
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                # Send initialize request
                init_response = await client.post(
                    peer_endpoint,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {},
                    },
                )

                if init_response.status_code != 200:
                    return None

                init_data = init_response.json()
                if "error" in init_data:
                    return None

                server_info = init_data.get("result", {}).get("serverInfo", {})
                agent_name = server_info.get("name", "")

                if not agent_name:
                    return None

                # Query tools list
                tools_response = await client.post(
                    peer_endpoint,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/list",
                        "params": {},
                    },
                )
                tools_data = (
                    tools_response.json().get("result", {}).get("tools", [])
                    if tools_response.status_code == 200
                    else []
                )

                # Query resources list
                resources_response = await client.post(
                    peer_endpoint,
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "resources/list",
                        "params": {},
                    },
                )
                resources_data = (
                    resources_response.json()
                    .get("result", {})
                    .get("resources", [])
                    if resources_response.status_code == 200
                    else []
                )

                # Query prompts list
                prompts_response = await client.post(
                    peer_endpoint,
                    json={
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "prompts/list",
                        "params": {},
                    },
                )
                prompts_data = (
                    prompts_response.json()
                    .get("result", {})
                    .get("prompts", [])
                    if prompts_response.status_code == 200
                    else []
                )

                # Build CapabilityCard from responses
                from a2a.mcp.capabilities import (
                    PromptDescriptor,
                    ResourceDescriptor,
                    ToolDescriptor,
                )

                tools = [
                    ToolDescriptor(
                        name=t.get("name", ""),
                        description=t.get("description", ""),
                        input_schema=t.get("inputSchema", t.get("input_schema", {})),
                    )
                    for t in tools_data
                ]

                resources = [
                    ResourceDescriptor(
                        uri=r.get("uri", ""),
                        name=r.get("name", ""),
                        description=r.get("description", ""),
                        mime_type=r.get("mimeType", r.get("mime_type", "text/plain")),
                    )
                    for r in resources_data
                ]

                prompts = [
                    PromptDescriptor(
                        name=p.get("name", ""),
                        description=p.get("description", ""),
                        arguments=p.get("arguments", []),
                    )
                    for p in prompts_data
                ]

                from datetime import datetime, timezone

                return CapabilityCard(
                    agent_name=agent_name,
                    agent_role="agent",
                    endpoint=peer_endpoint,
                    transport_type="streamable-http",
                    zone_name="",  # Set per-zone during registration
                    tools=tools,
                    resources=resources,
                    prompts=prompts,
                    published_at=datetime.now(timezone.utc).isoformat(),
                    version=1,
                )

        except (httpx.RequestError, httpx.TimeoutException, ValueError, KeyError) as e:
            logger.debug(
                "Could not query capability card from %s: %s", peer_endpoint, e
            )
            return None

    async def _health_check_loop(self) -> None:
        """Periodically validate that registered MCP servers are reachable.

        Runs every health_check_interval seconds. For each registered peer,
        sends a validation request. If a server fails to respond 3 times
        consecutively, deregisters it and emits a "peer_unreachable" event.
        """
        while self._running:
            try:
                await asyncio.sleep(self._health_check_interval)
            except asyncio.CancelledError:
                return

            if not self._running:
                return

            # Take a snapshot of current peers to check
            peers_to_check = list(self._peer_endpoints.items())

            for peer_name, endpoint in peers_to_check:
                if not self._running:
                    return

                try:
                    is_healthy = await self._validate_server(endpoint)
                except asyncio.CancelledError:
                    return

                if is_healthy:
                    self._health_failures[peer_name] = 0
                else:
                    failures = self._health_failures.get(peer_name, 0) + 1
                    self._health_failures[peer_name] = failures

                    if failures >= _MAX_HEALTH_FAILURES:
                        logger.warning(
                            "Peer '%s' unreachable after %d failures, deregistering",
                            peer_name,
                            failures,
                        )
                        # Deregister from all zones
                        zones = self._registered_peers.pop(peer_name, [])
                        for zone_name in zones:
                            registry = self._registries.get(zone_name)
                            if registry is not None:
                                registry.deregister(peer_name)

                        self._peer_endpoints.pop(peer_name, None)
                        self._health_failures.pop(peer_name, None)

                        # Emit peer_unreachable event
                        self._event_handler.emit(
                            "peer_unreachable",
                            {
                                "agent_name": peer_name,
                                "endpoint": endpoint,
                                "zones": zones,
                            },
                        )

    async def _validate_server(self, endpoint: str) -> bool:
        """Check if an MCP server is reachable.

        Sends a JSON-RPC 2.0 initialize request with a short timeout.

        Args:
            endpoint: The MCP endpoint URL to validate.

        Returns:
            True if the server responds successfully, False otherwise.
        """
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                response = await client.post(
                    endpoint,
                    json={
                        "jsonrpc": "2.0",
                        "id": 0,
                        "method": "initialize",
                        "params": {},
                    },
                )
                return response.status_code == 200
        except (httpx.RequestError, httpx.TimeoutException):
            return False
