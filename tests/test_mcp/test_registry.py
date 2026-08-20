"""Unit tests for ZoneRegistry."""

import threading

import pytest

from a2a.events import EventHandler
from a2a.mcp.capabilities import (
    CapabilityCard,
    ResourceDescriptor,
    ToolDescriptor,
)
from a2a.mcp.registry import ZoneRegistry


def _make_card(
    agent_name: str = "agent-a",
    zone_name: str = "test-zone",
    tools: list[ToolDescriptor] | None = None,
    resources: list[ResourceDescriptor] | None = None,
) -> CapabilityCard:
    """Helper to build a CapabilityCard for testing."""
    return CapabilityCard(
        agent_name=agent_name,
        agent_role="worker",
        endpoint=f"http://localhost:5100/mcp",
        transport_type="streamable-http",
        zone_name=zone_name,
        tools=tools or [],
        resources=resources or [],
        prompts=[],
        published_at="2024-12-01T10:00:00Z",
        version=1,
    )


class TestZoneRegistryInit:
    """Tests for ZoneRegistry initialization."""

    def test_zone_name_property(self):
        eh = EventHandler()
        registry = ZoneRegistry("my-zone", eh)
        assert registry.zone_name == "my-zone"

    def test_agent_count_starts_at_zero(self):
        eh = EventHandler()
        registry = ZoneRegistry("z", eh)
        assert registry.agent_count == 0

    def test_get_all_cards_empty(self):
        eh = EventHandler()
        registry = ZoneRegistry("z", eh)
        assert registry.get_all_cards() == []


