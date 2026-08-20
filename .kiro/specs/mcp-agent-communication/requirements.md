# Requirements Document

## Introduction

This document specifies requirements for adding Model Context Protocol (MCP) as a communication layer between agents within the GA2A Protocol. Currently, GA2A agents communicate via direct TCP sockets and organize into thematic "zones." This feature evolves the protocol so that agents in zones can expose and consume capabilities (tools, resources, prompts) through MCP — a standardized JSON-RPC 2.0 protocol for AI system interoperability. The existing P2P discovery and zone topology remain intact; MCP adds a structured capability exchange layer on top.

## Glossary

- **Agent**: An independent peer node in the GA2A network that can produce or consume information within zones
- **Zone**: A thematic space identified by a name and metadata where agents connect to exchange information on a specific topic
- **MCP (Model Context Protocol)**: An open standard using JSON-RPC 2.0 that enables AI systems to integrate with external data sources and tools through a client-server architecture
- **MCP_Server**: A component within an Agent that exposes capabilities (tools, resources, prompts) to other agents via the MCP protocol
- **MCP_Client**: A component within an Agent that discovers and invokes capabilities exposed by other agents' MCP_Servers
- **Tool**: A function or action that an Agent exposes via its MCP_Server for other agents to execute
- **Resource**: Contextual data or content that an Agent exposes via its MCP_Server for other agents to read
- **Prompt**: A templated message or workflow that an Agent exposes via its MCP_Server for other agents to use
- **Capability_Card**: A structured advertisement describing an Agent's MCP capabilities (tools, resources, prompts) and connection metadata
- **Zone_Registry**: A component within the Zone Layer that maintains a registry of active MCP_Servers and their Capability_Cards for agents in that zone
- **Transport_Bridge**: The mechanism that translates GA2A P2P discovery events into MCP_Server registrations and deregistrations within a zone
- **JSON-RPC 2.0**: The message format used by MCP for request/response communication between clients and servers
- **SDK**: The GA2A Python package (src/a2a/) providing classes and utilities for building agents and interacting with the protocol

## Requirements

### Requirement 1: MCP Server Initialization

**User Story:** As an agent developer, I want my agent to expose an MCP server, so that other agents in the same zone can discover and invoke my agent's capabilities.

#### Acceptance Criteria

1. WHEN an Agent is configured with MCP capabilities, THE MCP_Server SHALL initialize and listen for incoming JSON-RPC 2.0 connections on a configurable port
2. WHEN the MCP_Server starts, THE MCP_Server SHALL register all configured tools, resources, and prompts as available capabilities
3. IF the MCP_Server fails to bind to the configured port, THEN THE MCP_Server SHALL raise a descriptive error indicating the port conflict
4. THE MCP_Server SHALL support both stdio and HTTP with SSE as transport mechanisms for JSON-RPC 2.0 communication

### Requirement 2: MCP Client Initialization

**User Story:** As an agent developer, I want my agent to act as an MCP client, so that it can discover and consume capabilities exposed by other agents in the same zone.

#### Acceptance Criteria

1. WHEN an Agent joins a Zone, THE MCP_Client SHALL initialize and prepare to connect to MCP_Servers of other agents in that Zone
2. THE MCP_Client SHALL send JSON-RPC 2.0 requests to invoke tools on remote MCP_Servers
3. THE MCP_Client SHALL retrieve resource listings and read resources from remote MCP_Servers
4. IF an MCP_Server becomes unreachable, THEN THE MCP_Client SHALL mark that server as unavailable and cease sending requests to it until rediscovery

### Requirement 3: Capability Advertisement via Capability Cards

**User Story:** As an agent developer, I want my agent to advertise its capabilities when joining a zone, so that other agents can discover what tools and resources are available.

#### Acceptance Criteria

1. WHEN an Agent joins a Zone, THE Agent SHALL publish a Capability_Card to the Zone_Registry containing the agent's name, MCP_Server endpoint, and list of available tools, resources, and prompts
2. WHEN an Agent's capabilities change at runtime, THE Agent SHALL publish an updated Capability_Card to the Zone_Registry
3. THE Capability_Card SHALL include the agent's name, role, MCP transport type, connection endpoint, and a list of capability descriptors (name, description, input schema for each tool/resource/prompt)
4. WHEN an Agent leaves a Zone, THE Agent SHALL remove its Capability_Card from the Zone_Registry

### Requirement 4: Zone-Scoped MCP Discovery

**User Story:** As an agent developer, I want to discover all MCP-capable agents and their capabilities within my zone, so that my agent can decide which remote tools and resources to use.

#### Acceptance Criteria

1. WHEN an Agent joins a Zone, THE Zone_Registry SHALL provide the Agent with a list of all active Capability_Cards in that Zone
2. WHEN a new Agent publishes a Capability_Card to a Zone, THE Zone_Registry SHALL notify all other agents in that Zone about the new capabilities
3. WHEN an Agent removes its Capability_Card from a Zone, THE Zone_Registry SHALL notify all remaining agents in that Zone about the removal
4. THE Zone_Registry SHALL scope capability discovery to the Zone boundary so that agents only see capabilities from agents in the same Zone

### Requirement 5: Transport Bridge (P2P Discovery to MCP Registration)

**User Story:** As a protocol maintainer, I want the existing P2P discovery mechanism to feed into MCP registration, so that agents discovered via UDP broadcast are automatically available as MCP peers within their zones.

