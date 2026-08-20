"""Unit tests for MCP capabilities data models."""

import base64

import pytest

from a2a.mcp.capabilities import (
    CapabilityCard,
    PromptDescriptor,
    ResourceContent,
    ResourceDescriptor,
    ToolDescriptor,
    ToolResult,
    VALID_TRANSPORT_TYPES,
)


class TestToolDescriptor:
    """Tests for ToolDescriptor serialization."""

    def test_to_dict(self):
        tool = ToolDescriptor(
            name="search",
            description="Search documents",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )
        d = tool.to_dict()
        assert d == {
            "name": "search",
            "description": "Search documents",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        }

    def test_from_dict(self):
        data = {
            "name": "search",
            "description": "Search documents",
            "input_schema": {"type": "object"},
        }
        tool = ToolDescriptor.from_dict(data)
        assert tool.name == "search"
        assert tool.description == "Search documents"
        assert tool.input_schema == {"type": "object"}

    def test_round_trip(self):
        tool = ToolDescriptor(
            name="calc",
            description="Calculator",
            input_schema={"type": "object", "properties": {"x": {"type": "number"}}},
        )
        assert ToolDescriptor.from_dict(tool.to_dict()) == tool

    def test_from_dict_missing_schema_defaults_empty(self):
        data = {"name": "noop", "description": "No-op tool"}
        tool = ToolDescriptor.from_dict(data)
        assert tool.input_schema == {}


class TestResourceDescriptor:
    """Tests for ResourceDescriptor serialization."""

    def test_to_dict(self):
        res = ResourceDescriptor(
            uri="papers://recent",
            name="Recent Papers",
            description="Recently indexed papers",
            mime_type="application/json",
        )
        d = res.to_dict()
        assert d == {
            "uri": "papers://recent",
            "name": "Recent Papers",
            "description": "Recently indexed papers",
            "mime_type": "application/json",
        }

    def test_from_dict(self):
        data = {
            "uri": "data://logs",
            "name": "Logs",
            "description": "System logs",
            "mime_type": "text/plain",
        }
        res = ResourceDescriptor.from_dict(data)
        assert res.uri == "data://logs"
        assert res.mime_type == "text/plain"

    def test_round_trip(self):
        res = ResourceDescriptor(
            uri="file://config.yaml",
            name="Config",
            description="Configuration file",
            mime_type="application/yaml",
        )
        assert ResourceDescriptor.from_dict(res.to_dict()) == res


class TestPromptDescriptor:
    """Tests for PromptDescriptor serialization."""

    def test_to_dict(self):
        prompt = PromptDescriptor(
            name="summarize",
            description="Summarize a document",
            arguments=[{"name": "doc_id", "description": "Document ID", "required": True}],
        )
        d = prompt.to_dict()
        assert d["name"] == "summarize"
        assert len(d["arguments"]) == 1
        assert d["arguments"][0]["name"] == "doc_id"

    def test_from_dict(self):
        data = {
            "name": "translate",
            "description": "Translate text",
            "arguments": [
                {"name": "text", "description": "Input text", "required": True},
                {"name": "lang", "description": "Target language", "required": True},
            ],
        }
        prompt = PromptDescriptor.from_dict(data)
        assert prompt.name == "translate"
        assert len(prompt.arguments) == 2

    def test_round_trip(self):
        prompt = PromptDescriptor(
            name="greet",
            description="Greeting prompt",
            arguments=[{"name": "name", "description": "Name", "required": False}],
        )
        assert PromptDescriptor.from_dict(prompt.to_dict()) == prompt

    def test_from_dict_missing_arguments_defaults_empty(self):
        data = {"name": "hello", "description": "Hello prompt"}
        prompt = PromptDescriptor.from_dict(data)
        assert prompt.arguments == []


