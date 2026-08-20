"""
Zone Registry for MCP capability card management.

Maintains a per-zone registry of active MCP capability cards, providing
fast lookup of agents by tool name or resource URI. Emits events on
registration and deregistration to integrate with the existing EventHandler.
"""

from __future__ import annotations

import threading
from typing import Any

from a2a.events import EventHandler
from a2a.mcp.capabilities import CapabilityCard


class ZoneRegistry:
    """Maintains a registry of MCP capability cards within a zone.

    Thread-safe: all mutations are protected by a threading.Lock.
    Maintains internal indexes for O(1) tool lookups and resource lookups.
    """

    def __init__(self, zone_name: str, event_handler: EventHandler) -> None:
        """Initialize the registry for a specific zone.

        Args:
            zone_name: Name of the zone this registry is scoped to.
            event_handler: EventHandler instance for emitting lifecycle events.
        """
        self._zone_name = zone_name
        self._event_handler = event_handler
        self._lock = threading.Lock()

        # Internal state
        self._cards: dict[str, CapabilityCard] = {}  # agent_name -> card
        self._tool_index: dict[str, list[str]] = {}  # tool_name -> [agent_names]
        self._resource_index: dict[str, list[str]] = {}  # uri -> [agent_names]

    @property
    def zone_name(self) -> str:
        """The name of the zone this registry is scoped to."""
        return self._zone_name

    @property
    def agent_count(self) -> int:
        """The number of agents currently registered."""
        with self._lock:
            return len(self._cards)

    def register(self, card: CapabilityCard) -> None:
        """Register a capability card in the registry.

        Stores the card, updates indexes, and emits a "capability_added" event.

        Args:
            card: The CapabilityCard to register.
        """
        with self._lock:
            self._cards[card.agent_name] = card
            self._rebuild_indexes_for_agent(card)

        self._event_handler.emit("capability_added", card.to_dict())

    def deregister(self, agent_name: str) -> None:
        """Remove a capability card from the registry.

        Removes the card, cleans up indexes, and emits a "capability_removed" event.

        Args:
            agent_name: Name of the agent to deregister.
        """
        with self._lock:
            if agent_name not in self._cards:
                return
            del self._cards[agent_name]
            self._remove_agent_from_indexes(agent_name)

        self._event_handler.emit(
            "capability_removed",
            {"agent_name": agent_name, "zone_name": self._zone_name},
        )

    def update(self, card: CapabilityCard) -> None:
        """Update an existing capability card (or register if new).

        Replaces the existing card, rebuilds indexes, and emits "capability_added".

        Args:
            card: The updated CapabilityCard.
        """
        with self._lock:
            # Remove old index entries for this agent if it exists
            if card.agent_name in self._cards:
                self._remove_agent_from_indexes(card.agent_name)
            self._cards[card.agent_name] = card
            self._rebuild_indexes_for_agent(card)

        self._event_handler.emit("capability_added", card.to_dict())

    def get_card(self, agent_name: str) -> CapabilityCard | None:
        """Get the capability card for a specific agent.

        Args:
            agent_name: Name of the agent to look up.

        Returns:
            The CapabilityCard if found, None otherwise.
        """
        with self._lock:
            return self._cards.get(agent_name)

    def get_all_cards(self) -> list[CapabilityCard]:
        """Get all capability cards in this registry.

        Returns:
            List of all registered CapabilityCards.
        """
        with self._lock:
            return list(self._cards.values())

    def find_by_tool(self, tool_name: str) -> list[CapabilityCard]:
        """Find all agents that expose a tool with the given name.

        Uses the internal tool_index for O(1) lookup of agent names,
        then resolves to CapabilityCards.

        Args:
            tool_name: The name of the tool to search for.

        Returns:
            List of CapabilityCards that expose the specified tool.
        """
        with self._lock:
            agent_names = self._tool_index.get(tool_name, [])
            return [self._cards[name] for name in agent_names if name in self._cards]

    def find_by_resource(self, uri_pattern: str) -> list[CapabilityCard]:
        """Find all agents that expose a resource matching the URI pattern.

        Performs a substring match against registered resource URIs.

        Args:
            uri_pattern: Substring to match against resource URIs.

        Returns:
            List of CapabilityCards that have a resource matching the pattern.
        """
        with self._lock:
            matching_agents: set[str] = set()
            for uri, agent_names in self._resource_index.items():
                if uri_pattern in uri:
                    matching_agents.update(agent_names)
            return [
                self._cards[name] for name in matching_agents if name in self._cards
            ]

    # --- Private helpers (must be called with lock held) ---

    def _rebuild_indexes_for_agent(self, card: CapabilityCard) -> None:
        """Add agent entries to tool_index and resource_index from card."""
        for tool in card.tools:
            if tool.name not in self._tool_index:
                self._tool_index[tool.name] = []
            if card.agent_name not in self._tool_index[tool.name]:
                self._tool_index[tool.name].append(card.agent_name)

        for resource in card.resources:
            if resource.uri not in self._resource_index:
                self._resource_index[resource.uri] = []
            if card.agent_name not in self._resource_index[resource.uri]:
                self._resource_index[resource.uri].append(card.agent_name)

    def _remove_agent_from_indexes(self, agent_name: str) -> None:
        """Remove all index entries for an agent."""
        # Clean tool_index
        empty_tools: list[str] = []
        for tool_name, agents in self._tool_index.items():
            if agent_name in agents:
                agents.remove(agent_name)
            if not agents:
                empty_tools.append(tool_name)
        for tool_name in empty_tools:
            del self._tool_index[tool_name]

        # Clean resource_index
        empty_resources: list[str] = []
        for uri, agents in self._resource_index.items():
            if agent_name in agents:
                agents.remove(agent_name)
            if not agents:
                empty_resources.append(uri)
        for uri in empty_resources:
            del self._resource_index[uri]
