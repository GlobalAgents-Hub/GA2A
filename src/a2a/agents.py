"""
Agent management and peer functionality.

Provides the Peer dataclass representing an agent in the GA2A network,
with optional MCP capabilities for structured tool/resource/prompt exchange.

Requirements: 8.1, 8.2, 8.3, 8.4, 9.1, 9.2, 9.3, 9.4
"""
import socket
import threading
import json
import logging
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Type aliases for MCP configuration dicts
ToolConfig = Dict[str, Any]
ResourceConfig = Dict[str, Any]
PromptConfig = Dict[str, Any]


@dataclass
class Peer:
    """
    Represents a peer (agent) in the network.

    When mcp_port is set, the peer gains MCP capabilities: it can host an
    MCP server exposing tools/resources/prompts to other agents and act as
    an MCP client invoking remote agent capabilities.

    Backward compatibility: all MCP features are opt-in. If mcp_port is None,
    the Peer behaves identically to the original implementation.
    """
    name: str
    role: str
    port: int = 5050
    zone: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)
    _event_handlers: Dict[str, List[Callable]] = field(default_factory=dict)

    # MCP configuration — None means MCP is disabled
    mcp_port: Optional[int] = None
    mcp_tools: List[ToolConfig] = field(default_factory=list)
    mcp_resources: List[ResourceConfig] = field(default_factory=list)
    mcp_prompts: List[PromptConfig] = field(default_factory=list)

    def __post_init__(self):
        self._server = None
        self._running = False

        # MCP internal state — initialized lazily in start_mcp()
        self._mcp_loop_runner = None
        self._mcp_server = None
        self._mcp_clients = None

    # --- MCP Properties ---

    @property
    def mcp_enabled(self) -> bool:
        """Return True if MCP is configured (mcp_port is set)."""
        return self.mcp_port is not None

    @property
    def mcp_server(self):
        """Return the MCPServerHost instance, or None if MCP is not started.

        Returns:
            MCPServerHost | None
        """
        return self._mcp_server

    @property
    def mcp_clients(self):
        """Return the MCPClientPool instance, or None if MCP is not started.

        Returns:
            MCPClientPool | None
        """
        return self._mcp_clients

    # --- Existing TCP methods (unchanged) ---

    def start(self) -> None:
        """Start the peer server"""
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("", self.port))
        self._server.listen()
        self._running = True
        
        # Start listener thread
        thread = threading.Thread(target=self._listen)
        thread.daemon = True
        thread.start()

    def stop(self) -> None:
        """Stop the peer server"""
        self._running = False
        if self._server:
            self._server.close()

    def _listen(self) -> None:
        """Listen for incoming connections"""
        while self._running:
            try:
                conn, addr = self._server.accept()
                thread = threading.Thread(target=self._handle_connection, args=(conn, addr))
                thread.daemon = True
                thread.start()
            except:
                if self._running:  # Only log if we're still supposed to be running
                    print(f"Error in peer {self.name} listener")

    def _handle_connection(self, conn: socket.socket, addr: tuple) -> None:
        """Handle an incoming connection"""
        try:
            data = conn.recv(1024)
            if data:
                message = json.loads(data.decode())
                self._trigger_event('message_received', message)
                conn.send(json.dumps({"status": "ok"}).encode())
        except Exception as e:
            print(f"Error handling connection: {e}")
        finally:
            conn.close()

    def send(self, message: Any, target_port: int = 5050) -> bool:
        """
        Send a message to another peer
        
        Args:
            message: The message to send
            target_port: Port of the target peer
            
        Returns:
            bool: True if message was sent successfully
        """
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect(("localhost", target_port))
            client.send(json.dumps(message).encode())
            client.close()
            return True
        except:
            return False

    def on(self, event: str) -> Callable:
        """
        Decorator for registering event handlers
        
        Args:
            event: Name of the event to handle
            
        Returns:
            Callable: Decorator function
        """
        def decorator(func: Callable) -> Callable:
            if event not in self._event_handlers:
                self._event_handlers[event] = []
            self._event_handlers[event].append(func)
            return func
        return decorator

    def _trigger_event(self, event: str, data: Any = None) -> None:
        """
        Trigger an event and call all registered handlers
        
        Args:
            event: Name of the event to trigger
            data: Data to pass to the event handlers
        """
        for handler in self._event_handlers.get(event, []):
            try:
                handler(data)
            except Exception as e:
                print(f"Error in event handler: {e}")

    # --- MCP Lifecycle Methods ---

    def start_mcp(self) -> None:
        """Initialize and start MCP components.

        Creates an AsyncLoopRunner, MCPServerHost, and MCPClientPool.
        Registers any pre-configured tools, resources, and prompts from
        mcp_tools, mcp_resources, mcp_prompts configuration lists.
        Starts the MCP server.

        Raises:
            RuntimeError: If mcp_port is not configured.
            MCPPortConflictError: If the MCP port is already in use.
        """
        if not self.mcp_enabled:
            raise RuntimeError(
                f"Cannot start MCP for peer '{self.name}': mcp_port is not configured."
            )

        # Deferred imports to avoid breaking non-MCP usage
        from a2a.mcp.loop_runner import AsyncLoopRunner
        from a2a.mcp.server import MCPServerHost
        from a2a.mcp.client import MCPClientPool

        # Create and start the async loop runner
        self._mcp_loop_runner = AsyncLoopRunner()
        self._mcp_loop_runner.start()

        # Create MCP server host
        self._mcp_server = MCPServerHost(
            agent_name=self.name,
            port=self.mcp_port,
            loop_runner=self._mcp_loop_runner,
        )

        # Create MCP client pool
        self._mcp_clients = MCPClientPool(
            agent_name=self.name,
            zone_name=self.zone or "",
            loop_runner=self._mcp_loop_runner,
        )

        # Register pre-configured tools
        for tool_cfg in self.mcp_tools:
            handler = tool_cfg.get("handler")
            if handler is not None:
                self._mcp_server.register_tool(
                    name=tool_cfg["name"],
                    handler=handler,
                    description=tool_cfg.get("description", ""),
                    input_schema=tool_cfg.get("input_schema"),
                )

        # Register pre-configured resources
        for res_cfg in self.mcp_resources:
            handler = res_cfg.get("handler")
            if handler is not None:
                self._mcp_server.register_resource(
                    uri=res_cfg["uri"],
                    handler=handler,
                    description=res_cfg.get("description", ""),
                    mime_type=res_cfg.get("mime_type", "text/plain"),
                )

        # Register pre-configured prompts
        for prompt_cfg in self.mcp_prompts:
            handler = prompt_cfg.get("handler")
            if handler is not None:
                self._mcp_server.register_prompt(
                    name=prompt_cfg["name"],
                    handler=handler,
                    description=prompt_cfg.get("description", ""),
                    arguments=prompt_cfg.get("arguments"),
                )

        # Start the server
        self._mcp_server.start()
        logger.info(
            "MCP started for peer '%s' on port %d", self.name, self.mcp_port
        )

    def stop_mcp(self) -> None:
        """Stop MCP components.

        Stops the MCPServerHost and AsyncLoopRunner. Safe to call if
        MCP was never started.
        """
        if self._mcp_server is not None:
            self._mcp_server.stop()
            self._mcp_server = None

        if self._mcp_loop_runner is not None:
            self._mcp_loop_runner.stop()
            self._mcp_loop_runner = None

        self._mcp_clients = None
        logger.info("MCP stopped for peer '%s'", self.name)

    # --- MCP Convenience Methods ---

    def register_tool(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        input_schema: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register a tool on the MCP server.

        Convenience method that delegates to MCPServerHost.register_tool().

        Args:
            name: Unique tool name.
            handler: Callable invoked when tool is called. May be sync or async.
            description: Human-readable tool description.
            input_schema: JSON Schema for the tool's input parameters.

        Raises:
            RuntimeError: If MCP server is not started.
        """
        if self._mcp_server is None:
            raise RuntimeError(
                f"MCP server not started for peer '{self.name}'. Call start_mcp() first."
            )
        self._mcp_server.register_tool(
            name=name,
            handler=handler,
            description=description,
            input_schema=input_schema,
        )

    def deregister_tool(self, name: str) -> None:
        """Deregister a tool from the MCP server.

        Convenience method that delegates to MCPServerHost.deregister_tool().

        Args:
            name: Name of the tool to remove.

        Raises:
            RuntimeError: If MCP server is not started.
        """
        if self._mcp_server is None:
            raise RuntimeError(
                f"MCP server not started for peer '{self.name}'. Call start_mcp() first."
            )
        self._mcp_server.deregister_tool(name)

    def invoke_remote_tool(
        self,
        agent_name: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Invoke a tool on a remote agent's MCP server.

        Synchronous wrapper that uses the loop runner to execute the
        async MCPClientPool.call_tool() method.

        Args:
            agent_name: Name of the remote agent hosting the tool.
            tool_name: Name of the tool to invoke.
            arguments: Input arguments for the tool.

        Returns:
            ToolResult from the remote tool execution.

        Raises:
            RuntimeError: If MCP is not started.
            MCPConnectionError: If the remote server is unreachable.
            MCPToolError: If the remote server returns an error.
        """
        if self._mcp_clients is None or self._mcp_loop_runner is None:
            raise RuntimeError(
                f"MCP not started for peer '{self.name}'. Call start_mcp() first."
            )
        return self._mcp_loop_runner.run_coroutine(
            self._mcp_clients.call_tool(
                agent_name=agent_name,
                tool_name=tool_name,
                arguments=arguments or {},
            )
        )

    def read_remote_resource(self, agent_name: str, uri: str) -> Any:
        """Read a resource from a remote agent's MCP server.

        Synchronous wrapper that uses the loop runner to execute the
        async MCPClientPool.read_resource() method.

        Args:
            agent_name: Name of the remote agent hosting the resource.
            uri: URI of the resource to read.

        Returns:
            ResourceContent from the remote resource.

        Raises:
            RuntimeError: If MCP is not started.
            MCPConnectionError: If the remote server is unreachable.
        """
        if self._mcp_clients is None or self._mcp_loop_runner is None:
            raise RuntimeError(
                f"MCP not started for peer '{self.name}'. Call start_mcp() first."
            )
        return self._mcp_loop_runner.run_coroutine(
            self._mcp_clients.read_resource(
                agent_name=agent_name,
                uri=uri,
            )
        )


class AgentBuilder:
    """
    Builder pattern for creating agents with specific configurations.

    Supports both basic agent configuration (name, role, port, capabilities)
    and optional MCP configuration (mcp_port, tools, resources, prompts).

    Requirements: 9.4
    """
    def __init__(self):
        self._name = None
        self._role = None
        self._port = 5050
        self._capabilities = []
        self._mcp_port: Optional[int] = None
        self._mcp_tools: List[Dict[str, Any]] = []
        self._mcp_resources: List[Dict[str, Any]] = []
        self._mcp_prompts: List[Dict[str, Any]] = []

    def with_name(self, name: str) -> 'AgentBuilder':
        """Set the agent's name"""
        self._name = name
        return self

    def with_role(self, role: str) -> 'AgentBuilder':
        """Set the agent's role"""
        self._role = role
        return self

    def with_port(self, port: int) -> 'AgentBuilder':
        """Set the agent's port"""
        self._port = port
        return self

    def with_capabilities(self, capabilities: List[str]) -> 'AgentBuilder':
        """Set the agent's capabilities"""
        self._capabilities = capabilities
        return self

    def with_mcp_port(self, port: int) -> 'AgentBuilder':
        """Set the agent's MCP server port.

        Setting an MCP port enables MCP capabilities on the built agent.

        Args:
            port: Port number for the MCP server to listen on.
        """
        self._mcp_port = port
        return self

    def with_mcp_tools(self, tools: List[Dict[str, Any]]) -> 'AgentBuilder':
        """Set the agent's MCP tools configuration.

        Each tool dict should contain 'name', 'handler', and optionally
        'description' and 'input_schema' keys.

        Args:
            tools: List of tool configuration dictionaries.
        """
        self._mcp_tools = tools
        return self

    def with_mcp_resources(self, resources: List[Dict[str, Any]]) -> 'AgentBuilder':
        """Set the agent's MCP resources configuration.

        Each resource dict should contain 'uri', 'handler', and optionally
        'description' and 'mime_type' keys.

        Args:
            resources: List of resource configuration dictionaries.
        """
        self._mcp_resources = resources
        return self

    def with_mcp_prompts(self, prompts: List[Dict[str, Any]]) -> 'AgentBuilder':
        """Set the agent's MCP prompts configuration.

        Each prompt dict should contain 'name', 'handler', and optionally
        'description' and 'arguments' keys.

        Args:
            prompts: List of prompt configuration dictionaries.
        """
        self._mcp_prompts = prompts
        return self

    def build(self) -> Peer:
        """Build and return the configured agent.

        Raises:
            ValueError: If name or role is not set.
        """
        if not self._name or not self._role:
            raise ValueError("Agent requires at least a name and role")

        return Peer(
            name=self._name,
            role=self._role,
            port=self._port,
            capabilities=self._capabilities,
            mcp_port=self._mcp_port,
            mcp_tools=self._mcp_tools,
            mcp_resources=self._mcp_resources,
            mcp_prompts=self._mcp_prompts,
        )