class TestCapabilityCard:
    """Tests for CapabilityCard serialization and validation."""

    def _make_card(self) -> CapabilityCard:
        return CapabilityCard(
            agent_name="research-agent",
            agent_role="researcher",
            endpoint="http://192.168.1.5:5100/mcp",
            transport_type="streamable-http",
            zone_name="science-zone",
            tools=[
                ToolDescriptor(
                    name="search_papers",
                    description="Search academic papers",
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                )
            ],
            resources=[
                ResourceDescriptor(
                    uri="papers://recent",
                    name="Recent Papers",
                    description="Recently indexed papers",
                    mime_type="application/json",
                )
            ],
            prompts=[
                PromptDescriptor(
                    name="summarize",
                    description="Summarize a paper",
                    arguments=[{"name": "doi", "description": "Paper DOI", "required": True}],
                )
            ],
            published_at="2024-12-01T10:30:00Z",
            version=1,
        )

    def test_to_dict_structure(self):
        card = self._make_card()
        d = card.to_dict()
        assert d["agent_name"] == "research-agent"
        assert d["transport_type"] == "streamable-http"
        assert len(d["tools"]) == 1
        assert d["tools"][0]["name"] == "search_papers"
        assert len(d["resources"]) == 1
        assert d["resources"][0]["uri"] == "papers://recent"
        assert len(d["prompts"]) == 1
        assert d["prompts"][0]["name"] == "summarize"

    def test_from_dict_valid(self):
        card = self._make_card()
        d = card.to_dict()
        restored = CapabilityCard.from_dict(d)
        assert restored.agent_name == "research-agent"
        assert restored.tools[0].name == "search_papers"
        assert restored.resources[0].uri == "papers://recent"
        assert restored.prompts[0].name == "summarize"

    def test_round_trip(self):
        card = self._make_card()
        assert CapabilityCard.from_dict(card.to_dict()) == card

    def test_from_dict_invalid_transport_type(self):
        data = {
            "agent_name": "test",
            "agent_role": "tester",
            "endpoint": "http://localhost:5000/mcp",
            "transport_type": "websocket",
            "zone_name": "test-zone",
        }
        with pytest.raises(ValueError, match="Invalid transport_type"):
            CapabilityCard.from_dict(data)

    def test_from_dict_stdio_transport(self):
        data = {
            "agent_name": "local-agent",
            "agent_role": "helper",
            "endpoint": "stdio://local",
            "transport_type": "stdio",
            "zone_name": "dev-zone",
        }
        card = CapabilityCard.from_dict(data)
        assert card.transport_type == "stdio"

    def test_from_dict_empty_capabilities(self):
        data = {
            "agent_name": "bare",
            "agent_role": "idle",
            "endpoint": "http://localhost:9000/mcp",
            "transport_type": "streamable-http",
            "zone_name": "idle-zone",
        }
        card = CapabilityCard.from_dict(data)
        assert card.tools == []
        assert card.resources == []
        assert card.prompts == []
        assert card.version == 1
        assert card.published_at == ""

    def test_round_trip_empty_card(self):
        card = CapabilityCard(
            agent_name="minimal",
            agent_role="none",
            endpoint="http://localhost:8080/mcp",
            transport_type="streamable-http",
            zone_name="z",
        )
        assert CapabilityCard.from_dict(card.to_dict()) == card


class TestToolResult:
    """Tests for ToolResult serialization."""

    def test_to_dict_success(self):
        result = ToolResult(
            content=[{"type": "text", "text": "hello world"}],
            structured_content={"answer": 42},
            is_error=False,
        )
        d = result.to_dict()
        assert d["content"] == [{"type": "text", "text": "hello world"}]
        assert d["structured_content"] == {"answer": 42}
        assert d["is_error"] is False

    def test_to_dict_error(self):
        result = ToolResult(
            content=[{"type": "text", "text": "something went wrong"}],
            is_error=True,
        )
        d = result.to_dict()
        assert d["is_error"] is True
        assert d["structured_content"] is None

    def test_round_trip(self):
        result = ToolResult(
            content=[{"type": "text", "text": "data"}],
            structured_content={"key": "value"},
            is_error=False,
        )
        assert ToolResult.from_dict(result.to_dict()) == result

    def test_from_dict_defaults(self):
        data = {}
        result = ToolResult.from_dict(data)
        assert result.content == []
        assert result.structured_content is None
        assert result.is_error is False


class TestResourceContent:
    """Tests for ResourceContent serialization with base64 blob handling."""

    def test_to_dict_text_only(self):
        content = ResourceContent(
            uri="data://config",
            mime_type="text/plain",
            text="key=value",
        )
        d = content.to_dict()
        assert d == {
            "uri": "data://config",
            "mime_type": "text/plain",
            "text": "key=value",
        }
        assert "blob" not in d

    def test_to_dict_blob_only(self):
        raw_bytes = b"\x00\x01\x02\xff"
        content = ResourceContent(
            uri="data://binary",
            mime_type="application/octet-stream",
            blob=raw_bytes,
        )
        d = content.to_dict()
        assert d["blob"] == base64.b64encode(raw_bytes).decode("ascii")
        assert "text" not in d

    def test_to_dict_both_text_and_blob(self):
        content = ResourceContent(
            uri="data://mixed",
            mime_type="multipart/mixed",
            text="description",
            blob=b"binary data",
        )
        d = content.to_dict()
        assert d["text"] == "description"
        assert d["blob"] == base64.b64encode(b"binary data").decode("ascii")

    def test_from_dict_text_only(self):
        data = {"uri": "x://y", "mime_type": "text/plain", "text": "hello"}
        content = ResourceContent.from_dict(data)
        assert content.text == "hello"
        assert content.blob is None

    def test_from_dict_blob_decoded(self):
        encoded = base64.b64encode(b"secret").decode("ascii")
        data = {"uri": "x://y", "mime_type": "application/octet-stream", "blob": encoded}
        content = ResourceContent.from_dict(data)
        assert content.blob == b"secret"
        assert content.text is None

    def test_round_trip_text(self):
        content = ResourceContent(uri="a://b", mime_type="text/plain", text="data")
        assert ResourceContent.from_dict(content.to_dict()) == content

    def test_round_trip_blob(self):
        content = ResourceContent(
            uri="a://b",
            mime_type="application/octet-stream",
            blob=b"\x00\xff\x80",
        )
        assert ResourceContent.from_dict(content.to_dict()) == content

    def test_round_trip_both(self):
        content = ResourceContent(
            uri="a://b",
            mime_type="multipart/mixed",
            text="info",
            blob=b"raw",
        )
        assert ResourceContent.from_dict(content.to_dict()) == content