#### Acceptance Criteria

1. WHEN the NetworkDiscovery component discovers a new peer, THE Transport_Bridge SHALL query that peer for its Capability_Card and register it in the appropriate Zone_Registry
2. WHEN the NetworkDiscovery component detects a peer has left the network, THE Transport_Bridge SHALL deregister that peer's Capability_Card from all Zone_Registries where it was present
3. THE Transport_Bridge SHALL translate between GA2A peer identity (name, role, port) and MCP_Server connection metadata (endpoint, transport type)
4. WHILE the Transport_Bridge is active, THE Transport_Bridge SHALL periodically validate that registered MCP_Servers are still reachable and remove stale entries

### Requirement 6: Tool Invocation Between Agents

**User Story:** As an agent developer, I want my agent to invoke tools exposed by other agents in the same zone, so that agents can collaborate by leveraging each other's capabilities.

#### Acceptance Criteria

1. WHEN an Agent invokes a tool on a remote MCP_Server, THE MCP_Client SHALL send a JSON-RPC 2.0 request with the tool name and input parameters
2. WHEN the remote MCP_Server processes the tool invocation, THE MCP_Server SHALL return a JSON-RPC 2.0 response containing the tool result
3. IF the tool invocation fails on the remote MCP_Server, THEN THE MCP_Server SHALL return a JSON-RPC 2.0 error response with an error code and descriptive message
4. WHEN an Agent invokes a tool, THE MCP_Client SHALL validate the input parameters against the tool's schema from the Capability_Card before sending the request

### Requirement 7: Security and Authentication for MCP Connections

**User Story:** As a protocol maintainer, I want MCP connections between agents to be authenticated and secure, so that only authorized agents within a zone can invoke each other's capabilities.

#### Acceptance Criteria

1. WHEN an MCP_Client connects to an MCP_Server, THE MCP_Client SHALL present an authentication token that identifies the requesting Agent and its Zone membership
2. WHEN an MCP_Server receives a connection request, THE MCP_Server SHALL validate the authentication token and reject connections from agents not in the same Zone
3. THE MCP_Server SHALL support token-based authentication where tokens are issued upon Zone membership confirmation
4. IF an authentication token is invalid or expired, THEN THE MCP_Server SHALL reject the request with a JSON-RPC 2.0 error response indicating an authentication failure

### Requirement 8: Backward Compatibility with TCP Socket Communication

**User Story:** As an existing GA2A user, I want the new MCP layer to be optional, so that agents using direct TCP socket communication continue to work without modification.

#### Acceptance Criteria

1. THE SDK SHALL maintain the existing Peer.send() and Peer._handle_connection() TCP socket interface without modification
2. WHEN an Agent is configured without MCP capabilities, THE Agent SHALL operate using only the existing TCP socket communication
3. THE SDK SHALL allow agents to use both TCP socket communication and MCP communication simultaneously within the same Zone
4. WHEN an MCP-capable Agent communicates with a non-MCP Agent in the same Zone, THE MCP-capable Agent SHALL fall back to TCP socket communication for that peer

### Requirement 9: Dynamic Capability Registration and Deregistration

**User Story:** As an agent developer, I want to register and deregister capabilities at runtime, so that my agent can adapt its exposed functionality based on context or workload.

#### Acceptance Criteria

1. WHEN an Agent registers a new tool at runtime, THE MCP_Server SHALL make that tool immediately available for invocation by MCP_Clients
2. WHEN an Agent deregisters a tool at runtime, THE MCP_Server SHALL immediately stop accepting invocations for that tool and return a "method not found" error for subsequent requests
3. WHEN capabilities change, THE Agent SHALL publish an updated Capability_Card to the Zone_Registry so that other agents receive the updated capability list
4. THE SDK SHALL provide an API for programmatic registration and deregistration of tools, resources, and prompts on a running MCP_Server

### Requirement 10: Resource Sharing Between Agents

**User Story:** As an agent developer, I want my agent to expose and consume resources (data, context) through MCP, so that agents in a zone can share contextual information beyond tool invocations.

#### Acceptance Criteria

1. WHEN an Agent exposes a resource, THE MCP_Server SHALL make that resource available for listing and reading by MCP_Clients in the same Zone
2. WHEN an MCP_Client requests a resource listing, THE MCP_Server SHALL return a list of available resources with their URIs and descriptions
3. WHEN an MCP_Client reads a resource by URI, THE MCP_Server SHALL return the resource content in the format specified by the resource's MIME type
4. IF a requested resource does not exist, THEN THE MCP_Server SHALL return a JSON-RPC 2.0 error response with a "resource not found" error code

### Requirement 11: Event Notification for MCP Lifecycle

**User Story:** As an agent developer, I want to receive events when MCP-related changes occur (new capabilities available, agent joined, agent left), so that my agent can react to dynamic zone composition.

#### Acceptance Criteria

1. WHEN a new Capability_Card is published to the Zone_Registry, THE EventHandler SHALL emit a "capability_added" event containing the new Capability_Card data
2. WHEN a Capability_Card is removed from the Zone_Registry, THE EventHandler SHALL emit a "capability_removed" event containing the removed agent's identity
3. WHEN an MCP_Server becomes unreachable, THE EventHandler SHALL emit a "peer_unreachable" event containing the affected agent's identity and zone
4. THE SDK SHALL allow agents to register handlers for MCP lifecycle events using the existing EventHandler.on() pattern