class TestZoneRegistryRegister:
    """Tests for register() method."""

    def test_register_stores_card(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        card = _make_card("agent-a")

        registry.register(card)

        assert registry.get_card("agent-a") == card
        assert registry.agent_count == 1

    def test_register_multiple_agents(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)

        registry.register(_make_card("agent-a"))
        registry.register(_make_card("agent-b"))
        registry.register(_make_card("agent-c"))

        assert registry.agent_count == 3
        assert len(registry.get_all_cards()) == 3

    def test_register_emits_capability_added_event(self):
        eh = EventHandler()
        events_received = []

        @eh.on("capability_added")
        def handler(data):
            events_received.append(data)

        registry = ZoneRegistry("test-zone", eh)
        card = _make_card("agent-a")
        registry.register(card)

        assert len(events_received) == 1
        assert events_received[0]["agent_name"] == "agent-a"

    def test_register_indexes_tools(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        card = _make_card(
            "agent-a",
            tools=[
                ToolDescriptor(name="search", description="Search things", input_schema={}),
                ToolDescriptor(name="calculate", description="Do math", input_schema={}),
            ],
        )
        registry.register(card)

        results = registry.find_by_tool("search")
        assert len(results) == 1
        assert results[0].agent_name == "agent-a"

    def test_register_indexes_resources(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        card = _make_card(
            "agent-a",
            resources=[
                ResourceDescriptor(
                    uri="papers://recent",
                    name="Recent Papers",
                    description="Papers",
                    mime_type="application/json",
                ),
            ],
        )
        registry.register(card)

        results = registry.find_by_resource("papers://recent")
        assert len(results) == 1
        assert results[0].agent_name == "agent-a"


class TestZoneRegistryDeregister:
    """Tests for deregister() method."""

    def test_deregister_removes_card(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        registry.register(_make_card("agent-a"))

        registry.deregister("agent-a")

        assert registry.get_card("agent-a") is None
        assert registry.agent_count == 0

    def test_deregister_emits_capability_removed_event(self):
        eh = EventHandler()
        events_received = []

        @eh.on("capability_removed")
        def handler(data):
            events_received.append(data)

        registry = ZoneRegistry("test-zone", eh)
        registry.register(_make_card("agent-a"))
        registry.deregister("agent-a")

        assert len(events_received) == 1
        assert events_received[0] == {
            "agent_name": "agent-a",
            "zone_name": "test-zone",
        }

    def test_deregister_cleans_tool_index(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        card = _make_card(
            "agent-a",
            tools=[ToolDescriptor(name="search", description="Search", input_schema={})],
        )
        registry.register(card)
        registry.deregister("agent-a")

        assert registry.find_by_tool("search") == []

    def test_deregister_cleans_resource_index(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        card = _make_card(
            "agent-a",
            resources=[
                ResourceDescriptor(
                    uri="data://logs",
                    name="Logs",
                    description="System logs",
                    mime_type="text/plain",
                ),
            ],
        )
        registry.register(card)
        registry.deregister("agent-a")

        assert registry.find_by_resource("data://logs") == []

    def test_deregister_nonexistent_agent_is_noop(self):
        eh = EventHandler()
        events_received = []

        @eh.on("capability_removed")
        def handler(data):
            events_received.append(data)

        registry = ZoneRegistry("test-zone", eh)
        registry.deregister("ghost-agent")

        # No event should be emitted
        assert len(events_received) == 0

    def test_deregister_does_not_affect_other_agents(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        tool = ToolDescriptor(name="shared_tool", description="Shared", input_schema={})
        registry.register(_make_card("agent-a", tools=[tool]))
        registry.register(_make_card("agent-b", tools=[tool]))

        registry.deregister("agent-a")

        results = registry.find_by_tool("shared_tool")
        assert len(results) == 1
        assert results[0].agent_name == "agent-b"


class TestZoneRegistryUpdate:
    """Tests for update() method."""

    def test_update_replaces_card(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        card_v1 = _make_card("agent-a")
        registry.register(card_v1)

        card_v2 = _make_card(
            "agent-a",
            tools=[ToolDescriptor(name="new_tool", description="New", input_schema={})],
        )
        registry.update(card_v2)

        assert registry.get_card("agent-a") == card_v2
        assert registry.agent_count == 1

    def test_update_emits_capability_added_event(self):
        eh = EventHandler()
        events_received = []

        @eh.on("capability_added")
        def handler(data):
            events_received.append(data)

        registry = ZoneRegistry("test-zone", eh)
        card = _make_card("agent-a")
        registry.register(card)
        events_received.clear()

        card_v2 = _make_card(
            "agent-a",
            tools=[ToolDescriptor(name="updated", description="Updated", input_schema={})],
        )
        registry.update(card_v2)

        assert len(events_received) == 1
        assert events_received[0]["agent_name"] == "agent-a"

    def test_update_rebuilds_indexes(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        card_v1 = _make_card(
            "agent-a",
            tools=[ToolDescriptor(name="old_tool", description="Old", input_schema={})],
        )
        registry.register(card_v1)

        card_v2 = _make_card(
            "agent-a",
            tools=[ToolDescriptor(name="new_tool", description="New", input_schema={})],
        )
        registry.update(card_v2)

        assert registry.find_by_tool("old_tool") == []
        assert len(registry.find_by_tool("new_tool")) == 1

    def test_update_new_agent_acts_as_register(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        card = _make_card("new-agent")
        registry.update(card)

        assert registry.get_card("new-agent") == card
        assert registry.agent_count == 1


class TestZoneRegistryQueries:
    """Tests for query methods."""

    def test_get_card_returns_none_for_unknown(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        assert registry.get_card("nonexistent") is None

    def test_find_by_tool_multiple_agents(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        tool = ToolDescriptor(name="shared_tool", description="Shared", input_schema={})
        registry.register(_make_card("agent-a", tools=[tool]))
        registry.register(_make_card("agent-b", tools=[tool]))

        results = registry.find_by_tool("shared_tool")
        names = [c.agent_name for c in results]
        assert "agent-a" in names
        assert "agent-b" in names

    def test_find_by_tool_no_match(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        registry.register(_make_card("agent-a"))
        assert registry.find_by_tool("nonexistent_tool") == []

    def test_find_by_resource_substring_match(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        card = _make_card(
            "agent-a",
            resources=[
                ResourceDescriptor(
                    uri="papers://recent/2024",
                    name="Papers 2024",
                    description="Recent papers",
                    mime_type="application/json",
                ),
            ],
        )
        registry.register(card)

        # Substring match
        assert len(registry.find_by_resource("papers://recent")) == 1
        assert len(registry.find_by_resource("recent/2024")) == 1
        assert len(registry.find_by_resource("2024")) == 1

    def test_find_by_resource_no_match(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        registry.register(_make_card("agent-a"))
        assert registry.find_by_resource("nonexistent://uri") == []


class TestZoneRegistryThreadSafety:
    """Tests verifying thread-safe behavior."""

    def test_concurrent_register_deregister(self):
        eh = EventHandler()
        registry = ZoneRegistry("test-zone", eh)
        errors = []

        def register_agents(start: int, count: int):
            try:
                for i in range(start, start + count):
                    registry.register(_make_card(f"agent-{i}"))
            except Exception as e:
                errors.append(e)

        def deregister_agents(start: int, count: int):
            try:
                for i in range(start, start + count):
                    registry.deregister(f"agent-{i}")
            except Exception as e:
                errors.append(e)

        # Register 50 agents from two threads concurrently
        t1 = threading.Thread(target=register_agents, args=(0, 25))
        t2 = threading.Thread(target=register_agents, args=(25, 25))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        assert registry.agent_count == 50

        # Deregister half from two threads
        t3 = threading.Thread(target=deregister_agents, args=(0, 25))
        t4 = threading.Thread(target=deregister_agents, args=(25, 25))
        t3.start()
        t4.start()
        t3.join()
        t4.join()

        assert not errors
        assert registry.agent_count == 0
