# Design Document: MCP Agent Communication

## Overview

This design introduces the Model Context Protocol (MCP) as a structured capability exchange layer on top of the existing GA2A peer-to-peer network. Agents in a zone can expose tools, resources, and prompts via MCP servers and consume them via MCP clients — all while maintaining backward compatibility with the existing TCP socket communication.

The design leverages the official [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/) (`mcp` package, v2) which provides `MCPServer` for exposing capabilities and `Client` for consuming them over Streamable HTTP transport. Since the MCP SDK is asyncio-based while the existing GA2A codebase is threaded, the integration uses a dedicated asyncio event loop running in a background thread, bridging the two concurrency models.

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| MCP SDK | Official `mcp` package (v2) | Standard-compliant, maintained, handles JSON-RPC 2.0, schema generation, transport |
| Integration Pattern | Composition (mixin-style) | Peer gains an optional `MCPServerHost` and `MCPClientPool`; non-MCP agents are unchanged |
| Zone_Registry | In-memory per-zone, event-driven | Matches existing architecture; no external dependencies; zones are already in-memory |
| Transport | Streamable HTTP | Current MCP standard; enables network-accessible servers; deprecates SSE |
| Transport_Bridge | Event-driven | Hooks into existing EventHandler; reacts to discovery events rather than polling |
| Auth | HMAC-based zone tokens | Simple, no external infra; token = HMAC(zone_secret, agent_name + zone_name) |
| Async bridging | Background asyncio loop in thread | Preserves existing threading model; MCP operations run in their own loop |

## Architecture

### High-Level Architecture

```mermaid
graph TB
    subgraph "Agent (Peer)"
        direction TB
        TCP[TCP Socket Server<br/>Port 5050]
        MCP_S[MCP Server Host<br/>Streamable HTTP]
        MCP_C[MCP Client Pool]
        EH[Event Handler]
    end

    subgraph "Zone Layer"
        ZR[Zone Registry]
        ZM[Zone Manager]
    end

    subgraph "Discovery Layer"
        ND[Network Discovery<br/>UDP Broadcast]
        TB_[Transport Bridge]
    end

    TCP <--> |Legacy messages| OtherAgent1[Other Agent<br/>TCP only]
    MCP_S <--> |JSON-RPC 2.0| OtherAgent2[Other Agent<br/>MCP Client]
    MCP_C <--> |JSON-RPC 2.0| OtherAgent3[Other Agent<br/>MCP Server]

    ND --> TB_
    TB_ --> ZR
    ZR --> EH
    EH --> MCP_C

    MCP_S --> ZR
    ZM --> ZR
```

### Module Architecture

```mermaid
graph LR
    subgraph "src/a2a/ (existing)"
        core[core.py]
        agents[agents.py]
        zones[zones.py]
        events[events.py]
        discovery[discovery.py]
    end

    subgraph "src/a2a/mcp/ (new)"
        server[server.py<br/>MCPServerHost]
        client[client.py<br/>MCPClientPool]
        registry[registry.py<br/>ZoneRegistry]
        bridge[bridge.py<br/>TransportBridge]
        capabilities[capabilities.py<br/>CapabilityCard, schemas]
        auth[auth.py<br/>Token generation/validation]
        loop_runner[loop_runner.py<br/>AsyncLoopRunner]
    end

    agents --> server
    agents --> client
    zones --> registry
    discovery --> bridge
    events --> bridge
    events --> registry
    server --> capabilities
    client --> capabilities
    registry --> capabilities
    server --> auth
    client --> auth
    bridge --> registry
```

## Components and Interfaces

### 1. AsyncLoopRunner (`src/a2a/mcp/loop_runner.py`)

Bridges the existing threaded architecture with the asyncio-based MCP SDK.

```python
class AsyncLoopRunner:
    """Runs a dedicated asyncio event loop in a background thread."""

    def __init__(self) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def run_coroutine(self, coro: Coroutine[Any, Any, T]) -> T: ...
    def schedule(self, coro: Coroutine[Any, Any, T]) -> asyncio.Future[T]: ...

    @property
    def loop(self) -> asyncio.AbstractEventLoop: ...
```

### 2. MCPServerHost (`src/a2a/mcp/server.py`)

Wraps the official `mcp.server.MCPServer` and manages its lifecycle within an Agent.

