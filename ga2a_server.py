"""
GA2A Zone Server — instância persistente do protocolo.

Modelo de Contexto Distribuído:
  - Agentes entram em zonas declarando CAPABILITIES (o que oferecem) e INTERESTS (o que buscam)
  - O servidor faz matchmaking: cruza interesses com capabilities disponíveis
  - Agentes solicitam contexto de outros agentes e absorvem a resposta
  - Cada agente é uma fonte de contexto especializado na rede

Fluxo:
  1. agent/join      → entra na zona com capabilities + interests
  2. agent/match     → servidor retorna quem pode atender seus interesses
  3. agent/request   → solicita contexto de outro agente (invoca tool/resource)
  4. agent/context   → consulta todo o contexto disponível para um agente na zona

Uso:
  python3 ga2a_server.py [--port PORT]
"""

import sys
sys.path.insert(0, 'src')

import argparse
import json
import logging
import signal
import socket
import time
from datetime import datetime, timezone
from typing import Any

import uvicorn

from a2a.mcp.capabilities import (
    CapabilityCard, ToolDescriptor, ResourceDescriptor, PromptDescriptor
)
from a2a.mcp.registry import ZoneRegistry
from a2a.mcp.loop_runner import AsyncLoopRunner
from a2a.events import EventHandler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [GA2A] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('ga2a')


