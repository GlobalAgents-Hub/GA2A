# Changelog

Todas as mudanças relevantes do GA2A (protocolo agent-to-agent com MCP) nesta branch são registradas aqui.
O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). As versões abaixo são as do servidor (`ga2a_server.py`).

## [Unreleased]

### Documentação
- Adicionado `CHANGELOG.md`.
- Adicionado `docs/external-tools-report.md`, relatório de viabilidade e uso de ferramentas externas (bibliotecas, VPNs mesh e ferramentas do ambiente).

## [0.6.0] - 2026-09-16

### Adicionado
- Auto-descoberta zero-config por varredura TCP da sub-rede. Na mesma LAN não é mais necessário `--peer`. Funciona onde o broadcast UDP é bloqueado (isolamento de clientes em redes corporativas).
- Opções do servidor: `--scan`/`--no-scan`, `--scan-prefix` (padrão 24), `--scan-ports`, `--scan-interval` (padrão 15s).
- Funções `get_local_ipv4_cidr`, `iter_subnet_hosts` e `tcp_probe` em `src/a2a/discovery.py`.
- Comando `explore` no cliente: mapa da rede agrupado por instância.
- Comando `connect` no cliente: acha o agente, pede acesso e invoca em um único passo.
- `GA2A_NETWORK_GUIDE.md`: guia de uso em pt-BR.
- `GA2A_VPN_GUIDE.md`: guia para conectar máquinas em redes diferentes via VPN mesh (Tailscale e ZeroTier).
- Seção "Running the Network (v0.6.0)" no `README.md`, com links para os guias.

### Corrigido
- Agentes que entram depois da descoberta inicial de uma instância não se propagavam. A varredura e o loop de gossip agora refazem o handshake com peers já conhecidos, e o intervalo padrão de varredura passou de 30s para 15s.

## [0.5.0] - 2026-09-03

### Adicionado
- Autorização por consentimento (`src/a2a/mcp/grants.py`): grants assinados com HMAC-SHA256, com escopo (tool, `zone:<nome>` ou `*`), TTL (padrão 1800s), políticas de auto-aprovação e revogação.
- Métodos `access/request`, `access/pending`, `access/approve`, `access/deny`, `access/revoke` e `access/grants`.
- `agent/invoke` passa a exigir grant válido para agentes locais que têm gerenciador de grants.
- `agent/join` aceita `auto_approve` (booleano ou lista de políticas).
- Descoberta unicast com `--peer host:porta` (repetível) e gossip via `network/hello`, para VPN e redes diferentes.
- `NetworkDiscovery.register_unicast_peer()`.
- 55 testes novos para `grants.py` (total de 269).

### Corrigido
- (2026-09-11) `access/request` decidia se um agente era remoto pela existência de um gerenciador de grants, o que impedia o proxy para a instância dona. Agora usa o registro de agentes locais.
- (2026-09-11) `agent/invoke` remoto aninhava erros como `authorization_required` dentro de `result`. Agora o erro sobe para o nível superior.

### Adicionado (2026-09-11)
- `ga2a_client.py`: cliente CLI em stdlib pura, com `join`, `agents`, `peers`, `find`, `discover`, `request`, `pending`, `approve`, `deny`, `revoke`, `grants`, `invoke`, `flow` e `message`. Grants aprovados ficam em cache em `.ga2a_grants.json`.
- `mcp_methods.json`: referência JSON-RPC 2.0 dos 21 métodos, com parâmetros, retornos e exemplos.

## [0.4.0] - 2026-09-03

### Adicionado
- Federação na LAN: `NetworkDiscovery` reescrito com heartbeat por broadcast UDP contendo identidade, zonas, agentes e capabilities, e callbacks de peer descoberto, atualizado e perdido.
- Agentes de outras máquinas aparecem como agentes remotos e são invocáveis por proxy MCP.
- Métodos `network/peers`, `network/agents` e `network/find`.
- `agent/discover`, `agent/match`, `agent/request` e `zones/list` passam a incluir agentes e zonas remotos.
- `get_local_ip()` para detectar o endereço da LAN.

## [0.3.0] - 2026-09-01

### Adicionado
- Modelo de contexto distribuído: agentes declaram `interests` (o que buscam) além de capabilities.
- Matchmaking por interesses, com `agent/match`.
- `agent/request` (solicitar contexto de outro agente) e `agent/context` (contexto disponível para um agente na zona).

## [0.2.0] - 2026-08-20

### Adicionado
- `ga2a_server.py`: instância persistente com zonas padrão (General, AI-Research, DevOps, Data-Science) e endpoint MCP em `POST /mcp`.
- Métodos `initialize`, `zones/list`, `zones/create`, `agent/join`, `agent/leave`, `agent/discover`, `agent/invoke` e `agent/message`.

## [0.1.0] - 2026-08-20

### Adicionado
- Spec da feature MCP: requisitos, design (com 11 propriedades de corretude) e plano de tasks.
- Pacote `src/a2a/mcp/`: `loop_runner`, `capabilities`, `auth` (tokens HMAC por zona), `errors`, `registry` (ZoneRegistry), `server` (MCPServerHost), `client` (MCPClientPool com validação de schema e retry) e `bridge` (TransportBridge).
- `Peer` e `AgentBuilder` estendidos com MCP opcional, mantendo compatibilidade com a comunicação TCP existente.
- Exportação das classes MCP em `src/a2a/__init__.py`.
- Dependências `mcp`, `uvicorn` e `httpx`, e dependências de desenvolvimento `hypothesis` e `pytest-asyncio` em `pyproject.toml`.
- 214 testes em `tests/test_mcp/`.

### Observações
- A dependência `mcp` está declarada em `pyproject.toml`, mas o servidor e o cliente usam JSON-RPC próprio sobre `uvicorn` e `httpx`; o pacote `mcp` não é importado.
- `hypothesis` está declarada, mas os testes de propriedade do design não foram escritos.
