# Implementation Plan: MCP Agent Communication

## Overview

This plan implements the Model Context Protocol (MCP) as a structured capability exchange layer on top of the existing GA2A peer-to-peer network. The implementation follows a dependency-driven order: infrastructure first, then auth, registry, server, client, bridge, and finally peer integration with wiring. All code is Python 3.10+ using the official `mcp` SDK (v2) with asyncio bridging to the existing threaded architecture.

## Tasks

- [ ] 1. Project setup and infrastructure
  - [ ] 1.1 Add new dependencies to pyproject.toml
    - Add `mcp>=2.0.0`, `uvicorn>=0.30.0`, `httpx>=0.27.0` to `[project.dependencies]`
    - Add `hypothesis>=6.100.0`, `pytest-asyncio>=0.23.0` to `[project.optional-dependencies].dev`
    - _Requirements: 1.4, 2.2_

  - [ ] 1.2 Create `src/a2a/mcp/` package with `__init__.py`
    - Create directory structure `src/a2a/mcp/`
    - Create `__init__.py` that exports all public classes
    - _Requirements: 8.1, 8.2_

  - [ ] 1.3 Implement `AsyncLoopRunner` in `src/a2a/mcp/loop_runner.py`
    - Implement background thread running a dedicated asyncio event loop
    - Implement `start()`, `stop()`, `run_coroutine()`, `schedule()` methods
    - Implement `loop` property
    - Ensure thread-safe coroutine submission from synchronous code
    - _Requirements: 1.1, 2.1_

  - [ ] 1.4 Implement data models in `src/a2a/mcp/capabilities.py`
    - Implement `ToolDescriptor`, `ResourceDescriptor`, `PromptDescriptor` dataclasses
    - Implement `CapabilityCard` dataclass with all fields (agent_name, agent_role, endpoint, transport_type, zone_name, tools, resources, prompts, published_at, version)
    - Implement `ToolResult` and `ResourceContent` dataclasses
    - Implement JSON serialization/deserialization methods (`to_dict()`, `from_dict()`)
    - _Requirements: 3.3, 6.2, 10.3_

  - [ ]* 1.5 Write property test for CapabilityCard serialization round-trip
    - **Property 1: Capability Card Round-Trip Serialization**
    - **Validates: Requirements 3.3**

- [ ] 2. Authentication module
  - [ ] 2.1 Implement auth module in `src/a2a/mcp/auth.py`
    - Implement `AuthToken` dataclass with fields: agent_name, zone_name, issued_at, expires_at, signature
    - Implement `AuthProvider` class with `generate_token(zone_name)` method using HMAC-SHA256
    - Implement `AuthValidator` class with `validate_token(token_str)` and `is_zone_member(token)` methods
    - Implement base64 serialization/deserialization for token transport
    - Token TTL: 1 hour; signature: HMAC-SHA256(zone_secret, f"{agent_name}:{zone_name}:{issued_at}:{expires_at}")
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [ ]* 2.2 Write property test for auth token zone binding
    - **Property 6: Auth Token Zone Binding**
    - **Validates: Requirements 7.2**

  - [ ]* 2.3 Write property test for auth token expiry
    - **Property 7: Auth Token Expiry**
    - **Validates: Requirements 7.4**

- [ ] 3. Zone Registry
  - [ ] 3.1 Implement `ZoneRegistry` in `src/a2a/mcp/registry.py`
    - Implement `__init__(zone_name, event_handler)` storing zone name and reference to EventHandler
    - Implement `register(card)` that stores card and emits "capability_added" event
    - Implement `deregister(agent_name)` that removes card and emits "capability_removed" event
    - Implement `update(card)` that replaces existing card and emits "capability_added" event
    - Implement `get_card(agent_name)`, `get_all_cards()` query methods
    - Implement `find_by_tool(tool_name)` and `find_by_resource(uri_pattern)` index lookups
    - Maintain internal tool_index and resource_index dicts for fast lookups
    - _Requirements: 3.1, 3.2, 3.4, 4.1, 4.2, 4.3, 4.4, 11.1, 11.2_

  - [ ]* 3.2 Write property test for zone-scoped isolation
    - **Property 2: Zone-Scoped Isolation**
    - **Validates: Requirements 4.4**

  - [ ]* 3.3 Write property test for registration consistency
    - **Property 3: Registration Consistency**
    - **Validates: Requirements 3.1, 3.2**

  - [ ]* 3.4 Write property test for deregistration completeness
    - **Property 4: Deregistration Completeness**
    - **Validates: Requirements 3.4, 9.2**

