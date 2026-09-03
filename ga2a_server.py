"""
GA2A Zone Server v0.5.0 — instância persistente do protocolo com descoberta LAN,
autorização por consentimento e descoberta unicast/gossip (VPN / cross-subnet).

Modelo de Contexto Distribuído (agora federado pela rede local):
  - Agentes entram em zonas declarando CAPABILITIES (o que oferecem) e INTERESTS (o que buscam)
  - O servidor faz matchmaking: cruza interesses com capabilities disponíveis
  - Agentes solicitam contexto de outros agentes e absorvem a resposta
  - Cada agente é uma fonte de contexto especializado na rede
  - NOVO: instâncias GA2A se descobrem na LAN via broadcast UDP. Agentes de outras
    máquinas aparecem como "remote agents" e podem ser invocados via proxy.

Fluxo:
  1. agent/join      → entra na zona com capabilities + interests (broadcast na LAN)
  2. agent/match     → servidor retorna quem pode atender seus interesses (local + remoto)
  3. agent/request   → solicita contexto de outro agente (invoca tool/resource)
  4. agent/context   → consulta todo o contexto disponível para um agente na zona
  5. network/peers   → lista instâncias GA2A descobertas na LAN
  6. network/agents  → lista TODOS os agentes (locais + remotos)
  7. network/find    → busca um agente ou tool em toda a rede

Uso:
  python3 ga2a_server.py --port 9420 [--name my-instance] [--broadcast-port 5060]
                         [--peer host:port ...]
"""

import sys
sys.path.insert(0, 'src')

import argparse
import json
import logging
import signal
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import uvicorn

from a2a.mcp.capabilities import (
    CapabilityCard, ToolDescriptor, ResourceDescriptor, PromptDescriptor
)
from a2a.mcp.registry import ZoneRegistry
from a2a.mcp.loop_runner import AsyncLoopRunner
from a2a.mcp.grants import GrantManager, AccessRequest, AccessGrant
from a2a.events import EventHandler
from a2a.discovery import NetworkDiscovery, get_local_ip

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [GA2A] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('ga2a')

VERSION = "0.5.0"