```python
class MCPServerHost:
    """Hosts an MCP server for an agent, exposing tools/resources/prompts."""

    def __init__(
        self,
        agent_name: str,
        port: int,
        loop_runner: AsyncLoopRunner,
        auth_validator: AuthValidator | None = None,
    ) -> None: ...

    def register_tool(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> None: ...

    def deregister_tool(self, name: str) -> None: ...

    def register_resource(
        self,
        uri: str,
        handler: Callable,
        description: str = "",
        mime_type: str = "text/plain",
    ) -> None: ...

    def deregister_resource(self, uri: str) -> None: ...

    def register_prompt(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        arguments: list[dict[str, Any]] | None = None,
    ) -> None: ...

    def deregister_prompt(self, name: str) -> None: ...

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def get_capability_card(self) -> CapabilityCard: ...

    @property
    def endpoint(self) -> str: ...

    @property
    def is_running(self) -> bool: ...
```

### 3. MCPClientPool (`src/a2a/mcp/client.py`)

Manages connections to multiple remote MCP servers within a zone.

```python
class MCPClientPool:
    """Pool of MCP client connections to remote agents' MCP servers."""

    def __init__(
        self,
        agent_name: str,
        zone_name: str,
        loop_runner: AsyncLoopRunner,
        auth_provider: AuthProvider | None = None,
    ) -> None: ...

    async def connect(self, endpoint: str, capability_card: CapabilityCard) -> None: ...
    async def disconnect(self, agent_name: str) -> None: ...

    async def call_tool(
        self,
        agent_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult: ...

    async def list_tools(self, agent_name: str) -> list[ToolDescriptor]: ...

    async def read_resource(self, agent_name: str, uri: str) -> ResourceContent: ...
    async def list_resources(self, agent_name: str) -> list[ResourceDescriptor]: ...

    async def get_prompt(
        self,
        agent_name: str,
        prompt_name: str,
        arguments: dict[str, str],
    ) -> PromptResult: ...

    def get_available_agents(self) -> list[str]: ...
    def is_agent_available(self, agent_name: str) -> bool: ...
```

### 4. ZoneRegistry (`src/a2a/mcp/registry.py`)

Per-zone registry of active MCP capability cards. Scopes discovery to zone boundaries.

```python
class ZoneRegistry:
    """Maintains a registry of MCP capability cards within a zone."""

    def __init__(self, zone_name: str, event_handler: EventHandler) -> None: ...

    def register(self, card: CapabilityCard) -> None: ...
    def deregister(self, agent_name: str) -> None: ...
    def update(self, card: CapabilityCard) -> None: ...

    def get_card(self, agent_name: str) -> CapabilityCard | None: ...
    def get_all_cards(self) -> list[CapabilityCard]: ...
    def find_by_tool(self, tool_name: str) -> list[CapabilityCard]: ...
    def find_by_resource(self, uri_pattern: str) -> list[CapabilityCard]: ...

    @property
    def zone_name(self) -> str: ...
    @property
    def agent_count(self) -> int: ...
```

### 5. TransportBridge (`src/a2a/mcp/bridge.py`)

Translates P2P discovery events into MCP registrations/deregistrations.

```python
class TransportBridge:
    """Bridges NetworkDiscovery events to ZoneRegistry operations."""

    def __init__(
        self,
        discovery: NetworkDiscovery,
        registries: dict[str, ZoneRegistry],
        event_handler: EventHandler,
        loop_runner: AsyncLoopRunner,
        health_check_interval: float = 30.0,
    ) -> None: ...

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def on_peer_discovered(self, peer_info: dict[str, Any]) -> None: ...
    def on_peer_lost(self, peer_name: str) -> None: ...

    async def _query_capability_card(self, peer_endpoint: str) -> CapabilityCard | None: ...
    async def _health_check_loop(self) -> None: ...
    async def _validate_server(self, endpoint: str) -> bool: ...
```

### 6. Auth Module (`src/a2a/mcp/auth.py`)

Zone-scoped authentication for MCP connections.

```python
@dataclass
class AuthToken:
    """Authentication token for MCP connections."""
    agent_name: str
    zone_name: str
    issued_at: float
    expires_at: float
    signature: str


class AuthProvider:
    """Generates authentication tokens for outgoing MCP requests."""

    def __init__(self, agent_name: str, zone_secrets: dict[str, str]) -> None: ...
    def generate_token(self, zone_name: str) -> str: ...


class AuthValidator:
    """Validates authentication tokens on incoming MCP requests."""

    def __init__(self, zone_name: str, zone_secret: str) -> None: ...
    def validate_token(self, token_str: str) -> AuthToken | None: ...
    def is_zone_member(self, token: AuthToken) -> bool: ...
```

### 7. Enhanced Peer (`src/a2a/agents.py` — modified)

The existing `Peer` class gains optional MCP capabilities through composition.

```python
@dataclass
class Peer:
    # ... existing fields ...
    mcp_port: int | None = None  # None = MCP disabled
    mcp_tools: list[ToolConfig] = field(default_factory=list)
    mcp_resources: list[ResourceConfig] = field(default_factory=list)
    mcp_prompts: list[PromptConfig] = field(default_factory=list)

    # New properties
    @property
    def mcp_enabled(self) -> bool: ...

    @property
    def mcp_server(self) -> MCPServerHost | None: ...

    @property
    def mcp_clients(self) -> MCPClientPool | None: ...

    # New methods
    def start_mcp(self) -> None: ...
    def stop_mcp(self) -> None: ...
    def register_tool(self, name: str, handler: Callable, ...) -> None: ...
    def deregister_tool(self, name: str) -> None: ...
    def invoke_remote_tool(self, agent: str, tool: str, args: dict) -> Any: ...
    def read_remote_resource(self, agent: str, uri: str) -> Any: ...
```

### Sequence Diagrams

#### Agent Join + Capability Advertisement

```mermaid
sequenceDiagram
    participant Agent as Agent (Peer)
    participant MCP_S as MCPServerHost
    participant ZR as ZoneRegistry
    participant EH as EventHandler
    participant Others as Other Agents

    Agent->>Agent: start_mcp()
    Agent->>MCP_S: start() on configured port
    MCP_S-->>Agent: Server running

    Agent->>ZR: register(capability_card)
    ZR->>EH: emit("capability_added", card)
    EH->>Others: notify capability_added
    ZR-->>Agent: List of existing cards

    Agent->>Agent: Connect MCP clients to existing peers
```

#### Tool Invocation Between Agents

```mermaid
sequenceDiagram
    participant A as Agent A (Client)
    participant Pool as MCPClientPool
    participant Net as Network (HTTP)
    participant B_MCP as Agent B (MCPServer)
    participant B_Tool as Tool Handler

    A->>Pool: call_tool("agent_b", "search", {query: "..."})
    Pool->>Pool: Validate input against schema
    Pool->>Net: JSON-RPC 2.0 request + auth token
    Net->>B_MCP: POST /mcp (tools/call)
    B_MCP->>B_MCP: Validate auth token
    B_MCP->>B_Tool: Execute handler
    B_Tool-->>B_MCP: Result
    B_MCP-->>Net: JSON-RPC 2.0 response
    Net-->>Pool: CallToolResult
    Pool-->>A: ToolResult
```

#### Discovery Bridge Flow

```mermaid
sequenceDiagram
    participant ND as NetworkDiscovery
    participant TB as TransportBridge
    participant Peer as New Peer
    participant ZR as ZoneRegistry
    participant EH as EventHandler

    ND->>TB: on_peer_discovered(peer_info)
    TB->>Peer: GET /mcp (capabilities query)
    Peer-->>TB: CapabilityCard
    TB->>ZR: register(card)
    ZR->>EH: emit("capability_added", card)

    Note over TB: Periodic health check
    TB->>Peer: HEAD /mcp (health check)
    Peer--xTB: Connection refused
    TB->>ZR: deregister(peer_name)
    ZR->>EH: emit("capability_removed", peer_name)
    EH->>EH: emit("peer_unreachable", peer_info)
```

## Data Models

### CapabilityCard

```python
@dataclass
class ToolDescriptor:
    """Describes a single tool exposed by an MCP server."""
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema

@dataclass
class ResourceDescriptor:
    """Describes a single resource exposed by an MCP server."""
    uri: str
    name: str
    description: str
    mime_type: str

@dataclass
class PromptDescriptor:
    """Describes a single prompt exposed by an MCP server."""
    name: str
    description: str
    arguments: list[dict[str, Any]]  # [{name, description, required}]

@dataclass
class CapabilityCard:
    """Structured advertisement of an agent's MCP capabilities."""
    agent_name: str
    agent_role: str
    endpoint: str              # e.g., "http://192.168.1.5:5100/mcp"
    transport_type: str        # "streamable-http" | "stdio"
    zone_name: str
    tools: list[ToolDescriptor]
    resources: list[ResourceDescriptor]
    prompts: list[PromptDescriptor]
    published_at: str          # ISO 8601 timestamp
    version: int               # Incremented on each update
```

**JSON representation:**

