"""
GA2A Zone Server — instância persistente do protocolo.

Roda como daemon no localhost, mantém zonas ativas e expõe um endpoint MCP
onde agentes externos (LLMs, bots, Kiro) podem:
  - Se registrar numa zona
  - Descobrir outros agentes e suas capabilities
  - Invocar tools de agentes na mesma zona
  - Expor suas próprias tools

Uso:
  python3 ga2a_server.py [--port PORT]
"""

import sys
sys.path.insert(0, 'src')

import argparse
import asyncio
import inspect
import json
import logging
import signal
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

import uvicorn

from a2a.mcp.capabilities import (
    CapabilityCard, ToolDescriptor, ResourceDescriptor, PromptDescriptor
)
from a2a.mcp.registry import ZoneRegistry
from a2a.mcp.auth import AuthProvider, AuthValidator
from a2a.mcp.loop_runner import AsyncLoopRunner
from a2a.events import EventHandler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [GA2A] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('ga2a')


class GA2AServer:
    """Servidor GA2A com zonas persistentes e endpoint MCP para agentes externos."""

    def __init__(self, port: int):
        self.port = port
        self.event_handler = EventHandler()
        self.zones: dict[str, ZoneRegistry] = {}
        self.loop_runner = AsyncLoopRunner()
        self._running = False
        self._server = None

        # Agents registrados externamente (via MCP)
        # agent_name -> {endpoint, zone, tools[], registered_at}
        self._external_agents: dict[str, dict] = {}

        # Zone secret para auth
        self._zone_secret = "ga2a-local-dev-secret"

        # Setup eventos
        @self.event_handler.on('capability_added')
        def _on_add(data):
            log.info(f"📣 [{data.get('zone_name','')}] +{data['agent_name']} ({len(data.get('tools',[]))} tools)")

        @self.event_handler.on('capability_removed')
        def _on_rm(data):
            log.info(f"📣 [{data['zone_name']}] -{data['agent_name']} saiu")

        # Criar zonas padrão
        for zone_name in ['General', 'AI-Research', 'DevOps', 'Data-Science']:
            self.zones[zone_name] = ZoneRegistry(zone_name, self.event_handler)

    def start(self):
        """Inicia o servidor."""
        self.loop_runner.start()
        self._running = True

        app = self._create_app()
        config = uvicorn.Config(
            app=app, host="0.0.0.0", port=self.port,
            log_level="warning", access_log=False
        )
        self._server = uvicorn.Server(config)

        # Rodar uvicorn no loop runner
        self.loop_runner.schedule(self._server.serve())

        # Esperar server subir
        for _ in range(50):
            if self._server.started:
                break
            time.sleep(0.1)

        log.info(f"🌐 GA2A Server rodando em http://localhost:{self.port}")
        log.info(f"   Endpoint MCP: http://localhost:{self.port}/mcp")
        log.info(f"   Zonas ativas: {list(self.zones.keys())}")
        log.info(f"   Para conectar um agente, use o endpoint /mcp")
        log.info("")
        log.info("   Métodos disponíveis:")
        log.info("   ├─ zones/list          → listar zonas")
        log.info("   ├─ zones/agents        → listar agentes numa zona")
        log.info("   ├─ zones/join          → entrar numa zona")
        log.info("   ├─ zones/leave         → sair de uma zona")
        log.info("   ├─ zones/create        → criar nova zona")
        log.info("   ├─ agent/register      → registrar agente com tools")
        log.info("   ├─ agent/discover      → descobrir capabilities na zona")
        log.info("   ├─ agent/invoke        → invocar tool de outro agente")
        log.info("   └─ agent/message       → enviar mensagem a outro agente")
        log.info("")

    def stop(self):
        """Para o servidor."""
        self._running = False
        if self._server:
            self._server.should_exit = True
        time.sleep(0.5)
        self.loop_runner.stop()
        log.info("🛑 GA2A Server parado")

    def _create_app(self):
        """Cria o ASGI app."""
        server = self

        async def app(scope, receive, send):
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
                return

            if scope["type"] != "http":
                return

            path = scope.get("path", "")
            method = scope.get("method", "")

            if method == "GET" and path == "/":
                await _send_response(send, 200, json.dumps({
                    "service": "GA2A Protocol Server",
                    "version": "0.2.0",
                    "mcp_endpoint": f"http://localhost:{server.port}/mcp",
                    "zones": list(server.zones.keys()),
                    "agents_online": sum(z.agent_count for z in server.zones.values()),
                    "status": "running"
                }, indent=2).encode())

            elif method == "GET" and path == "/status":
                status = {}
                for zname, zreg in server.zones.items():
                    cards = zreg.get_all_cards()
                    status[zname] = {
                        "agents": zreg.agent_count,
                        "members": [
                            {"name": c.agent_name, "role": c.agent_role,
                             "tools": [t.name for t in c.tools]}
                            for c in cards
                        ]
                    }
                await _send_response(send, 200, json.dumps(status, indent=2).encode())

            elif method == "POST" and path == "/mcp":
                await server._handle_mcp(scope, receive, send)

            else:
                await _send_response(send, 404, b'{"error":"Not found"}')

        return app

    async def _handle_mcp(self, scope, receive, send):
        """Handler JSON-RPC 2.0 para o endpoint MCP do servidor."""
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body", False):
                break

        try:
            request = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            await _send_response(send, 400, json.dumps(
                _error(None, -32700, "Parse error")).encode())
            return

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        # Roteamento
        handlers = {
            "initialize": self._h_initialize,
            "zones/list": self._h_zones_list,
            "zones/agents": self._h_zones_agents,
            "zones/join": self._h_zones_join,
            "zones/leave": self._h_zones_leave,
            "zones/create": self._h_zones_create,
            "agent/register": self._h_agent_register,
            "agent/discover": self._h_agent_discover,
            "agent/invoke": self._h_agent_invoke,
            "agent/message": self._h_agent_message,
        }

        handler = handlers.get(method)
        if handler:
            result = await handler(params)
            resp = _success(req_id, result)
        else:
            resp = _error(req_id, -32601, f"Method not found: {method}")

        await _send_response(send, 200, json.dumps(resp).encode())

    # --- Handlers ---

    async def _h_initialize(self, params):
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "GA2A-Server", "version": "0.2.0"},
            "capabilities": {
                "zones": True,
                "agentRegistry": True,
                "toolInvocation": True,
            },
            "zones": list(self.zones.keys()),
        }

    async def _h_zones_list(self, params):
        result = {}
        for zname, zreg in self.zones.items():
            result[zname] = {"agent_count": zreg.agent_count}
        return {"zones": result}

    async def _h_zones_agents(self, params):
        zone_name = params.get("zone", "General")
        zreg = self.zones.get(zone_name)
        if not zreg:
            return {"error": f"Zone '{zone_name}' not found"}
        cards = zreg.get_all_cards()
        return {
            "zone": zone_name,
            "agents": [
                {
                    "name": c.agent_name,
                    "role": c.agent_role,
                    "endpoint": c.endpoint,
                    "tools": [{"name": t.name, "description": t.description} for t in c.tools],
                    "resources": [{"uri": r.uri, "name": r.name} for r in c.resources],
                }
                for c in cards
            ]
        }

    async def _h_zones_join(self, params):
        zone_name = params.get("zone", "General")
        agent_name = params.get("agent_name", "")
        agent_role = params.get("agent_role", "assistant")
        tools = params.get("tools", [])
        resources = params.get("resources", [])
        endpoint = params.get("endpoint", "")

        if not agent_name:
            return {"error": "agent_name is required"}

        # Criar zona se não existir
        if zone_name not in self.zones:
            self.zones[zone_name] = ZoneRegistry(zone_name, self.event_handler)

        zreg = self.zones[zone_name]

        # Construir card
        tool_descriptors = [
            ToolDescriptor(name=t["name"], description=t.get("description", ""),
                          input_schema=t.get("input_schema", {}))
            for t in tools
        ]
        resource_descriptors = [
            ResourceDescriptor(uri=r["uri"], name=r.get("name", ""),
                             description=r.get("description", ""),
                             mime_type=r.get("mime_type", "text/plain"))
            for r in resources
        ]

        card = CapabilityCard(
            agent_name=agent_name,
            agent_role=agent_role,
            endpoint=endpoint,
            transport_type="streamable-http",
            zone_name=zone_name,
            tools=tool_descriptors,
            resources=resource_descriptors,
            prompts=[],
            published_at=datetime.now(timezone.utc).isoformat(),
            version=1,
        )

        zreg.register(card)

        # Guardar referência
        self._external_agents[agent_name] = {
            "endpoint": endpoint,
            "zone": zone_name,
            "tools": [t["name"] for t in tools],
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }

        return {
            "status": "joined",
            "zone": zone_name,
            "agent": agent_name,
            "peers": [c.agent_name for c in zreg.get_all_cards() if c.agent_name != agent_name],
        }

    async def _h_zones_leave(self, params):
        zone_name = params.get("zone", "General")
        agent_name = params.get("agent_name", "")

        zreg = self.zones.get(zone_name)
        if zreg:
            zreg.deregister(agent_name)

        self._external_agents.pop(agent_name, None)

        return {"status": "left", "zone": zone_name, "agent": agent_name}

    async def _h_zones_create(self, params):
        zone_name = params.get("name", "")
        if not zone_name:
            return {"error": "name is required"}
        if zone_name in self.zones:
            return {"error": f"Zone '{zone_name}' already exists"}

        self.zones[zone_name] = ZoneRegistry(zone_name, self.event_handler)
        log.info(f"📍 Nova zona criada: {zone_name}")
        return {"status": "created", "zone": zone_name}

    async def _h_agent_register(self, params):
        """Registra um agente com suas tools (atalho para join com capabilities)."""
        return await self._h_zones_join(params)

    async def _h_agent_discover(self, params):
        """Descobre capabilities disponíveis numa zona."""
        zone_name = params.get("zone", "General")
        tool_filter = params.get("tool", None)

        zreg = self.zones.get(zone_name)
        if not zreg:
            return {"error": f"Zone '{zone_name}' not found"}

        if tool_filter:
            cards = zreg.find_by_tool(tool_filter)
        else:
            cards = zreg.get_all_cards()

        return {
            "zone": zone_name,
            "capabilities": [
                {
                    "agent": c.agent_name,
                    "role": c.agent_role,
                    "endpoint": c.endpoint,
                    "tools": [{"name": t.name, "description": t.description,
                              "input_schema": t.input_schema} for t in c.tools],
                }
                for c in cards
            ]
        }

    async def _h_agent_invoke(self, params):
        """Invoca uma tool de outro agente via MCP (proxy)."""
        target_agent = params.get("target_agent", "")
        tool_name = params.get("tool", "")
        arguments = params.get("arguments", {})
        zone_name = params.get("zone", "General")

        zreg = self.zones.get(zone_name)
        if not zreg:
            return {"error": f"Zone '{zone_name}' not found"}

        card = zreg.get_card(target_agent)
        if not card:
            return {"error": f"Agent '{target_agent}' not found in zone '{zone_name}'"}

        # Verificar se o target tem a tool
        has_tool = any(t.name == tool_name for t in card.tools)
        if not has_tool:
            return {"error": f"Agent '{target_agent}' does not have tool '{tool_name}'"}

        # Se o target tem endpoint, tentar invocar via HTTP
        if card.endpoint:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(card.endpoint, json={
                        "jsonrpc": "2.0", "id": 1,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": arguments}
                    })
                    data = resp.json()
                    if "result" in data:
                        return {"status": "ok", "result": data["result"]}
                    else:
                        return {"status": "error", "error": data.get("error", {})}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        else:
            return {"error": f"Agent '{target_agent}' has no reachable endpoint"}

    async def _h_agent_message(self, params):
        """Envia mensagem simbólica entre agentes na zona."""
        from_agent = params.get("from", "anonymous")
        to_agent = params.get("to", "")
        zone_name = params.get("zone", "General")
        message = params.get("message", "")

        log.info(f"💬 [{zone_name}] {from_agent} → {to_agent}: {message[:80]}")

        return {
            "status": "delivered",
            "from": from_agent,
            "to": to_agent,
            "zone": zone_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# --- Helpers ---

def _success(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}

def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

async def _send_response(send, status, body, content_type="application/json"):
    await send({
        "type": "http.response.start", "status": status,
        "headers": [[b"content-type", content_type.encode()],
                    [b"content-length", str(len(body)).encode()],
                    [b"access-control-allow-origin", b"*"]],
    })
    await send({"type": "http.response.body", "body": body})


# --- Entry point ---

def main():
    parser = argparse.ArgumentParser(description="GA2A Zone Server")
    parser.add_argument("--port", type=int, default=0, help="Port (0=auto)")
    args = parser.parse_args()

    # Pegar porta livre se não especificada
    if args.port == 0:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("", 0))
        args.port = s.getsockname()[1]
        s.close()

    server = GA2AServer(port=args.port)

    # Handle Ctrl+C
    def _signal_handler(sig, frame):
        print()
        server.stop()
        sys.exit(0)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    server.start()

    # Manter rodando
    print()
    log.info("Pressione Ctrl+C para parar")
    print()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