class GA2AServer:
    """Servidor GA2A com zonas, matchmaking, contexto distribuído e descoberta LAN."""

    def __init__(self, my_port: int, instance_name: str = None, broadcast_port: int = 5060,
                 seed_peers: list[str] = None):
        self.port = my_port
        self.my_port = my_port
        self.my_host = get_local_ip()
        self.instance_name = instance_name or f"ga2a-{self.my_host}-{my_port}"

        self.event_handler = EventHandler()
        self.zones: dict[str, ZoneRegistry] = {}
        self.loop_runner = AsyncLoopRunner()
        self._running = False
        self._server = None

        # Estado dos agentes locais: agent_name -> AgentState
        self._agents: dict[str, dict] = {}

        # ─── Autorização por consentimento (v0.5.0) ───
        # Segredo server-wide usado para assinar grants de todos os agentes locais.
        self._grant_secret = uuid.uuid4().hex
        # Registro de GrantManagers por agente local dono: agent_name -> GrantManager
        self._grant_managers: dict[str, GrantManager] = {}

        # ─── Descoberta unicast/gossip (v0.5.0) ───
        # Seed peers passados via --peer ("host:port")
        self._seed_peers: list[str] = list(seed_peers or [])
        # Thread de re-contato periódico dos seeds
        self._gossip_thread: threading.Thread = None

        # Agentes remotos descobertos em outras instâncias da LAN:
        #   agent_name -> {role, tools, interests, zones, peer_endpoint, peer_instance, peer_host}
        self._remote_agents: dict[str, dict] = {}

        # Histórico de contexto trocado
        self._context_log: list[dict] = []

        # Descoberta de rede (LAN)
        self.discovery = NetworkDiscovery(broadcast_port=broadcast_port)
        self.discovery.on_peer_discovered(self._on_remote_peer_discovered)
        self.discovery.on_peer_updated(self._on_remote_peer_updated)
        self.discovery.on_peer_lost(self._on_remote_peer_lost)

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

    # ─── Autorização por consentimento ──────────────────────────

    def _get_grant_manager(self, agent_name: str) -> GrantManager:
        """Retorna (criando se necessário) o GrantManager de um agente local."""
        mgr = self._grant_managers.get(agent_name)
        if mgr is None:
            mgr = GrantManager(agent_name, self._grant_secret)
            self._grant_managers[agent_name] = mgr
        return mgr

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

        # Publicar nossa identidade e iniciar a descoberta LAN
        self._update_discovery_identity()
        self.discovery.start()

        # Semear a partir dos peers unicast (VPN / cross-subnet) e iniciar o gossip
        self._seed_unicast_peers()
        self._start_gossip_loop()

        log.info(f"🌐 GA2A Server v{VERSION} rodando")
        log.info(f"   Instância: {self.instance_name}")
        log.info(f"   Host LAN:  {self.my_host}:{self.my_port}")
        log.info(f"   Endpoint MCP: http://{self.my_host}:{self.my_port}/mcp")
        log.info(f"   Broadcast LAN: porta {self.discovery.broadcast_port}")
        if self._seed_peers:
            log.info(f"   Seed peers (unicast): {self._seed_peers}")
        log.info(f"   Zonas: {list(self.zones.keys())}")
        log.info("")
        log.info("   Métodos:")
        log.info("   ├─ initialize         → handshake")
        log.info("   ├─ zones/list         → listar zonas (local + remoto)")
        log.info("   ├─ zones/create       → criar zona")
        log.info("   ├─ agent/join         → entrar (capabilities + interests + auto_approve)")
        log.info("   ├─ agent/leave        → sair da zona")
        log.info("   ├─ agent/match        → matchmaking (local + remoto)")
        log.info("   ├─ agent/discover     → ver capabilities (local + remoto)")
        log.info("   ├─ agent/request      → solicitar contexto de outro agente")
        log.info("   ├─ agent/context      → ver contexto disponível p/ mim")
        log.info("   ├─ agent/invoke       → invocar tool (requer grant p/ agentes locais)")
        log.info("   ├─ agent/message      → mensagem entre agentes")
        log.info("   ├─ access/request     → pedir autorização de interação (consent)")
        log.info("   ├─ access/pending     → listar pedidos pendentes (dono)")
        log.info("   ├─ access/approve     → aprovar pedido → emite grant")
        log.info("   ├─ access/deny        → negar pedido")
        log.info("   ├─ access/revoke      → revogar um grant")
        log.info("   ├─ access/grants      → listar grants emitidos")
        log.info("   ├─ network/peers      → instâncias GA2A na rede")
        log.info("   ├─ network/agents     → todos os agentes (local + remoto)")
        log.info("   ├─ network/hello      → handshake unicast + gossip (VPN)")
        log.info("   └─ network/find       → buscar agente/tool na rede")
        log.info("")

    def stop(self):
        self._running = False
        try:
            self.discovery.stop()
        except Exception:
            pass
        if self._server:
            self._server.should_exit = True
        time.sleep(0.5)
        self.loop_runner.stop()
        log.info("🛑 Server parado")

    # ─── Descoberta LAN ─────────────────────────────────────────

    def _update_discovery_identity(self):
        """Reconstrói a identidade broadcast a partir das zonas locais."""
        agents_by_zone: dict[str, list[str]] = {}
        agent_details: dict[str, dict] = {}

        for zname, zreg in self.zones.items():
            names = []
            for card in zreg.get_all_cards():
                names.append(card.agent_name)
                state = self._agents.get(card.agent_name, {})
                agent_details[card.agent_name] = {
                    "role": card.agent_role,
                    "tools": [t.name for t in card.tools],
                    "interests": state.get("interests", []),
                    "zone": zname,
                    "endpoint": card.endpoint,
                }
            if names:
                agents_by_zone[zname] = names

        active_zones = [z for z, reg in self.zones.items() if reg.agent_count > 0]

        self.discovery.set_identity(
            instance_id=self.instance_name,
            host=self.my_host,
            port=self.my_port,
            zones=active_zones,
            agents=agents_by_zone,
            agent_details=agent_details,
        )

    def _on_remote_peer_discovered(self, peer: dict):
        """Novo peer descoberto: registrar seus agentes como remotos."""
        self._index_remote_peer(peer)
        log.info(
            f"🟢 Instância LAN descoberta: {peer.get('instance_id')} "
            f"@ {peer.get('mcp_endpoint')} "
            f"({sum(len(v) for v in peer.get('agents', {}).values())} agentes)"
        )

    def _on_remote_peer_updated(self, peer: dict):
        """Peer atualizou seu estado: re-indexar seus agentes."""
        self._index_remote_peer(peer)

    def _on_remote_peer_lost(self, peer: dict):
        """Peer perdido (timeout): remover todos os seus agentes remotos."""
        instance_id = peer.get("instance_id", "")
        removed = [
            name for name, info in list(self._remote_agents.items())
            if info.get("peer_instance") == instance_id
        ]
        for name in removed:
            self._remote_agents.pop(name, None)
        if removed:
            log.info(f"🔴 Instância LAN perdida: {instance_id} (removidos {len(removed)} agentes remotos)")

    def _index_remote_peer(self, peer: dict):
        """Reconstrói as entradas de _remote_agents para um peer."""
        instance_id = peer.get("instance_id", "")
        endpoint = peer.get("mcp_endpoint", "")
        host = peer.get("host", "")

        # Remover entradas antigas desse peer antes de re-indexar
        for name, info in list(self._remote_agents.items()):
            if info.get("peer_instance") == instance_id:
                self._remote_agents.pop(name, None)

        details = peer.get("agent_details", {})
        agents_by_zone = peer.get("agents", {})

        # Mapa reverso agent -> zone (a partir de agents_by_zone)
        agent_zone: dict[str, str] = {}
        for zone, names in agents_by_zone.items():
            for name in names:
                agent_zone[name] = zone

        # Preferir detalhes completos quando disponíveis
        for name, info in details.items():
            self._remote_agents[name] = {
                "role": info.get("role", ""),
                "tools": info.get("tools", []),
                "interests": info.get("interests", []),
                "zone": info.get("zone", agent_zone.get(name, "")),
                "peer_endpoint": endpoint,
                "peer_instance": instance_id,
                "peer_host": host,
            }

        # Fallback: agentes listados sem detalhes
        for name, zone in agent_zone.items():
            if name not in self._remote_agents:
                self._remote_agents[name] = {
                    "role": "",
                    "tools": [],
                    "interests": [],
                    "zone": zone,
                    "peer_endpoint": endpoint,
                    "peer_instance": instance_id,
                    "peer_host": host,
                }

    async def _proxy_to_remote(self, endpoint: str, method: str, params: dict) -> dict:
        """Encaminha uma chamada JSON-RPC para a instância remota."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(endpoint, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": method,
                    "params": params,
                })
                data = resp.json()
                if "result" in data:
                    return {"status": "ok", "result": data["result"]}
                return {"status": "error", "error": data.get("error", {})}
        except Exception as e:
            return {"status": "error", "error": str(e), "fallback": "peer_unreachable"}

    # ─── Descoberta unicast + gossip (v0.5.0) ───────────────────

    def _self_identity(self) -> dict:
        """Constrói o dict de identidade deste servidor para o handshake unicast."""
        agents_by_zone: dict[str, list[str]] = {}
        agent_details: dict[str, dict] = {}
        for zname, zreg in self.zones.items():
            names = []
            for card in zreg.get_all_cards():
                names.append(card.agent_name)
                state = self._agents.get(card.agent_name, {})
                agent_details[card.agent_name] = {
                    "role": card.agent_role,
                    "tools": [t.name for t in card.tools],
                    "interests": state.get("interests", []),
                    "zone": zname,
                    "endpoint": card.endpoint,
                }
            if names:
                agents_by_zone[zname] = names

        active_zones = [z for z, reg in self.zones.items() if reg.agent_count > 0]
        return {
            "instance_id": self.instance_name,
            "host": self.my_host,
            "port": self.my_port,
            "mcp_endpoint": f"http://{self.my_host}:{self.my_port}/mcp",
            "zones": active_zones,
            "agents": agents_by_zone,
            "agent_details": agent_details,
        }

    def _known_peer_identities(self) -> list[dict]:
        """Lista de identidades dos peers conhecidos (para gossip)."""
        out = []
        for pid, peer in self.discovery.get_peers().items():
            out.append({
                "instance_id": pid,
                "host": peer.get("host", ""),
                "port": peer.get("port", 0),
                "mcp_endpoint": peer.get("mcp_endpoint", ""),
                "zones": peer.get("zones", []),
                "agents": peer.get("agents", {}),
                "agent_details": peer.get("agent_details", {}),
            })
        return out

    def _learn_peer(self, peer: dict):
        """Registra um peer aprendido via unicast/gossip (ignora a si mesmo)."""
        if not peer or not peer.get("instance_id"):
            return
        if peer.get("instance_id") == self.instance_name:
            return
        try:
            self.discovery.register_unicast_peer(peer)
        except Exception as e:
            log.debug(f"Falha ao registrar peer unicast: {e}")

    def _hello_seed(self, address: str):
        """Envia network/hello a um seed 'host:port' e aprende sua malha (gossip)."""
        addr = address.strip()
        if not addr:
            return
        if "://" in addr:
            endpoint = addr.rstrip("/")
            if not endpoint.endswith("/mcp"):
                endpoint = endpoint + "/mcp"
        else:
            endpoint = f"http://{addr}/mcp"

        try:
            import httpx
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(endpoint, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "network/hello",
                    "params": self._self_identity(),
                })
                data = resp.json()
                result = data.get("result", {})
            # Registrar o próprio seed (sua identidade)
            peer_identity = result.get("identity")
            if peer_identity:
                self._learn_peer(peer_identity)
            # Gossip: registrar todos os peers que o seed conhece
            for p in result.get("known_peers", []):
                self._learn_peer(p)
        except Exception as e:
            log.debug(f"Seed unicast '{address}' inacessível: {e}")

    def _seed_unicast_peers(self):
        """Contata todos os seed peers uma vez no startup."""
        for addr in self._seed_peers:
            self._hello_seed(addr)

    def _start_gossip_loop(self):
        """Re-contata seeds periodicamente para manter a malha viva (unicast)."""
        if not self._seed_peers:
            return

        def _loop():
            interval = max(self.discovery.broadcast_interval * 2, 1)
            while self._running:
                time.sleep(interval)
                if not self._running:
                    break
                for addr in list(self._seed_peers):
                    self._hello_seed(addr)

        self._gossip_thread = threading.Thread(target=_loop, daemon=True)
        self._gossip_thread.start()

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
                    "version": VERSION,
                    "model": "distributed-context-via-zones + LAN federation",
                    "instance": server.instance_name,
                    "host": server.my_host,
                    "port": server.my_port,
                    "mcp_endpoint": f"http://{server.my_host}:{server.my_port}/mcp",
                    "zones": list(server.zones.keys()),
                    "local_agents": len(server._agents),
                    "remote_agents": len(server._remote_agents),
                    "lan_peers": len(server.discovery.get_peers()),
                    "status": "running"
                }, indent=2).encode())

            elif method == "GET" and path == "/status":
                await _send(send, 200, json.dumps(server._get_full_status(), indent=2).encode())

            elif method == "POST" and path == "/mcp":
                await server._handle_mcp(scope, receive, send)

            else:
                await _send(send, 404, b'{"error":"Not found"}')

        return app

    def _get_full_status(self):
        # Zonas locais
        local_zones = {}
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
            local_zones[zname] = {"agents": zreg.agent_count, "members": members}

        # Agentes remotos agrupados por instância
        remote_by_instance: dict[str, dict] = {}
        for name, info in self._remote_agents.items():
            inst = info.get("peer_instance", "unknown")
            entry = remote_by_instance.setdefault(inst, {
                "peer_endpoint": info.get("peer_endpoint", ""),
                "peer_host": info.get("peer_host", ""),
                "agents": [],
            })
            entry["agents"].append({
                "name": name,
                "role": info.get("role", ""),
                "tools": info.get("tools", []),
                "zone": info.get("zone", ""),
            })

        # Peers LAN
        lan_peers = []
        for pid, peer in self.discovery.get_peers().items():
            lan_peers.append({
                "instance": pid,
                "host": peer.get("host", ""),
                "port": peer.get("port", 0),
                "mcp_endpoint": peer.get("mcp_endpoint", ""),
                "zones": peer.get("zones", []),
                "agent_count": sum(len(v) for v in peer.get("agents", {}).values()),
            })

        return {
            "instance": self.instance_name,
            "host": self.my_host,
            "port": self.my_port,
            "version": VERSION,
            "local_zones": local_zones,
            "remote_agents": remote_by_instance,
            "lan_peers": lan_peers,
        }

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
            # access / consent (v0.5.0)
            "access/request": self._h_access_request,
            "access/pending": self._h_access_pending,
            "access/approve": self._h_access_approve,
            "access/deny": self._h_access_deny,
            "access/revoke": self._h_access_revoke,
            "access/grants": self._h_access_grants,
            # network (v0.4.0)
            "network/peers": self._h_network_peers,
            "network/agents": self._h_network_agents,
            "network/find": self._h_network_find,
            # network unicast/gossip (v0.5.0)
            "network/hello": self._h_network_hello,
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
            "serverInfo": {"name": "GA2A-Server", "version": VERSION},
            "model": "distributed-context + LAN federation",
            "instance": self.instance_name,
            "host": self.my_host,
            "capabilities": {
                "zones": True, "matchmaking": True, "contextSharing": True,
                "lanDiscovery": True, "remoteProxy": True,
            },
            "zones": list(self.zones.keys()),
        }

    async def _h_zones_list(self, params):
        """Zonas locais + zonas dos peers remotos."""
        result = {}
        for zname, zreg in self.zones.items():
            result[zname] = {
                "agent_count": zreg.agent_count,
                "agents": [c.agent_name for c in zreg.get_all_cards()],
                "source": "local",
            }

        # Agregar zonas remotas
        for pid, peer in self.discovery.get_peers().items():
            for zname, names in peer.get("agents", {}).items():
                entry = result.setdefault(zname, {
                    "agent_count": 0, "agents": [], "source": "remote",
                })
                # Não duplicar contagem local; marcar como misto se já existia
                if entry.get("source") == "local":
                    entry["source"] = "mixed"
                entry.setdefault("remote_agents", [])
                for n in names:
                    entry["remote_agents"].append({"agent": n, "instance": pid})
                entry["agent_count"] = entry.get("agent_count", 0) + len(names)

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
        Após o registro, a identidade é rebroadcast na LAN.
        """
        zone_name = params.get("zone", "General")
        agent_name = params.get("agent_name", "")
        agent_role = params.get("agent_role", "agent")
        tools = params.get("tools", [])
        resources = params.get("resources", [])
        interests = params.get("interests", [])
        endpoint = params.get("endpoint", "")
        auto_approve = params.get("auto_approve", None)

        if not agent_name:
            return {"error": "agent_name is required"}

        if zone_name not in self.zones:
            self.zones[zone_name] = ZoneRegistry(zone_name, self.event_handler)

        zreg = self.zones[zone_name]

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

        # Consentimento (v0.5.0): todo agente local ganha um GrantManager.
        # - auto_approve=True         → política vazia (aprova tudo automaticamente)
        # - auto_approve=[{...}, ...]  → cada dict vira uma política add_policy(**dict)
        # - ausente/False              → interações ficam pendentes até approve manual
        mgr = self._get_grant_manager(agent_name)
        requires_consent = True
        if auto_approve is True:
            mgr.add_policy()  # política vazia = aprova tudo
            requires_consent = False
        elif isinstance(auto_approve, list):
            for policy in auto_approve:
                if isinstance(policy, dict):
                    mgr.add_policy(**policy)

        self._agents[agent_name] = {
            "zone": zone_name,
            "role": agent_role,
            "endpoint": endpoint,
            "tools": [t["name"] for t in tools],
            "interests": interests,
            "auto_approve": auto_approve if auto_approve is not None else False,
            "requires_consent": requires_consent,
            "joined_at": datetime.now(timezone.utc).isoformat(),
        }

        # Rebroadcast na LAN: o novo agente passa a ser visível para outras instâncias
        self._update_discovery_identity()

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
            "matches": matches,
        }

    async def _h_agent_leave(self, params):
        zone_name = params.get("zone", "General")
        agent_name = params.get("agent_name", "")
        zreg = self.zones.get(zone_name)
        if zreg:
            zreg.deregister(agent_name)
        self._agents.pop(agent_name, None)
        self._grant_managers.pop(agent_name, None)

        # Rebroadcast na LAN após remover o agente
        self._update_discovery_identity()

        return {"status": "left", "zone": zone_name, "agent": agent_name}

    async def _h_agent_match(self, params):
        """
        Busca agentes (locais + remotos) que atendem os interesses declarados.
        """
        zone_name = params.get("zone", "General")
        agent_name = params.get("agent_name", "")
        interests = params.get("interests", None)

        if interests is None:
            agent_state = self._agents.get(agent_name, {})
            interests = agent_state.get("interests", [])

        if not interests:
            return {"matches": [], "note": "No interests declared. Use 'interests' param or declare on join."}

        matches = self._find_matches(zone_name, agent_name, interests)
        remote_matches = self._find_remote_matches(agent_name, interests, zone_name)

        return {
            "zone": zone_name,
            "agent": agent_name,
            "interests": interests,
            "matches": matches,
            "remote_matches": remote_matches,
        }

    async def _h_agent_discover(self, params):
        zone_name = params.get("zone", "General")
        tool_filter = params.get("tool", None)
        include_remote = params.get("include_remote", True)

        zreg = self.zones.get(zone_name)
        if not zreg:
            return {"error": f"Zone '{zone_name}' not found"}

        cards = zreg.find_by_tool(tool_filter) if tool_filter else zreg.get_all_cards()
        capabilities = [
            {
                "agent": c.agent_name,
                "role": c.agent_role,
                "endpoint": c.endpoint,
                "tools": [{"name": t.name, "description": t.description,
                          "input_schema": t.input_schema} for t in c.tools],
                "interests": self._agents.get(c.agent_name, {}).get("interests", []),
                "source": "local",
            }
            for c in cards
        ]

        remote_capabilities = []
        if include_remote:
            for name, info in self._remote_agents.items():
                if zone_name and info.get("zone") and info.get("zone") != zone_name:
                    continue
                if tool_filter and tool_filter not in info.get("tools", []):
                    continue
                remote_capabilities.append({
                    "agent": name,
                    "role": info.get("role", ""),
                    "endpoint": info.get("peer_endpoint", ""),
                    "tools": [{"name": t, "description": "", "input_schema": {}}
                              for t in info.get("tools", [])],
                    "interests": info.get("interests", []),
                    "source": "remote",
                    "peer_instance": info.get("peer_instance", ""),
                    "peer_host": info.get("peer_host", ""),
                })

        return {
            "zone": zone_name,
            "capabilities": capabilities,
            "remote_capabilities": remote_capabilities,
        }

    async def _h_agent_request(self, params):
        """
        Agente solicita contexto de outro agente (local ou remoto).
        Remoto: proxy da chamada para a instância que hospeda o agente.
        """
        from_agent = params.get("from", "")
        target_agent = params.get("target", "")
        zone_name = params.get("zone", "General")
        interest = params.get("interest", "")
        tool_name = params.get("tool", "")
        arguments = params.get("arguments", {})

        zreg = self.zones.get(zone_name)
        card = zreg.get_card(target_agent) if zreg else None

        if card:
            # Agente local
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
        elif target_agent in self._remote_agents:
            # Agente remoto: proxy do request para a instância dona
            info = self._remote_agents[target_agent]
            endpoint = info.get("peer_endpoint", "")
            proxied = await self._proxy_to_remote(endpoint, "agent/request", {
                "from": from_agent,
                "target": target_agent,
                "zone": info.get("zone", zone_name),
                "interest": interest,
                "tool": tool_name,
                "arguments": arguments,
            })
            if proxied.get("status") == "ok":
                context_data = proxied["result"].get("context", proxied["result"])
            else:
                context_data = {"error": proxied.get("error"), "source": "remote_proxy"}
        else:
            return {"error": f"Agent '{target_agent}' not found (local zone '{zone_name}' or remote)"}

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

        available = []
        for card in zreg.get_all_cards():
            if card.agent_name != agent_name:
                available.append({
                    "agent": card.agent_name,
                    "role": card.agent_role,
                    "tools": [t.name for t in card.tools],
                    "match_score": self._calc_match_score(interests, card),
                })

        available.sort(key=lambda x: x["match_score"], reverse=True)

        history = [
            e for e in self._context_log
            if e["from"] == agent_name or e["target"] == agent_name
        ][-10:]

        return {
            "agent": agent_name,
            "zone": zone_name,
            "my_interests": interests,
            "available_context": available,
            "context_history": history,
        }

    async def _h_agent_invoke(self, params):
        """
        Proxy: invoca tool de outro agente.
        - Agente local: invoca diretamente o endpoint MCP do agente.
        - Agente remoto: encaminha 'agent/invoke' para a instância dona,
          que resolve localmente contra o endpoint do agente.
        """
        target = params.get("target_agent", params.get("target", ""))
        tool_name = params.get("tool", "")
        arguments = params.get("arguments", {})
        zone_name = params.get("zone", "General")
        grant = params.get("grant", "")

        zreg = self.zones.get(zone_name)
        card = zreg.get_card(target) if zreg else None

        if card:
            # Consentimento (v0.5.0): agentes locais com GrantManager exigem grant válido.
            # Se o alvo nunca registrou um GrantManager (nunca exigiu consent), permite
            # (compatibilidade retroativa). A checagem de autorização vem ANTES da
            # verificação de endpoint para não vazar estado do agente sem consent.
            if target in self._grant_managers:
                mgr = self._grant_managers[target]
                valid = mgr.validate_grant(grant, tool_name) if grant else None
                if valid is None:
                    return {
                        "error": "authorization_required",
                        "hint": "call access/request first",
                        "target": target,
                    }

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
                    return {"status": "error", "error": data.get("error", {})}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        # Agente remoto: proxy do invoke para a instância dona
        if target in self._remote_agents:
            info = self._remote_agents[target]
            endpoint = info.get("peer_endpoint", "")
            log.info(f"🛰️  Proxy invoke '{target}.{tool_name}' → {info.get('peer_instance')} ({endpoint})")
            return await self._proxy_to_remote(endpoint, "agent/invoke", {
                "target_agent": target,
                "tool": tool_name,
                "arguments": arguments,
                "zone": info.get("zone", zone_name),
                "grant": grant,
            })

        return {"error": f"Agent '{target}' not found (local zone '{zone_name}' or remote)"}

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

    # ─── Access / consentimento (v0.5.0) ────────────────────────

    async def _h_access_request(self, params):
        """
        Solicita autorização para interagir com um agente (local ou remoto).
        Params: {from_agent, target, zone, interest, tool}

        Local: submete o pedido ao GrantManager do alvo. Se auto-aprovado por
        política, também retorna o token de grant serializado.
        Remoto: encaminha o pedido para a instância dona do agente.
        """
        from_agent = params.get("from_agent", params.get("from", ""))
        target = params.get("target", params.get("target_agent", ""))
        zone_name = params.get("zone", "General")
        interest = params.get("interest", "")
        tool_name = params.get("tool", "")

        if not target:
            return {"error": "target is required"}

        # Alvo remoto: proxy do pedido para a instância dona
        if target not in self._grant_managers and target in self._remote_agents:
            info = self._remote_agents[target]
            endpoint = info.get("peer_endpoint", "")
            proxied = await self._proxy_to_remote(endpoint, "access/request", {
                "from_agent": from_agent,
                "target": target,
                "zone": info.get("zone", zone_name),
                "interest": interest,
                "tool": tool_name,
            })
            if proxied.get("status") == "ok":
                return proxied["result"]
            return {"error": proxied.get("error"), "source": "remote_proxy"}

        # Alvo local: criar GrantManager se ainda não existir
        mgr = self._get_grant_manager(target)
        request = mgr.submit_request(
            from_agent=from_agent, zone=zone_name,
            interest=interest, tool=tool_name,
        )

        result = {
            "request_id": request.request_id,
            "status": request.status,
            "target": target,
        }
        if request.status == "approved":
            grant = mgr.get_grant_for_request(request.request_id)
            if grant is not None:
                result["grant"] = mgr.serialize_grant(grant)

        log.info(f"🔑 access/request {from_agent} → {target} (tool: {tool_name or '*'}) = {request.status}")
        return result

    async def _h_access_pending(self, params):
        """Lista pedidos pendentes para um agente local. Params: {agent_name}."""
        agent_name = params.get("agent_name", "")
        if not agent_name:
            return {"error": "agent_name is required"}
        mgr = self._grant_managers.get(agent_name)
        pending = mgr.list_pending() if mgr else []
        return {
            "agent": agent_name,
            "pending": [r.to_dict() for r in pending],
            "count": len(pending),
        }

    async def _h_access_approve(self, params):
        """Aprova um pedido pendente e emite um grant. Params: {agent_name, request_id, scope?, ttl?}."""
        agent_name = params.get("agent_name", "")
        request_id = params.get("request_id", "")
        scope = params.get("scope", None)
        ttl = params.get("ttl", None)

        if not agent_name or not request_id:
            return {"error": "agent_name and request_id are required"}

        mgr = self._grant_managers.get(agent_name)
        if mgr is None:
            return {"error": f"No grant manager for agent '{agent_name}'"}

        grant = mgr.approve(request_id, scope=scope, ttl=ttl)
        if grant is None:
            return {"error": "request not found or not pending", "request_id": request_id}

        log.info(f"✅ access/approve {agent_name} aprovou {request_id} (scope: {grant.scope})")
        return {"status": "approved", "grant_token": mgr.serialize_grant(grant)}

    async def _h_access_deny(self, params):
        """Nega um pedido pendente. Params: {agent_name, request_id}."""
        agent_name = params.get("agent_name", "")
        request_id = params.get("request_id", "")

        if not agent_name or not request_id:
            return {"error": "agent_name and request_id are required"}

        mgr = self._grant_managers.get(agent_name)
        if mgr is None:
            return {"error": f"No grant manager for agent '{agent_name}'"}

        ok = mgr.deny(request_id)
        log.info(f"⛔ access/deny {agent_name} negou {request_id} = {ok}")
        return {"status": "denied" if ok else "not_found", "request_id": request_id}

    async def _h_access_revoke(self, params):
        """Revoga um grant emitido. Params: {agent_name, grant_id}."""
        agent_name = params.get("agent_name", "")
        grant_id = params.get("grant_id", "")

        if not agent_name or not grant_id:
            return {"error": "agent_name and grant_id are required"}

        mgr = self._grant_managers.get(agent_name)
        if mgr is None:
            return {"error": f"No grant manager for agent '{agent_name}'"}

        ok = mgr.revoke(grant_id)
        log.info(f"🗑️  access/revoke {agent_name} revogou {grant_id} = {ok}")
        return {"status": "revoked" if ok else "not_found", "grant_id": grant_id}

    async def _h_access_grants(self, params):
        """Lista os grants emitidos por um agente local. Params: {agent_name}."""
        agent_name = params.get("agent_name", "")
        if not agent_name:
            return {"error": "agent_name is required"}
        mgr = self._grant_managers.get(agent_name)
        grants = mgr.list_grants() if mgr else []
        return {
            "agent": agent_name,
            "grants": [g.to_dict() for g in grants],
            "count": len(grants),
        }

    # ─── Network (v0.4.0) ───────────────────────────────────────

    async def _h_network_hello(self, params):
        """
        Handshake unicast + gossip (v0.5.0).

        Recebe a identidade de um peer (params = identity dict), registra-o
        como peer conhecido (via NetworkDiscovery) e responde com NOSSA
        identidade E a lista dos peers que conhecemos (gossip), para que o
        remetente aprenda a malha inteira.
        """
        if params and params.get("instance_id"):
            self._learn_peer(params)
            # Gossip: o peer pode ter anexado os peers que ele conhece
            for p in params.get("known_peers", []):
                self._learn_peer(p)

        return {
            "identity": self._self_identity(),
            "known_peers": self._known_peer_identities(),
        }

    async def _h_network_peers(self, params):
        """Lista todas as instâncias GA2A descobertas na LAN."""
        peers = []
        for pid, peer in self.discovery.get_peers().items():
            peers.append({
                "instance": pid,
                "host": peer.get("host", ""),
                "port": peer.get("port", 0),
                "mcp_endpoint": peer.get("mcp_endpoint", ""),
                "zones": peer.get("zones", []),
                "agent_count": sum(len(v) for v in peer.get("agents", {}).values()),
                "last_seen": peer.get("timestamp", 0),
            })
        return {
            "self": {
                "instance": self.instance_name,
                "host": self.my_host,
                "port": self.my_port,
                "mcp_endpoint": f"http://{self.my_host}:{self.my_port}/mcp",
            },
            "peers": peers,
            "peer_count": len(peers),
        }

    async def _h_network_agents(self, params):
        """Lista TODOS os agentes: locais + remotos (de outras máquinas)."""
        local = []
        for name, state in self._agents.items():
            local.append({
                "agent": name,
                "role": state.get("role", ""),
                "zone": state.get("zone", ""),
                "tools": state.get("tools", []),
                "interests": state.get("interests", []),
                "source": "local",
                "instance": self.instance_name,
            })

        remote = []
        for name, info in self._remote_agents.items():
            remote.append({
                "agent": name,
                "role": info.get("role", ""),
                "zone": info.get("zone", ""),
                "tools": info.get("tools", []),
                "interests": info.get("interests", []),
                "source": "remote",
                "instance": info.get("peer_instance", ""),
                "peer_endpoint": info.get("peer_endpoint", ""),
                "peer_host": info.get("peer_host", ""),
            })

        return {
            "local_agents": local,
            "remote_agents": remote,
            "total": len(local) + len(remote),
        }

    async def _h_network_find(self, params):
        """
        Busca um agente específico ou uma tool em toda a rede (local + remoto).
        Params:
          - agent: nome do agente a procurar
          - tool:  nome da tool a procurar
        """
        agent_query = params.get("agent", "")
        tool_query = params.get("tool", "")

        found = []

        if agent_query:
            # Busca local
            for zname, zreg in self.zones.items():
                card = zreg.get_card(agent_query)
                if card:
                    found.append({
                        "agent": agent_query,
                        "role": card.agent_role,
                        "zone": zname,
                        "tools": [t.name for t in card.tools],
                        "source": "local",
                        "instance": self.instance_name,
                        "endpoint": card.endpoint,
                    })
            # Busca remota
            if agent_query in self._remote_agents:
                info = self._remote_agents[agent_query]
                found.append({
                    "agent": agent_query,
                    "role": info.get("role", ""),
                    "zone": info.get("zone", ""),
                    "tools": info.get("tools", []),
                    "source": "remote",
                    "instance": info.get("peer_instance", ""),
                    "peer_endpoint": info.get("peer_endpoint", ""),
                })

        if tool_query:
            # Local: qualquer agente que ofereça a tool
            for zname, zreg in self.zones.items():
                for card in zreg.get_all_cards():
                    if any(t.name == tool_query for t in card.tools):
                        found.append({
                            "agent": card.agent_name,
                            "role": card.agent_role,
                            "zone": zname,
                            "tool": tool_query,
                            "source": "local",
                            "instance": self.instance_name,
                            "endpoint": card.endpoint,
                        })
            # Remoto
            for name, info in self._remote_agents.items():
                if tool_query in info.get("tools", []):
                    found.append({
                        "agent": name,
                        "role": info.get("role", ""),
                        "zone": info.get("zone", ""),
                        "tool": tool_query,
                        "source": "remote",
                        "instance": info.get("peer_instance", ""),
                        "peer_endpoint": info.get("peer_endpoint", ""),
                    })

        return {
            "query": {"agent": agent_query, "tool": tool_query},
            "results": found,
            "count": len(found),
        }

    # ─── Matchmaking ────────────────────────────────────────────

    def _find_matches(self, zone_name: str, requester: str, interests: list[str]) -> list[dict]:
        """Encontra agentes locais cujas capabilities atendem os interesses."""
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
                    "source": "local",
                })

        matches.sort(key=lambda x: x["score"], reverse=True)
        return matches

    def _find_remote_matches(self, requester: str, interests: list[str], zone_name: str = None) -> list[dict]:
        """Encontra agentes remotos cujas capabilities/role atendem os interesses."""
        matches = []
        for name, info in self._remote_agents.items():
            if name == requester:
                continue

            searchable = " ".join([
                " ".join(info.get("tools", [])),
                info.get("role", ""),
                " ".join(info.get("interests", [])),
            ]).lower()

            score = sum(1 for i in interests if i.lower() in searchable)
            if score > 0:
                matching_tools = [
                    t for t in info.get("tools", [])
                    if any(i.lower() in t.lower() for i in interests)
                ]
                matches.append({
                    "agent": name,
                    "role": info.get("role", ""),
                    "score": score,
                    "matching_tools": matching_tools,
                    "peer_endpoint": info.get("peer_endpoint", ""),
                    "peer_instance": info.get("peer_instance", ""),
                    "zone": info.get("zone", ""),
                    "source": "remote",
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
    parser = argparse.ArgumentParser(
        description="GA2A Zone Server v0.5.0 (LAN discovery + consent + unicast/gossip)")
    parser.add_argument("--port", type=int, default=0, help="MCP port (0=auto)")
    parser.add_argument("--name", type=str, default=None, help="Instance name (default: ga2a-<ip>-<port>)")
    parser.add_argument("--broadcast-port", type=int, default=5060, help="UDP broadcast port for LAN discovery")
    parser.add_argument("--peer", action="append", default=None,
                        help="host:port of a known GA2A instance to seed from (unicast/VPN). Repeatable.")
    args = parser.parse_args()

    if args.port == 0:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("", 0))
        args.port = s.getsockname()[1]
        s.close()

    server = GA2AServer(
        my_port=args.port,
        instance_name=args.name,
        broadcast_port=args.broadcast_port,
        seed_peers=args.peer,
    )

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