```json
{
  "agent_name": "research-agent",
  "agent_role": "researcher",
  "endpoint": "http://192.168.1.5:5100/mcp",
  "transport_type": "streamable-http",
  "zone_name": "science-zone",
  "tools": [
    {
      "name": "search_papers",
      "description": "Search academic papers by query",
      "input_schema": {
        "type": "object",
        "properties": {
          "query": {"type": "string"},
          "limit": {"type": "integer", "default": 10}
        },
        "required": ["query"]
      }
    }
  ],
  "resources": [
    {
      "uri": "papers://recent",
      "name": "Recent Papers",
      "description": "List of recently indexed papers",
      "mime_type": "application/json"
    }
  ],
  "prompts": [
    {
      "name": "summarize",
      "description": "Summarize a paper given its DOI",
      "arguments": [
        {"name": "doi", "description": "Paper DOI", "required": true}
      ]
    }
  ],
  "published_at": "2024-12-01T10:30:00Z",
  "version": 1
}
```

### ZoneRegistry Internal Structure

```python
@dataclass
class ZoneRegistryState:
    """Internal state of a ZoneRegistry."""
    zone_name: str
    cards: dict[str, CapabilityCard]   # agent_name -> card
    tool_index: dict[str, list[str]]   # tool_name -> [agent_names]
    resource_index: dict[str, list[str]]  # uri_pattern -> [agent_names]
```

### AuthToken Structure

```python
@dataclass
class AuthToken:
    agent_name: str
    zone_name: str
    issued_at: float     # Unix timestamp
    expires_at: float    # Unix timestamp (issued_at + TTL)
    signature: str       # HMAC-SHA256(zone_secret, f"{agent_name}:{zone_name}:{issued_at}:{expires_at}")
```

**Serialized as base64-encoded JSON for transport in HTTP headers:**

```
Authorization: Bearer eyJhZ2VudF9uYW1lIjogInJlc2VhcmNoLWFnZW50IiwgLi4ufQ==.HMAC_SIGNATURE
```

### ToolResult / ResourceContent

```python
@dataclass
class ToolResult:
    """Result from a remote tool invocation."""
    content: list[dict[str, Any]]   # MCP content blocks
    structured_content: Any | None  # Parsed structured output
    is_error: bool

@dataclass
class ResourceContent:
    """Content read from a remote resource."""
    uri: str
    mime_type: str
    text: str | None = None
    blob: bytes | None = None
```

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Capability Card Round-Trip Serialization

*For any* valid CapabilityCard, serializing it to JSON and deserializing the result back SHALL produce an equivalent CapabilityCard.

**Validates: Requirements 3.3**

### Property 2: Zone-Scoped Isolation

*For any* two distinct zones and any agent registered in zone A, querying zone B's registry SHALL NOT return that agent's CapabilityCard.

**Validates: Requirements 4.4**

### Property 3: Registration Consistency

*For any* agent that publishes a CapabilityCard to a ZoneRegistry, querying that registry for the agent's name SHALL return the most recently published card.

**Validates: Requirements 3.1, 3.2**

### Property 4: Deregistration Completeness

*For any* agent that removes its CapabilityCard from a ZoneRegistry, querying that registry for the agent's name SHALL return None, and the agent SHALL NOT appear in any tool or resource index lookups.

**Validates: Requirements 3.4, 9.2**

### Property 5: Tool Schema Validation Rejection

*For any* tool invocation where the input arguments do NOT conform to the tool's declared input_schema, the MCP_Client SHALL reject the request before sending it to the remote server.

**Validates: Requirements 6.4**

### Property 6: Auth Token Zone Binding

*For any* valid auth token generated for zone A, attempting to validate that token against zone B SHALL fail (return None or raise an authentication error).

**Validates: Requirements 7.2**

### Property 7: Auth Token Expiry

*For any* auth token with an expiration time in the past, validation SHALL fail regardless of the token's other fields being correct.

**Validates: Requirements 7.4**

### Property 8: Dynamic Registration Visibility

*For any* tool registered at runtime on an MCP_Server, querying the server's tool list immediately after registration SHALL include that tool with its correct name and schema.

**Validates: Requirements 9.1**

### Property 9: Dynamic Deregistration Invisibility

*For any* tool deregistered at runtime from an MCP_Server, querying the server's tool list immediately after deregistration SHALL NOT include that tool.

**Validates: Requirements 9.2**

### Property 10: Transport Bridge Peer-to-Card Mapping

*For any* peer discovered by NetworkDiscovery that has MCP capabilities, the TransportBridge SHALL produce a ZoneRegistry entry whose agent_name matches the peer's name and whose endpoint is reachable.

**Validates: Requirements 5.1, 5.3**

### Property 11: Backward Compatibility — Non-MCP Peers Unaffected