- [ ] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. MCP Server Host
  - [ ] 5.1 Implement `MCPServerHost` in `src/a2a/mcp/server.py`
    - Implement `__init__(agent_name, port, loop_runner, auth_validator)` storing configuration
    - Implement `register_tool(name, handler, description, input_schema)` and `deregister_tool(name)`
    - Implement `register_resource(uri, handler, description, mime_type)` and `deregister_resource(uri)`
    - Implement `register_prompt(name, handler, description, arguments)` and `deregister_prompt(name)`
    - Implement `start()` that creates an `mcp.server.MCPServer` instance, configures Streamable HTTP transport via uvicorn, and runs it in the AsyncLoopRunner
    - Implement `stop()` that shuts down the HTTP server and MCP server
    - Implement `get_capability_card()` that builds a CapabilityCard from current registrations
    - Implement `endpoint` and `is_running` properties
    - Raise `MCPPortConflictError` if port binding fails
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 9.1, 9.2, 9.4_

  - [ ] 5.2 Implement error hierarchy in `src/a2a/mcp/server.py` (or separate `errors.py`)
    - Define `MCPError`, `MCPPortConflictError`, `MCPConnectionError`, `MCPAuthError`, `MCPToolError`, `MCPSchemaValidationError`, `MCPCapabilityCardError`
    - _Requirements: 1.3, 6.3, 7.4_

  - [ ]* 5.3 Write property test for dynamic registration visibility
    - **Property 8: Dynamic Registration Visibility**
    - **Validates: Requirements 9.1**

  - [ ]* 5.4 Write property test for dynamic deregistration invisibility
    - **Property 9: Dynamic Deregistration Invisibility**
    - **Validates: Requirements 9.2**

- [ ] 6. MCP Client Pool
  - [ ] 6.1 Implement `MCPClientPool` in `src/a2a/mcp/client.py`
    - Implement `__init__(agent_name, zone_name, loop_runner, auth_provider)` storing configuration
    - Implement `connect(endpoint, capability_card)` that establishes an MCP client session using the `mcp` SDK's `Client` over Streamable HTTP
    - Implement `disconnect(agent_name)` that closes the client session
    - Implement `call_tool(agent_name, tool_name, arguments)` that validates input against schema, sends JSON-RPC 2.0 request, returns `ToolResult`
    - Implement `list_tools(agent_name)` and `list_resources(agent_name)` query methods
    - Implement `read_resource(agent_name, uri)` that returns `ResourceContent`
    - Implement `get_prompt(agent_name, prompt_name, arguments)` returning `PromptResult`
    - Implement `get_available_agents()` and `is_agent_available(agent_name)` status methods
    - Validate input arguments against tool schema before network call; raise `MCPSchemaValidationError` on failure
    - Handle connection failures with exponential backoff (1s, 2s, 4s, max 30s, 3 retries)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 6.1, 6.2, 6.3, 6.4, 10.2, 10.3, 10.4_

  - [ ]* 6.2 Write property test for tool schema validation rejection
    - **Property 5: Tool Schema Validation Rejection**
    - **Validates: Requirements 6.4**

- [ ] 7. Transport Bridge
  - [ ] 7.1 Implement `TransportBridge` in `src/a2a/mcp/bridge.py`
    - Implement `__init__(discovery, registries, event_handler, loop_runner, health_check_interval)`
    - Implement `start()` that hooks into NetworkDiscovery events and starts health check loop
    - Implement `stop()` that removes hooks and stops health check loop
    - Implement `on_peer_discovered(peer_info)` that queries peer for CapabilityCard and registers in ZoneRegistry
    - Implement `on_peer_lost(peer_name)` that deregisters from all ZoneRegistries
    - Implement `_query_capability_card(peer_endpoint)` async method using httpx
    - Implement `_health_check_loop()` that periodically validates registered MCP servers (default 30s interval)
    - Implement `_validate_server(endpoint)` that performs HEAD request to check reachability
    - Emit "peer_unreachable" event when health check fails
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 11.3_

  - [ ]* 7.2 Write property test for transport bridge peer-to-card mapping
    - **Property 10: Transport Bridge Peer-to-Card Mapping**
    - **Validates: Requirements 5.1, 5.3**

