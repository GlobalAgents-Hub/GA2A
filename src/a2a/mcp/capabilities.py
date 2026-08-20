"""
Data models for MCP capability exchange.

Defines the structured types used across the MCP communication layer:
- ToolDescriptor, ResourceDescriptor, PromptDescriptor: describe individual capabilities
- CapabilityCard: structured advertisement of an agent's MCP capabilities
- ToolResult, ResourceContent: response types from remote invocations
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any


# Valid transport types for MCP connections
VALID_TRANSPORT_TYPES = ("streamable-http", "stdio")


@dataclass
class ToolDescriptor:
    """Describes a single tool exposed by an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolDescriptor:
        """Deserialize from a dictionary."""
        return cls(
            name=data["name"],
            description=data["description"],
            input_schema=data.get("input_schema", {}),
        )


@dataclass
class ResourceDescriptor:
    """Describes a single resource exposed by an MCP server."""

    uri: str
    name: str
    description: str
    mime_type: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mime_type": self.mime_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceDescriptor:
        """Deserialize from a dictionary."""
        return cls(
            uri=data["uri"],
            name=data["name"],
            description=data["description"],
            mime_type=data["mime_type"],
        )


@dataclass
class PromptDescriptor:
    """Describes a single prompt exposed by an MCP server."""

    name: str
    description: str
    arguments: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "arguments": self.arguments,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromptDescriptor:
        """Deserialize from a dictionary."""
        return cls(
            name=data["name"],
            description=data["description"],
            arguments=data.get("arguments", []),
        )


@dataclass
class CapabilityCard:
    """Structured advertisement of an agent's MCP capabilities.

    Published to ZoneRegistry when an agent joins a zone with MCP enabled.
    Used by other agents to discover available tools, resources, and prompts.
    """

    agent_name: str
    agent_role: str
    endpoint: str
    transport_type: str
    zone_name: str
    tools: list[ToolDescriptor] = field(default_factory=list)
    resources: list[ResourceDescriptor] = field(default_factory=list)
    prompts: list[PromptDescriptor] = field(default_factory=list)
    published_at: str = ""  # ISO 8601 timestamp
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary, including nested descriptors."""
        return {
            "agent_name": self.agent_name,
            "agent_role": self.agent_role,
            "endpoint": self.endpoint,
            "transport_type": self.transport_type,
            "zone_name": self.zone_name,
            "tools": [t.to_dict() for t in self.tools],
            "resources": [r.to_dict() for r in self.resources],
            "prompts": [p.to_dict() for p in self.prompts],
            "published_at": self.published_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityCard:
        """Deserialize from a dictionary, including nested descriptors.

        Validates that transport_type is one of the supported values.

        Raises:
            ValueError: If transport_type is not "streamable-http" or "stdio".
        """
        transport_type = data["transport_type"]
        if transport_type not in VALID_TRANSPORT_TYPES:
            raise ValueError(
                f"Invalid transport_type '{transport_type}'. "
                f"Must be one of: {', '.join(VALID_TRANSPORT_TYPES)}"
            )

        return cls(
            agent_name=data["agent_name"],
            agent_role=data["agent_role"],
            endpoint=data["endpoint"],
            transport_type=transport_type,
            zone_name=data["zone_name"],
            tools=[ToolDescriptor.from_dict(t) for t in data.get("tools", [])],
            resources=[ResourceDescriptor.from_dict(r) for r in data.get("resources", [])],
            prompts=[PromptDescriptor.from_dict(p) for p in data.get("prompts", [])],
            published_at=data.get("published_at", ""),
            version=data.get("version", 1),
        )


@dataclass
class ToolResult:
    """Result from a remote tool invocation."""

    content: list[dict[str, Any]] = field(default_factory=list)
    structured_content: Any = None
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "content": self.content,
            "structured_content": self.structured_content,
            "is_error": self.is_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolResult:
        """Deserialize from a dictionary."""
        return cls(
            content=data.get("content", []),
            structured_content=data.get("structured_content"),
            is_error=data.get("is_error", False),
        )


@dataclass
class ResourceContent:
    """Content read from a remote resource."""

    uri: str
    mime_type: str
    text: str | None = None
    blob: bytes | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary.

        The blob field is encoded as base64 for JSON transport.
        """
        result: dict[str, Any] = {
            "uri": self.uri,
            "mime_type": self.mime_type,
        }
        if self.text is not None:
            result["text"] = self.text
        if self.blob is not None:
            result["blob"] = base64.b64encode(self.blob).decode("ascii")
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceContent:
        """Deserialize from a dictionary.

        The blob field is decoded from base64.
        """
        blob = None
        if "blob" in data and data["blob"] is not None:
            blob = base64.b64decode(data["blob"])

        return cls(
            uri=data["uri"],
            mime_type=data["mime_type"],
            text=data.get("text"),
            blob=blob,
        )