*For any* Peer configured without mcp_port (MCP disabled), calling start() and send() SHALL behave identically to the current implementation with no MCP-related side effects.

**Validates: Requirements 8.1, 8.2**

## Error Handling

### Error Categories and Strategies

| Error Category | Example | Strategy |
|---------------|---------|----------|
| Port binding failure | MCP port already in use | Raise `MCPPortConflictError` with port number; agent does not start MCP |
| Connection refused | Remote MCP server down | Mark agent as unavailable in pool; emit `peer_unreachable` event |
| Auth failure | Invalid/expired token | Return JSON-RPC error -32001; client logs warning |
| Tool execution error | Handler raises exception | Return `is_error=True` result with error message in content |
| Schema validation error | Invalid tool arguments | Reject locally before network call; raise `SchemaValidationError` |
| Timeout | Slow tool execution | Configurable timeout (default 30s); return timeout error |
| Serialization error | Invalid capability card | Raise `CapabilityCardError` on publish; reject malformed cards on receive |

### Error Hierarchy

```python
class MCPError(Exception):
    """Base error for all MCP-related failures."""

class MCPPortConflictError(MCPError):
    """MCP server cannot bind to configured port."""

class MCPConnectionError(MCPError):
    """Cannot connect to remote MCP server."""

class MCPAuthError(MCPError):
    """Authentication failed for MCP connection."""

class MCPToolError(MCPError):
    """Error invoking a remote tool."""

class MCPSchemaValidationError(MCPError):
    """Input arguments do not match tool schema."""

class MCPCapabilityCardError(MCPError):
    """Invalid or malformed capability card."""
```

### Retry and Recovery

- **Connection failures**: Exponential backoff (1s, 2s, 4s, max 30s) with 3 retries before marking unavailable
- **Health check recovery**: TransportBridge health checks run every 30s; recovered agents are re-registered
- **Token refresh**: Tokens have 1-hour TTL; clients refresh 5 minutes before expiry
- **Graceful degradation**: If MCP layer fails entirely, TCP socket communication continues unaffected

## Testing Strategy

### Property-Based Testing

This feature is well-suited for property-based testing. The core logic involves data transformations (serialization), registry operations (set-like behavior), and authentication (cryptographic properties) — all pure functions or near-pure with clear input/output contracts.

**Library**: [Hypothesis](https://hypothesis.readthedocs.io/) (Python's standard PBT library)

**Configuration**:
- Minimum 100 iterations per property test
- Each property test tagged with design property reference
- Tag format: `# Feature: mcp-agent-communication, Property {N}: {title}`

**Property tests will cover:**
- CapabilityCard serialization round-trip (Property 1)
- Zone isolation invariant (Property 2)
- Registry CRUD consistency (Properties 3, 4)
- Schema validation correctness (Property 5)
- Auth token zone binding and expiry (Properties 6, 7)
- Dynamic registration/deregistration visibility (Properties 8, 9)
- Backward compatibility (Property 11)

### Unit Testing

Unit tests focus on specific examples and integration points:

- MCP server startup and shutdown lifecycle
- Tool invocation success/failure paths with concrete payloads
- Event emission on registry changes (specific event payloads)
- TransportBridge reaction to specific discovery events
- Fallback to TCP for non-MCP peers (integration between Peer and MCPClientPool)
- Error cases: port conflict, invalid JSON-RPC responses, malformed tokens

### Integration Testing

- End-to-end: Agent A registers tool → Agent B discovers → Agent B invokes tool → receives result
- Zone join/leave lifecycle with MCP advertisement
- TransportBridge with mocked NetworkDiscovery
- Mixed zone: MCP agents + TCP-only agents coexisting
- Auth flow: token generation → transmission → validation → access granted/denied

### Test Organization

```
tests/
  test_mcp/
    test_server.py          # MCPServerHost unit + property tests
    test_client.py          # MCPClientPool unit tests
    test_registry.py        # ZoneRegistry property + unit tests
    test_bridge.py          # TransportBridge unit tests
    test_auth.py            # Auth property + unit tests
    test_capabilities.py    # CapabilityCard serialization property tests
    test_integration.py     # End-to-end integration tests
    test_backward_compat.py # Backward compatibility tests
```

### Dependencies

New production dependencies:
- `mcp>=2.0.0` — Official MCP Python SDK
- `uvicorn>=0.30.0` — ASGI server for Streamable HTTP transport
- `httpx>=0.27.0` — HTTP client for MCP client connections (required by mcp SDK)

New test dependencies:
- `hypothesis>=6.100.0` — Property-based testing
- `pytest-asyncio>=0.23.0` — Async test support