- [ ] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Peer integration and wiring
  - [ ] 9.1 Extend `Peer` class in `src/a2a/agents.py` with MCP capabilities
    - Add optional fields: `mcp_port`, `mcp_tools`, `mcp_resources`, `mcp_prompts` to the `Peer` dataclass
    - Add `mcp_enabled` property returning `True` if `mcp_port` is not None
    - Add `mcp_server` and `mcp_clients` properties
    - Implement `start_mcp()` that initializes AsyncLoopRunner, MCPServerHost, MCPClientPool and starts the server
    - Implement `stop_mcp()` that stops server, disconnects clients, stops loop runner
    - Implement `register_tool(name, handler, ...)` and `deregister_tool(name)` convenience methods
    - Implement `invoke_remote_tool(agent, tool, args)` and `read_remote_resource(agent, uri)` convenience methods
    - Ensure `start()` and `stop()` remain unchanged for non-MCP peers
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 9.1, 9.2, 9.3, 9.4_

  - [ ] 9.2 Update `AgentBuilder` to support MCP configuration
    - Add `with_mcp_port(port)`, `with_mcp_tools(tools)`, `with_mcp_resources(resources)`, `with_mcp_prompts(prompts)` methods
    - Pass MCP config to `Peer` constructor in `build()`
    - _Requirements: 9.4_

  - [ ] 9.3 Update `src/a2a/__init__.py` to export new MCP classes
    - Export `MCPServerHost`, `MCPClientPool`, `ZoneRegistry`, `TransportBridge`, `CapabilityCard`, `AsyncLoopRunner`, `AuthProvider`, `AuthValidator`
    - Add MCP error classes to exports
    - _Requirements: 8.1_

  - [ ]* 9.4 Write property test for backward compatibility
    - **Property 11: Backward Compatibility — Non-MCP Peers Unaffected**
    - **Validates: Requirements 8.1, 8.2**

- [ ] 10. Integration tests
  - [ ]* 10.1 Write integration test for end-to-end tool invocation
    - Agent A registers a tool → Agent B discovers via ZoneRegistry → Agent B invokes tool → receives result
    - Test full lifecycle: start MCP servers, register capabilities, discover, invoke, verify result
    - _Requirements: 6.1, 6.2, 4.1, 4.2_

  - [ ]* 10.2 Write integration test for zone join/leave lifecycle
    - Agent joins zone → publishes CapabilityCard → other agents notified → agent leaves → card removed → others notified
    - Verify events "capability_added" and "capability_removed" are emitted
    - _Requirements: 3.1, 3.4, 4.2, 4.3, 11.1, 11.2_

  - [ ]* 10.3 Write integration test for mixed zone (MCP + TCP-only agents)
    - MCP-capable agent communicates with MCP peer via MCP, falls back to TCP for non-MCP peer
    - Verify both communication paths work within the same zone
    - _Requirements: 8.3, 8.4_

- [ ] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document using Hypothesis
- Unit tests validate specific examples and edge cases
- The implementation order ensures each module's dependencies are already implemented before it
- The official `mcp` Python SDK (v2) handles JSON-RPC 2.0 protocol details; our code wraps it for GA2A integration
- All async MCP operations run in a background asyncio loop (AsyncLoopRunner) to preserve the existing threaded architecture

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.4"] },
    { "id": 2, "tasks": ["1.5", "2.1"] },
    { "id": 3, "tasks": ["2.2", "2.3", "3.1", "5.2"] },
    { "id": 4, "tasks": ["3.2", "3.3", "3.4", "5.1"] },
    { "id": 5, "tasks": ["5.3", "5.4", "6.1"] },
    { "id": 6, "tasks": ["6.2", "7.1"] },
    { "id": 7, "tasks": ["7.2", "9.1"] },
    { "id": 8, "tasks": ["9.2", "9.3", "9.4"] },
    { "id": 9, "tasks": ["10.1", "10.2", "10.3"] }
  ]
}
```