class GA2AServer:
    """Servidor GA2A com zonas, matchmaking de interesses e contexto distribuído."""

    def __init__(self, port: int):
        self.port = port
        self.event_handler = EventHandler()
        self.zones: dict[str, ZoneRegistry] = {}
        self.loop_runner = AsyncLoopRunner()
        self._running = False
        self._server = None

        # Estado dos agentes: agent_name -> AgentState
        self._agents: dict[str, dict] = {}

        # Histórico de contexto trocado: lista de {from, to, zone, context, timestamp}
        self._context_log: list[dict] = []

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
        self.loop_runner.start()
        self._running = True

        app = self._create_app()
        config = uvicorn.Config(
            app=app, host="0.0.0.0", port=self.port,
            log_level="warning", access_log=False
        )
        self._server = uvicorn.Server(config)
        self.loop_runner.schedule(self._server.serve())

        for _ in range(50):
            if self._server.started:
                break
            time.sleep(0.1)

        log.info(f"🌐 GA2A Server rodando em http://localhost:{self.port}")
        log.info(f"   Endpoint MCP: http://localhost:{self.port}/mcp")
        log.info(f"   Zonas: {list(self.zones.keys())}")
        log.info("")
        log.info("   Métodos:")
        log.info("   ├─ initialize         → handshake")
        log.info("   ├─ zones/list         → listar zonas")
        log.info("   ├─ zones/create       → criar zona")
        log.info("   ├─ agent/join         → entrar (capabilities + interests)")
        log.info("   ├─ agent/leave        → sair da zona")
        log.info("   ├─ agent/match        → matchmaking de interesses")
        log.info("   ├─ agent/discover     → ver capabilities na zona")
        log.info("   ├─ agent/request      → solicitar contexto de outro agente")
        log.info("   ├─ agent/context      → ver contexto disponível p/ mim")
        log.info("   ├─ agent/invoke       → invocar tool (proxy MCP)")
        log.info("   └─ agent/message      → mensagem entre agentes")
        log.info("")

    def stop(self):
        self._running = False
        if self._server:
            self._server.should_exit = True
        time.sleep(0.5)
        self.loop_runner.stop()
        log.info("🛑 Server parado")

    def _create_app(self):
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
                await _send(send, 200, json.dumps({
                    "service": "GA2A Protocol Server",
                    "version": "0.3.0",
                    "model": "distributed-context-via-zones",
                    "mcp_endpoint": f"http://localhost:{server.port}/mcp",
                    "zones": list(server.zones.keys()),
                    "agents_online": len(server._agents),
                    "status": "running"
                }, indent=2).encode())

            elif method == "GET" and path == "/status":
                await _send(send, 200, json.dumps(server._get_status(), indent=2).encode())

            elif method == "POST" and path == "/mcp":
                await server._handle_mcp(scope, receive, send)

            else:
                await _send(send, 404, b'{"error":"Not found"}')

        return app

    def _get_status(self):
        status = {}
        for zname, zreg in self.zones.items():
            cards = zreg.get_all_cards()
            members = []
            for c in cards:
                agent_state = self._agents.get(c.agent_name, {})
                members.append({
                    "name": c.agent_name,
                    "role": c.agent_role,
                    "tools": [t.name for t in c.tools],
                    "interests": agent_state.get("interests", []),
                    "endpoint": c.endpoint,
                })
            status[zname] = {"agents": zreg.agent_count, "members": members}
        return status

    async def _handle_mcp(self, scope, receive, send):
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body", False):
                break

        try:
            request = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            await _send(send, 400, json.dumps(_error(None, -32700, "Parse error")).encode())
            return

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        handlers = {
            "initialize": self._h_initialize,
            "zones/list": self._h_zones_list,
            "zones/create": self._h_zones_create,
            "agent/join": self._h_agent_join,
            "agent/leave": self._h_agent_leave,
            "agent/match": self._h_agent_match,
            "agent/discover": self._h_agent_discover,
            "agent/request": self._h_agent_request,
            "agent/context": self._h_agent_context,
            "agent/invoke": self._h_agent_invoke,
            "agent/message": self._h_agent_message,
            # aliases
            "zones/join": self._h_agent_join,
            "agent/register": self._h_agent_join,
        }

        handler = handlers.get(method)
        if handler:
            result = await handler(params)
            await _send(send, 200, json.dumps(_success(req_id, result)).encode())
        else:
            await _send(send, 200, json.dumps(_error(req_id, -32601, f"Method not found: {method}")).encode())

    # ─── Handlers ───────────────────────────────────────────────

    async def _h_initialize(self, params):
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "GA2A-Server", "version": "0.3.0"},
            "model": "distributed-context",
            "capabilities": {"zones": True, "matchmaking": True, "contextSharing": True},
            "zones": list(self.zones.keys()),
        }

    async def _h_zones_list(self, params):
        result = {}
        for zname, zreg in self.zones.items():
            result[zname] = {
                "agent_count": zreg.agent_count,
                "agents": [c.agent_name for c in zreg.get_all_cards()]
            }
        return {"zones": result}

    async def _h_zones_create(self, params):
        name = params.get("name", "")
        if not name:
            return {"error": "name is required"}
        if name in self.zones:
            return {"error": f"Zone '{name}' already exists"}
        self.zones[name] = ZoneRegistry(name, self.event_handler)
        log.info(f"📍 Nova zona: {name}")
        return {"status": "created", "zone": name}

    async def _h_agent_join(self, params):
        """
        Agente entra na zona declarando:
          - capabilities: tools que oferece (o que eu sei fazer)
          - interests: o que busca (o que eu preciso de contexto)
        """
        zone_name = params.get("zone", "General")
        agent_name = params.get("agent_name", "")
        agent_role = params.get("agent_role", "agent")
        tools = params.get("tools", [])
        resources = params.get("resources", [])
        interests = params.get("interests", [])
        endpoint = params.get("endpoint", "")

        if not agent_name:
            return {"error": "agent_name is required"}

        # Criar zona se não existir
        if zone_name not in self.zones:
            self.zones[zone_name] = ZoneRegistry(zone_name, self.event_handler)

        zreg = self.zones[zone_name]

        # Construir card de capabilities
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
            agent_name=agent_name, agent_role=agent_role,
            endpoint=endpoint, transport_type="streamable-http",
            zone_name=zone_name, tools=tool_descriptors,
            resources=resource_descriptors, prompts=[],
            published_at=datetime.now(timezone.utc).isoformat(), version=1,
        )
        zreg.register(card)

        # Guardar estado do agente (com interesses)
        self._agents[agent_name] = {
            "zone": zone_name,
            "role": agent_role,
            "endpoint": endpoint,
            "tools": [t["name"] for t in tools],
            "interests": interests,
            "joined_at": datetime.now(timezone.utc).isoformat(),
        }

        # Fazer match automático se tem interesses
        matches = []
        if interests:
            matches = self._find_matches(zone_name, agent_name, interests)
            if matches:
                log.info(f"🔗 Match para {agent_name}: {[m['agent'] for m in matches]}")

        peers = [c.agent_name for c in zreg.get_all_cards() if c.agent_name != agent_name]

        return {
            "status": "joined",
            "zone": zone_name,
            "agent": agent_name,
            "peers": peers,
            "matches": matches,  # agentes que podem atender seus interesses
        }

    async def _h_agent_leave(self, params):
        zone_name = params.get("zone", "General")
        agent_name = params.get("agent_name", "")
        zreg = self.zones.get(zone_name)
        if zreg:
            zreg.deregister(agent_name)
        self._agents.pop(agent_name, None)
        return {"status": "left", "zone": zone_name, "agent": agent_name}

    async def _h_agent_match(self, params):
        """
        Busca agentes que podem atender os interesses declarados.
        Se não passar interests, usa os que foram declarados no join.
        """
        zone_name = params.get("zone", "General")
        agent_name = params.get("agent_name", "")
        interests = params.get("interests", None)

        # Se não passou interests, usar os do registro
        if interests is None:
            agent_state = self._agents.get(agent_name, {})
            interests = agent_state.get("interests", [])

        if not interests:
            return {"matches": [], "note": "No interests declared. Use 'interests' param or declare on join."}

        matches = self._find_matches(zone_name, agent_name, interests)
        return {
            "zone": zone_name,
            "agent": agent_name,
            "interests": interests,
            "matches": matches,
        }

    async def _h_agent_discover(self, params):
        zone_name = params.get("zone", "General")
        tool_filter = params.get("tool", None)
        zreg = self.zones.get(zone_name)
        if not zreg:
            return {"error": f"Zone '{zone_name}' not found"}

        cards = zreg.find_by_tool(tool_filter) if tool_filter else zreg.get_all_cards()
        return {
            "zone": zone_name,
            "capabilities": [
                {
                    "agent": c.agent_name,
                    "role": c.agent_role,
                    "endpoint": c.endpoint,
                    "tools": [{"name": t.name, "description": t.description,
                              "input_schema": t.input_schema} for t in c.tools],
                    "interests": self._agents.get(c.agent_name, {}).get("interests", []),
                }
                for c in cards
            ]
        }

    async def _h_agent_request(self, params):
        """
        Agente solicita contexto de outro agente.
        Se o target tem endpoint MCP, proxy a chamada.
        Senão, retorna a capability card como contexto disponível.
        """
        from_agent = params.get("from", "")
        target_agent = params.get("target", "")
        zone_name = params.get("zone", "General")
        interest = params.get("interest", "")  # qual interesse está buscando
        tool_name = params.get("tool", "")     # tool específica pra invocar
        arguments = params.get("arguments", {})

        zreg = self.zones.get(zone_name)
        if not zreg:
            return {"error": f"Zone '{zone_name}' not found"}

        card = zreg.get_card(target_agent)
        if not card:
            return {"error": f"Agent '{target_agent}' not found in zone '{zone_name}'"}

        # Se tem endpoint e tool, tentar invocar via proxy MCP
        if card.endpoint and tool_name:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(card.endpoint, json={
                        "jsonrpc": "2.0", "id": 1,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": arguments}
                    })
                    data = resp.json()
                    context_data = data.get("result", data.get("error", {}))
            except Exception as e:
                context_data = {"error": str(e), "fallback": "endpoint_unreachable"}
        else:
            # Retorna o contexto estático: o que esse agente oferece
            context_data = {
                "agent": target_agent,
                "role": card.agent_role,
                "available_tools": [
                    {"name": t.name, "description": t.description}
                    for t in card.tools
                ],
                "available_resources": [
                    {"uri": r.uri, "name": r.name, "description": r.description}
                    for r in card.resources
                ],
                "note": "Agent has no live endpoint. Context is its capability declaration."
            }

        # Registrar troca de contexto
        exchange = {
            "from": from_agent,
            "target": target_agent,
            "zone": zone_name,
            "interest": interest,
            "context": context_data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._context_log.append(exchange)
        log.info(f"📥 [{zone_name}] {from_agent} ← contexto de {target_agent} (interest: {interest})")

        return {
            "status": "context_delivered",
            "from": target_agent,
            "to": from_agent,
            "interest": interest,
            "context": context_data,
        }

    async def _h_agent_context(self, params):
        """
        Retorna todo o contexto disponível para um agente na zona:
        - Quem está na zona e o que oferece
        - Matches para seus interesses
        - Histórico de contexto já recebido
        """
        agent_name = params.get("agent_name", "")
        zone_name = params.get("zone", "General")

        agent_state = self._agents.get(agent_name, {})
        interests = agent_state.get("interests", [])

        zreg = self.zones.get(zone_name)
        if not zreg:
            return {"error": f"Zone '{zone_name}' not found"}

        # Contexto disponível na zona
        available = []
        for card in zreg.get_all_cards():
            if card.agent_name != agent_name:
                available.append({
                    "agent": card.agent_name,
                    "role": card.agent_role,
                    "tools": [t.name for t in card.tools],
                    "match_score": self._calc_match_score(interests, card),
                })

        # Ordenar por relevância (match_score)
        available.sort(key=lambda x: x["match_score"], reverse=True)

        # Histórico de contexto recebido
        history = [
            e for e in self._context_log
            if e["from"] == agent_name or e["target"] == agent_name
        ][-10:]  # últimos 10

        return {
            "agent": agent_name,
            "zone": zone_name,
            "my_interests": interests,
            "available_context": available,
            "context_history": history,
        }

    async def _h_agent_invoke(self, params):
        """Proxy: invoca tool de outro agente via endpoint MCP."""
        target = params.get("target_agent", params.get("target", ""))
        tool_name = params.get("tool", "")
        arguments = params.get("arguments", {})
        zone_name = params.get("zone", "General")

        zreg = self.zones.get(zone_name)
        if not zreg:
            return {"error": f"Zone '{zone_name}' not found"}

        card = zreg.get_card(target)
        if not card:
            return {"error": f"Agent '{target}' not found in zone '{zone_name}'"}

        if not card.endpoint:
            return {"error": f"Agent '{target}' has no live endpoint for invocation"}

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

    async def _h_agent_message(self, params):
        from_agent = params.get("from", "anonymous")
        to_agent = params.get("to", "")
        zone_name = params.get("zone", "General")
        message = params.get("message", "")

        log.info(f"💬 [{zone_name}] {from_agent} → {to_agent}: {message[:80]}")
        return {
            "status": "delivered",
            "from": from_agent, "to": to_agent,
            "zone": zone_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ─── Matchmaking ────────────────────────────────────────────

    def _find_matches(self, zone_name: str, requester: str, interests: list[str]) -> list[dict]:
        """Encontra agentes cujas capabilities atendem os interesses."""
        zreg = self.zones.get(zone_name)
        if not zreg:
            return []

        matches = []
        for card in zreg.get_all_cards():
            if card.agent_name == requester:
                continue

            score = self._calc_match_score(interests, card)
            if score > 0:
                matching_tools = []
                for t in card.tools:
                    for interest in interests:
                        if interest.lower() in t.name.lower() or interest.lower() in t.description.lower():
                            matching_tools.append({"name": t.name, "description": t.description})
                            break

                matches.append({
                    "agent": card.agent_name,
                    "role": card.agent_role,
                    "score": score,
                    "matching_tools": matching_tools,
                    "endpoint": card.endpoint,
                })

        matches.sort(key=lambda x: x["score"], reverse=True)
        return matches

    def _calc_match_score(self, interests: list[str], card: CapabilityCard) -> int:
        """Calcula score de match entre interesses e capabilities de um agente."""
        score = 0
        searchable = " ".join([
            " ".join(t.name + " " + t.description for t in card.tools),
            " ".join(r.name + " " + r.description for r in card.resources),
            card.agent_role,
        ]).lower()

        for interest in interests:
            if interest.lower() in searchable:
                score += 1
        return score


# ─── Helpers ───────────────────────────────────────────────────

def _success(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}

def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

async def _send(send_fn, status, body, content_type="application/json"):
    await send_fn({
        "type": "http.response.start", "status": status,
        "headers": [[b"content-type", content_type.encode()],
                    [b"content-length", str(len(body)).encode()],
                    [b"access-control-allow-origin", b"*"]],
    })
    await send_fn({"type": "http.response.body", "body": body})


# ─── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GA2A Zone Server")
    parser.add_argument("--port", type=int, default=0, help="Port (0=auto)")
    args = parser.parse_args()

    if args.port == 0:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("", 0))
        args.port = s.getsockname()[1]
        s.close()

    server = GA2AServer(port=args.port)

    def _sig(sig, frame):
        print()
        server.stop()
        sys.exit(0)
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    server.start()
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
