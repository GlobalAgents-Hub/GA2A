"""
Network discovery for GA2A — LAN peer discovery via UDP broadcast.

Each GA2A server instance broadcasts its presence periodically on the LAN.
Other instances listen for these broadcasts and maintain a registry of
known peers with their IPs, ports, zones, and agent counts.

The broadcast message includes the server's MCP endpoint so remote
agents can be discovered and invoked across machines.
"""
import socket
import json
import threading
import time
import logging
from typing import List, Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

# Default broadcast interval in seconds
_BROADCAST_INTERVAL = 5

# Peer is considered lost after this many missed broadcasts
_PEER_TIMEOUT_MULTIPLIER = 3


class NetworkDiscovery:
    """
    Handles peer and zone discovery across the LAN via UDP broadcast.

    Each GA2A server instance broadcasts a heartbeat containing:
      - instance_id: unique name for this server instance
      - host: IP address of this machine
      - port: MCP port of this server
      - mcp_endpoint: full URL to reach this server's MCP endpoint
      - zones: list of active zone names
      - agents: dict of zone_name -> [agent_names]
      - agent_details: dict of agent_name -> {role, tools, interests}
      - timestamp: when this heartbeat was sent

    Listeners on other machines receive these broadcasts and maintain
    a live map of the network.
    """

    def __init__(
        self,
        broadcast_port: int = 5060,
        broadcast_interval: float = _BROADCAST_INTERVAL,
    ):
        self.broadcast_port = broadcast_port
        self.broadcast_interval = broadcast_interval
        self._running = False

        # Our identity (set via set_identity before start)
        self._identity: dict[str, Any] = {}

        # Known remote peers: instance_id -> peer_info
        self._known_peers: Dict[str, Dict[str, Any]] = {}

        # Timestamps of last seen: instance_id -> float
        self._last_seen: Dict[str, float] = {}

        # Callbacks
        self._on_peer_discovered: Optional[Callable] = None
        self._on_peer_updated: Optional[Callable] = None
        self._on_peer_lost: Optional[Callable] = None

    def set_identity(
        self,
        instance_id: str,
        host: str,
        port: int,
        zones: list[str],
        agents: dict[str, list[str]],
        agent_details: dict[str, dict] = None,
    ) -> None:
        """Set this instance's identity for broadcasting.

        Args:
            instance_id: Unique name for this server instance.
            host: This machine's LAN IP address.
            port: The MCP port this server listens on.
            zones: List of active zone names.
            agents: Dict mapping zone_name -> list of agent_names.
            agent_details: Dict mapping agent_name -> {role, tools, interests}.
        """
        self._identity = {
            "instance_id": instance_id,
            "host": host,
            "port": port,
            "mcp_endpoint": f"http://{host}:{port}/mcp",
            "zones": zones,
            "agents": agents,
            "agent_details": agent_details or {},
        }

    def update_identity(self, **kwargs) -> None:
        """Update specific fields of the identity without replacing all."""
        self._identity.update(kwargs)
        # Recalculate endpoint if host or port changed
        if "host" in kwargs or "port" in kwargs:
            h = self._identity.get("host", "0.0.0.0")
            p = self._identity.get("port", 0)
            self._identity["mcp_endpoint"] = f"http://{h}:{p}/mcp"

    def on_peer_discovered(self, callback: Callable) -> None:
        """Register callback for when a new peer is discovered."""
        self._on_peer_discovered = callback

    def on_peer_updated(self, callback: Callable) -> None:
        """Register callback for when a known peer updates its info."""
        self._on_peer_updated = callback

    def on_peer_lost(self, callback: Callable) -> None:
        """Register callback for when a peer is lost (timeout)."""
        self._on_peer_lost = callback

    def start(self) -> None:
        """Start the discovery service (listener + broadcaster + reaper)."""
        self._running = True
        self._start_listener()
        self._start_broadcaster()
        self._start_reaper()
        logger.info(
            "NetworkDiscovery started (broadcast port %d, interval %.1fs)",
            self.broadcast_port, self.broadcast_interval
        )

    def stop(self) -> None:
        """Stop the discovery service."""
        self._running = False
        logger.info("NetworkDiscovery stopped")

    def get_peers(self) -> Dict[str, Dict[str, Any]]:
        """Return all currently known remote peers."""
        return dict(self._known_peers)

    def get_peer(self, instance_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific peer by instance_id."""
        return self._known_peers.get(instance_id)

    def find_remote_agent(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """Find which remote peer hosts a given agent.

        Returns peer_info with the MCP endpoint to reach that agent, or None.
        """
        for peer_id, peer in self._known_peers.items():
            all_agents = []
            for agents_list in peer.get("agents", {}).values():
                all_agents.extend(agents_list)
            if agent_name in all_agents:
                return peer
        return None

    def find_remote_agents_by_tool(self, tool_name: str) -> list[dict]:
        """Find remote agents that offer a specific tool.

        Returns list of {agent_name, role, peer_endpoint, peer_instance}.
        """
        results = []
        for peer_id, peer in self._known_peers.items():
            details = peer.get("agent_details", {})
            for agent_name, info in details.items():
                tools = info.get("tools", [])
                if tool_name in tools:
                    results.append({
                        "agent_name": agent_name,
                        "role": info.get("role", ""),
                        "tools": tools,
                        "peer_endpoint": peer.get("mcp_endpoint", ""),
                        "peer_instance": peer_id,
                        "peer_host": peer.get("host", ""),
                    })
        return results

    def find_peers(self, role: Optional[str] = None) -> List[str]:
        """Find peers in the network (backward compatible)."""
        if role:
            return [
                name for name, info in self._known_peers.items()
                if info.get("role") == role
            ]
        return list(self._known_peers.keys())

    def find_zones(self, topic: Optional[str] = None) -> List[str]:
        """Find all zones across the network."""
        all_zones = set()
        for peer in self._known_peers.values():
            all_zones.update(peer.get("zones", []))
        if topic:
            return [z for z in all_zones if topic.lower() in z.lower()]
        return list(all_zones)

    # ─── Private ────────────────────────────────────────────────

    def _start_listener(self) -> None:
        thread = threading.Thread(target=self._listen, daemon=True)
        thread.start()

    def _start_broadcaster(self) -> None:
        thread = threading.Thread(target=self._broadcast, daemon=True)
        thread.start()

    def _start_reaper(self) -> None:
        """Start a thread that removes peers that haven't been seen recently."""
        thread = threading.Thread(target=self._reap, daemon=True)
        thread.start()

    def _listen(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass  # SO_REUSEPORT not available on all systems
        sock.bind(("", self.broadcast_port))
        sock.settimeout(1)

        while self._running:
            try:
                data, addr = sock.recvfrom(65535)
                message = json.loads(data.decode())
                self._handle_broadcast(message, addr)
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.debug("Error in discovery listener: %s", e)

        sock.close()

    def _broadcast(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        while self._running:
            try:
                if self._identity:
                    message = {
                        "type": "ga2a_heartbeat",
                        "version": "0.3.0",
                        **self._identity,
                        "timestamp": time.time(),
                    }
                    payload = json.dumps(message).encode()
                    sock.sendto(payload, ("<broadcast>", self.broadcast_port))
            except Exception as e:
                if self._running:
                    logger.debug("Error in discovery broadcaster: %s", e)
            time.sleep(self.broadcast_interval)

        sock.close()

    def _reap(self) -> None:
        """Periodically check for peers that haven't sent a heartbeat recently."""
        timeout = self.broadcast_interval * _PEER_TIMEOUT_MULTIPLIER

        while self._running:
            time.sleep(self.broadcast_interval)
            now = time.time()
            lost = []
            for instance_id, last in list(self._last_seen.items()):
                if now - last > timeout:
                    lost.append(instance_id)

            for instance_id in lost:
                peer = self._known_peers.pop(instance_id, None)
                self._last_seen.pop(instance_id, None)
                if peer:
                    logger.info(
                        "🔴 Peer lost: %s (%s:%d)",
                        instance_id, peer.get("host", "?"), peer.get("port", 0)
                    )
                    if self._on_peer_lost:
                        try:
                            self._on_peer_lost(peer)
                        except Exception as e:
                            logger.debug("Error in on_peer_lost callback: %s", e)

    def _handle_broadcast(self, message: dict, addr: tuple) -> None:
        if message.get("type") != "ga2a_heartbeat":
            return

        instance_id = message.get("instance_id", "")
        if not instance_id:
            return

        # Don't register ourselves
        my_id = self._identity.get("instance_id", "")
        if instance_id == my_id:
            return

        # Use the sender's actual IP (from the UDP packet) if host is 0.0.0.0
        reported_host = message.get("host", "")
        if not reported_host or reported_host == "0.0.0.0":
            message["host"] = addr[0]
            # Recalculate endpoint with actual IP
            port = message.get("port", 0)
            message["mcp_endpoint"] = f"http://{addr[0]}:{port}/mcp"

        is_new = instance_id not in self._known_peers
        self._known_peers[instance_id] = message
        self._last_seen[instance_id] = time.time()

        if is_new:
            logger.info(
                "🟢 Peer discovered: %s at %s (zones: %s, agents: %s)",
                instance_id,
                message.get("mcp_endpoint", "?"),
                message.get("zones", []),
                sum(len(v) for v in message.get("agents", {}).values()),
            )
            if self._on_peer_discovered:
                try:
                    self._on_peer_discovered(message)
                except Exception as e:
                    logger.debug("Error in on_peer_discovered callback: %s", e)
        else:
            if self._on_peer_updated:
                try:
                    self._on_peer_updated(message)
                except Exception as e:
                    logger.debug("Error in on_peer_updated callback: %s", e)


def get_local_ip() -> str:
    """Get this machine's LAN IP address.

    Connects to a known external address (doesn't actually send data)
    to determine which local interface would be used.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
