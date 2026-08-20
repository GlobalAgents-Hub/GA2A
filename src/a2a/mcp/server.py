"""
MCP Server Host for GA2A agents.

Hosts a JSON-RPC 2.0 server over Streamable HTTP (via uvicorn) that exposes
tools, resources, and prompts to other agents. Implements the MCP protocol
subset needed for inter-agent capability exchange.

Requirements: 1.1, 1.2, 1.3, 1.4, 9.1, 9.2, 9.4
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import socket
import threading
from datetime import datetime, timezone
from typing import Any, Callable

import uvicorn

from a2a.mcp.auth import AuthValidator
from a2a.mcp.capabilities import (
    CapabilityCard,
    PromptDescriptor,
    ResourceDescriptor,
    ToolDescriptor,
)
from a2a.mcp.errors import MCPPortConflictError, MCPToolError
from a2a.mcp.loop_runner import AsyncLoopRunner

logger = logging.getLogger(__name__)


class MCPServerHost:
    """Hosts an MCP server for an agent, exposing tools/resources/prompts.

    The server implements a subset of the MCP protocol using a custom ASGI
    application that handles JSON-RPC 2.0 requests over HTTP. It runs within
    the AsyncLoopRunner's background event loop via uvicorn.

    Thread-safe: registration/deregistration operations are protected by a lock.
    """

    def __init__(
        self,
        agent_name: str,
        port: int,
        loop_runner: AsyncLoopRunner,
        auth_validator: AuthValidator | None = None,
    ) -> None:
        """Initialize the MCP server host.

        Args:
            agent_name: Name of the agent hosting this server.
            port: TCP port to listen on.
            loop_runner: AsyncLoopRunner instance for running the server.
            auth_validator: Optional validator for incoming auth tokens.
        """
        self._agent_name = agent_name
        self._port = port
        self._loop_runner = loop_runner
        self._auth_validator = auth_validator
        self._lock = threading.Lock()

        # Registered capabilities
        self._tools: dict[str, _ToolEntry] = {}
        self._resources: dict[str, _ResourceEntry] = {}
        self._prompts: dict[str, _PromptEntry] = {}

        # Server state
        self._running = False
        self._server: uvicorn.Server | None = None

    # --- Registration methods ---

    def register_tool(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        """Register a tool that can be invoked by remote MCP clients.

        Args:
            name: Unique tool name.
            handler: Callable to invoke when tool is called. May be sync or async.
            description: Human-readable description of what the tool does.
            input_schema: JSON Schema for the tool's input parameters.
        """
        with self._lock:
            self._tools[name] = _ToolEntry(
                name=name,
                handler=handler,
                description=description,
                input_schema=input_schema or {},
            )

    def deregister_tool(self, name: str) -> None:
        """Remove a registered tool.

        After deregistration, subsequent calls to this tool will return
        a "method not found" error.

        Args:
            name: Name of the tool to remove.
        """
        with self._lock:
            self._tools.pop(name, None)

    def register_resource(
        self,
        uri: str,
        handler: Callable,
        description: str = "",
        mime_type: str = "text/plain",
    ) -> None:
        """Register a resource that can be read by remote MCP clients.

        Args:
            uri: Unique resource URI.
            handler: Callable that returns resource content when invoked.
            description: Human-readable description of the resource.
            mime_type: MIME type of the resource content.
        """
        with self._lock:
            self._resources[uri] = _ResourceEntry(
                uri=uri,
                handler=handler,
                description=description,
                mime_type=mime_type,
            )

    def deregister_resource(self, uri: str) -> None:
        """Remove a registered resource.

        Args:
            uri: URI of the resource to remove.
        """
        with self._lock:
            self._resources.pop(uri, None)

    def register_prompt(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        arguments: list[dict[str, Any]] | None = None,
    ) -> None:
        """Register a prompt that can be retrieved by remote MCP clients.

        Args:
            name: Unique prompt name.
            handler: Callable that returns prompt content when invoked.
            description: Human-readable description of the prompt.
            arguments: List of argument descriptors for the prompt.
        """
        with self._lock:
            self._prompts[name] = _PromptEntry(
                name=name,
                handler=handler,
                description=description,
                arguments=arguments or [],
            )

    def deregister_prompt(self, name: str) -> None:
        """Remove a registered prompt.

        Args:
            name: Name of the prompt to remove.
        """
        with self._lock:
            self._prompts.pop(name, None)

    # --- Lifecycle methods ---

    def start(self) -> None:
        """Start the MCP server.

        Creates a uvicorn server running the JSON-RPC 2.0 ASGI app in the
        background event loop. Blocks until the server is confirmed listening.

        Raises:
            MCPPortConflictError: If the configured port is already in use.
            RuntimeError: If the server is already running.
        """
        if self._running:
            return

        # Pre-check port availability to give a clear error
        self._check_port_available()

        # Create the ASGI app and uvicorn config
        app = self._create_asgi_app()
        config = uvicorn.Config(
            app=app,
            host="0.0.0.0",
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)

        # Start server in the async loop
        ready_event = threading.Event()

        async def _run_server() -> None:
            # Override the startup_event to signal readiness
            try:
                await self._server.serve()
            except OSError as e:
                if "address already in use" in str(e).lower() or e.errno == 98:
                    raise MCPPortConflictError(self._port) from e
                raise

        # Schedule server startup and wait for it to begin listening
        future = self._loop_runner.schedule(_run_server())

        # Wait for server to start (poll the started flag)
        for _ in range(50):  # Up to 5 seconds
            if self._server.started:
                break
            import time
            time.sleep(0.1)
        else:
            # Check if there was a port conflict during startup
            if not self._server.started:
                # Try to cancel and check for errors
                try:
                    future.result(timeout=0.5)
                except MCPPortConflictError:
                    raise
                except OSError as e:
                    if "address already in use" in str(e).lower() or e.errno == 98:
                        raise MCPPortConflictError(self._port) from e
                    raise
                except Exception:
                    pass

        self._running = True
        logger.info(
            "MCP server started for agent '%s' on port %d",
            self._agent_name,
            self._port,
        )

    def stop(self) -> None:
        """Stop the MCP server.

        Signals uvicorn to shut down gracefully. Safe to call if not running.
        """
        if not self._running or self._server is None:
            return

        self._server.should_exit = True

        # Give the server a moment to shut down
        import time
        for _ in range(20):  # Up to 2 seconds
            if not self._server.started:
                break
            time.sleep(0.1)

        self._running = False
        self._server = None
        logger.info(
            "MCP server stopped for agent '%s' on port %d",
            self._agent_name,
            self._port,
        )

    def get_capability_card(self) -> CapabilityCard:
        """Build a CapabilityCard from current registrations.

        Returns:
            A CapabilityCard reflecting the current state of registered
            tools, resources, and prompts.
        """
        with self._lock:
            tools = [
                ToolDescriptor(
                    name=entry.name,
                    description=entry.description,
                    input_schema=entry.input_schema,
                )
                for entry in self._tools.values()
            ]
            resources = [
                ResourceDescriptor(
                    uri=entry.uri,
                    name=entry.uri.split("/")[-1] if "/" in entry.uri else entry.uri,
                    description=entry.description,
                    mime_type=entry.mime_type,
                )
                for entry in self._resources.values()
            ]
            prompts = [
                PromptDescriptor(
                    name=entry.name,
                    description=entry.description,
                    arguments=entry.arguments,
                )
                for entry in self._prompts.values()
            ]

        return CapabilityCard(
            agent_name=self._agent_name,
            agent_role="agent",
            endpoint=self.endpoint,
            transport_type="streamable-http",
            zone_name="",  # Set by caller when publishing to registry
            tools=tools,
            resources=resources,
            prompts=prompts,
            published_at=datetime.now(timezone.utc).isoformat(),
            version=1,
        )

    # --- Properties ---

    @property
    def endpoint(self) -> str:
        """The HTTP endpoint for this MCP server."""
        return f"http://localhost:{self._port}/mcp"

    @property
    def is_running(self) -> bool:
        """Whether the server is currently running."""
        return self._running

    # --- Private methods ---

    def _check_port_available(self) -> None:
        """Check if the configured port is available.

        Raises:
            MCPPortConflictError: If the port is already in use.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("0.0.0.0", self._port))
        except OSError:
            raise MCPPortConflictError(self._port)
        finally:
            sock.close()

    def _create_asgi_app(self) -> Callable:
        """Create the ASGI application that handles JSON-RPC 2.0 requests."""
        server_host = self

        async def app(scope: dict, receive: Callable, send: Callable) -> None:
            """ASGI application entry point."""
            if scope["type"] == "lifespan":
                # Handle ASGI lifespan events
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
                return

            if scope["type"] != "http":
                return

            # Only handle POST to /mcp
            path = scope.get("path", "")
            method = scope.get("method", "")

            if method == "POST" and path == "/mcp":
                await server_host._handle_jsonrpc_request(scope, receive, send)
            else:
                # Return 404 for anything else
                await _send_http_response(
                    send,
                    status=404,
                    body=json.dumps({"error": "Not found"}).encode(),
                )

        return app

    async def _handle_jsonrpc_request(
        self,
        scope: dict,
        receive: Callable,
        send: Callable,
    ) -> None:
        """Handle an incoming JSON-RPC 2.0 request."""
        # Validate auth if configured
        if self._auth_validator is not None:
            headers = dict(scope.get("headers", []))
            # ASGI headers are bytes
            auth_header = headers.get(b"authorization", b"").decode("utf-8")
            if auth_header.startswith("Bearer "):
                token_str = auth_header[7:]
                token = self._auth_validator.validate_token(token_str)
                if token is None or not self._auth_validator.is_zone_member(token):
                    error_response = _jsonrpc_error(
                        None, -32000, "Authentication failed"
                    )
                    await _send_http_response(
                        send,
                        status=401,
                        body=json.dumps(error_response).encode(),
                    )
                    return
            else:
                error_response = _jsonrpc_error(
                    None, -32000, "Authorization header required"
                )
                await _send_http_response(
                    send,
                    status=401,
                    body=json.dumps(error_response).encode(),
                )
                return

        # Read request body
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        # Parse JSON-RPC request
        try:
            request = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            error_response = _jsonrpc_error(None, -32700, "Parse error")
            await _send_http_response(
                send,
                status=400,
                body=json.dumps(error_response).encode(),
            )
            return

        # Validate JSON-RPC 2.0 structure
        if not isinstance(request, dict):
            error_response = _jsonrpc_error(None, -32600, "Invalid Request")
            await _send_http_response(
                send,
                status=400,
                body=json.dumps(error_response).encode(),
            )
            return

        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if not method or not isinstance(method, str):
            error_response = _jsonrpc_error(request_id, -32600, "Invalid Request")
            await _send_http_response(
                send,
                status=400,
                body=json.dumps(error_response).encode(),
            )
            return

        # Route to handler based on method
        response = await self._route_method(request_id, method, params)
        await _send_http_response(
            send,
            status=200,
            body=json.dumps(response).encode(),
        )

    async def _route_method(
        self, request_id: Any, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Route a JSON-RPC method to the appropriate handler."""
        if method == "initialize":
            return self._handle_initialize(request_id)
        elif method == "tools/list":
            return self._handle_tools_list(request_id)
        elif method == "tools/call":
            return await self._handle_tools_call(request_id, params)
        elif method == "resources/list":
            return self._handle_resources_list(request_id)
        elif method == "resources/read":
            return await self._handle_resources_read(request_id, params)
        elif method == "prompts/list":
            return self._handle_prompts_list(request_id)
        elif method == "prompts/get":
            return await self._handle_prompts_get(request_id, params)
        else:
            return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")

    def _handle_initialize(self, request_id: Any) -> dict[str, Any]:
        """Handle the initialize method — return server capabilities."""
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": True},
                "resources": {"listChanged": True},
                "prompts": {"listChanged": True},
            },
            "serverInfo": {
                "name": self._agent_name,
                "version": "1.0.0",
            },
        }
        return _jsonrpc_success(request_id, result)

    def _handle_tools_list(self, request_id: Any) -> dict[str, Any]:
        """Handle tools/list — return all registered tools."""
        with self._lock:
            tools = [
                {
                    "name": entry.name,
                    "description": entry.description,
                    "inputSchema": entry.input_schema,
                }
                for entry in self._tools.values()
            ]
        return _jsonrpc_success(request_id, {"tools": tools})

    async def _handle_tools_call(
        self, request_id: Any, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle tools/call — invoke a registered tool handler."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        with self._lock:
            entry = self._tools.get(tool_name)

        if entry is None:
            return _jsonrpc_error(
                request_id, -32601, f"Tool not found: {tool_name}"
            )

        try:
            # Invoke the handler (support both sync and async)
            if inspect.iscoroutinefunction(entry.handler):
                result = await entry.handler(**arguments)
            else:
                result = entry.handler(**arguments)

            # Wrap result in MCP content format
            if isinstance(result, dict):
                content = [{"type": "text", "text": json.dumps(result)}]
            elif isinstance(result, str):
                content = [{"type": "text", "text": result}]
            else:
                content = [{"type": "text", "text": str(result)}]

            return _jsonrpc_success(
                request_id,
                {"content": content, "isError": False},
            )
        except Exception as e:
            return _jsonrpc_error(
                request_id,
                -32000,
                f"Tool execution error: {e}",
            )

    def _handle_resources_list(self, request_id: Any) -> dict[str, Any]:
        """Handle resources/list — return all registered resources."""
        with self._lock:
            resources = [
                {
                    "uri": entry.uri,
                    "name": entry.uri.split("/")[-1] if "/" in entry.uri else entry.uri,
                    "description": entry.description,
                    "mimeType": entry.mime_type,
                }
                for entry in self._resources.values()
            ]
        return _jsonrpc_success(request_id, {"resources": resources})

    async def _handle_resources_read(
        self, request_id: Any, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle resources/read — invoke a resource handler and return content."""
        uri = params.get("uri", "")

        with self._lock:
            entry = self._resources.get(uri)

        if entry is None:
            return _jsonrpc_error(
                request_id, -32002, f"Resource not found: {uri}"
            )

        try:
            if inspect.iscoroutinefunction(entry.handler):
                result = await entry.handler()
            else:
                result = entry.handler()

            content = {
                "uri": entry.uri,
                "mimeType": entry.mime_type,
            }
            if isinstance(result, bytes):
                import base64
                content["blob"] = base64.b64encode(result).decode("ascii")
            else:
                content["text"] = str(result)

            return _jsonrpc_success(request_id, {"contents": [content]})
        except Exception as e:
            return _jsonrpc_error(
                request_id, -32000, f"Resource read error: {e}"
            )

    def _handle_prompts_list(self, request_id: Any) -> dict[str, Any]:
        """Handle prompts/list — return all registered prompts."""
        with self._lock:
            prompts = [
                {
                    "name": entry.name,
                    "description": entry.description,
                    "arguments": entry.arguments,
                }
                for entry in self._prompts.values()
            ]
        return _jsonrpc_success(request_id, {"prompts": prompts})

    async def _handle_prompts_get(
        self, request_id: Any, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle prompts/get — invoke a prompt handler."""
        prompt_name = params.get("name", "")
        arguments = params.get("arguments", {})

        with self._lock:
            entry = self._prompts.get(prompt_name)

        if entry is None:
            return _jsonrpc_error(
                request_id, -32601, f"Prompt not found: {prompt_name}"
            )

        try:
            if inspect.iscoroutinefunction(entry.handler):
                result = await entry.handler(**arguments)
            else:
                result = entry.handler(**arguments)

            # Wrap in MCP prompt message format
            if isinstance(result, list):
                messages = result
            elif isinstance(result, str):
                messages = [{"role": "user", "content": {"type": "text", "text": result}}]
            else:
                messages = [{"role": "user", "content": {"type": "text", "text": str(result)}}]

            return _jsonrpc_success(request_id, {"messages": messages})
        except Exception as e:
            return _jsonrpc_error(
                request_id, -32000, f"Prompt execution error: {e}"
            )


# --- Internal data classes ---


class _ToolEntry:
    """Internal storage for a registered tool."""

    __slots__ = ("name", "handler", "description", "input_schema")

    def __init__(
        self,
        name: str,
        handler: Callable,
        description: str,
        input_schema: dict[str, Any],
    ) -> None:
        self.name = name
        self.handler = handler
        self.description = description
        self.input_schema = input_schema


class _ResourceEntry:
    """Internal storage for a registered resource."""

    __slots__ = ("uri", "handler", "description", "mime_type")

    def __init__(
        self,
        uri: str,
        handler: Callable,
        description: str,
        mime_type: str,
    ) -> None:
        self.uri = uri
        self.handler = handler
        self.description = description
        self.mime_type = mime_type


class _PromptEntry:
    """Internal storage for a registered prompt."""

    __slots__ = ("name", "handler", "description", "arguments")

    def __init__(
        self,
        name: str,
        handler: Callable,
        description: str,
        arguments: list[dict[str, Any]],
    ) -> None:
        self.name = name
        self.handler = handler
        self.description = description
        self.arguments = arguments


# --- JSON-RPC 2.0 helpers ---


def _jsonrpc_success(request_id: Any, result: Any) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 success response."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def _jsonrpc_error(
    request_id: Any, code: int, message: str, data: Any = None
) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 error response."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error,
    }


async def _send_http_response(
    send: Callable, status: int, body: bytes, content_type: str = "application/json"
) -> None:
    """Send an HTTP response via ASGI."""
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", content_type.encode()],
                [b"content-length", str(len(body)).encode()],
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )
