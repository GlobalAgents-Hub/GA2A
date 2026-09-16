# GA2A Network — Guia de Uso (v0.6.0)

A GA2A é um protocolo agent-to-agent descentralizado. Cada máquina roda uma instância persistente (`ga2a_server.py`), e as instâncias se descobrem sozinhas na LAN por varredura de sub-rede zero-config — você não precisa saber os IPs das outras. Os agentes se registram em zonas temáticas; a descoberta é aberta (todo mundo vê todo mundo), mas a interação exige consentimento: para invocar a tool de outro agente é preciso um grant com escopo e TTL. O transporte é MCP sobre JSON-RPC 2.0, via `POST /mcp`.

## 1. Ativar a rede (subir a instância)

Cada máquina sobe uma instância do servidor. Na mesma LAN, o auto-scan já resolve a descoberta.

- LAN zero-config (recomendado): `python3 ga2a_server.py --port 9420 --name minha-maquina`

O auto-scan varre a sub-rede local via TCP e faz o handshake com cada instância que responder — ele acha as outras sozinho. Na mesma LAN você NÃO precisa de `--peer`.

- VPN / sub-redes diferentes: `python3 ga2a_server.py --port 9420 --name minha-maquina --peer 10.0.0.5:9420`

Quando as instâncias estão em sub-redes distintas (uma VPN, por exemplo), a varredura não alcança a outra ponta. Aí você passa um seed conhecido com `--peer`; a partir dele a malha é aprendida por gossip.

### Opções do servidor

| Opção | Default | Para que serve |
| --- | --- | --- |
| `--port` | `0` (auto) | Porta do endpoint MCP. |
| `--name` | `ga2a-<ip>-<porta>` | Nome da instância na rede. |
| `--broadcast-port` | `5060` | Porta UDP usada no broadcast de descoberta LAN. |
| `--scan-prefix` | `24` | Tamanho do prefixo da sub-rede a varrer (24 = 254 hosts, 23 = ~510). |
| `--scan-ports` | só a porta do servidor | Portas extras a sondar, separadas por vírgula (ex.: `9440,9441`). |
| `--scan-interval` | `15` | Segundos entre cada re-varredura da sub-rede. |
| `--no-scan` | — | Desliga o auto-scan de sub-rede. |
| `--peer` | — | `host:porta` de uma instância conhecida (unicast/VPN). Repetível. |

## 2. Ativar o agente (registrar numa zona)

Com a instância no ar, você registra um agente numa zona declarando suas tools e interesses:

`python3 ga2a_client.py join --zone AI-Research --name Kiro --role ai-assistant --tool "generate_code:Gera codigo" --tool "code_review:Revisa codigo" --interest deploy --interest data-analysis`

- `--tool` e `--interest` são repetíveis. `--tool` aceita o formato `"nome:descricao"` (ou só `"nome"`).
- `--auto-approve`: sem essa flag, toda interação com o seu agente exige aprovação manual do dono; com ela, o agente aprova automaticamente todos os pedidos de acesso que chegarem.

Nota: use `--server host:porta` para falar com outra instância (o default é `localhost:9420`).

## 3. Ver a rede (descoberta aberta)

A descoberta é aberta: qualquer instância enxerga todos os agentes e peers conhecidos.

- `python3 ga2a_client.py explore` → mapa estilo torrent (recomendado como primeiro passo).

Exemplo de saída em árvore (instância + agentes):

```
🌐 GA2A Network Map (via http://localhost:9420)

📍 pc-alex  (192.168.1.10:9420)  [THIS NODE]
   └─ Kiro             [ai-assistant]  zone=AI-Research   tools: generate_code, code_review   interests: deploy, data-analysis

📍 pc-bruno  (192.168.1.11:9420)
   └─ Analyst          [data-scientist]  zone=Data-Science   tools: train_model

Total: 2 instance(s), 2 agent(s) (1 local, 1 remote)
```

- `python3 ga2a_client.py agents` → lista todos os agentes (locais + remotos).
- `python3 ga2a_client.py peers` → lista as instâncias GA2A descobertas.
- `python3 ga2a_client.py find --tool train_model` → busca uma tool na rede toda.
- `python3 ga2a_client.py find --agent Analyst` → busca um agente na rede toda.

## 4. Interagir com consentimento

O modelo é simples: a descoberta é aberta, mas a interação exige que o alvo autorize. Ao aprovar, o dono emite um grant com escopo (uma tool específica, uma zona, ou `*`) e um TTL — por padrão 1800s. Depois de expirar, o grant deixa de valer.

### Atalho recomendado: connect

`python3 ga2a_client.py connect --from Kiro --agent Analyst --interest "preciso treinar um modelo" --arg dataset=papers`

O `connect` faz tudo de uma vez: acha o agente, escolhe a tool sozinho se houver só uma, pede acesso e, se o pedido for auto-aprovado, invoca na hora. Se o alvo exigir aprovação manual, ele imprime o `request_id` pendente e os comandos exatos de `approve` e `invoke` para você seguir.

### Fluxo manual (controle fino)

Quando você quer controlar cada etapa:

- Pedir acesso: `python3 ga2a_client.py request --from Kiro --target Analyst --zone Data-Science --interest "preciso treinar" --tool train_model`
- (dono) Ver pendentes: `python3 ga2a_client.py pending --agent Analyst`
- (dono) Aprovar: `python3 ga2a_client.py approve --agent Analyst --request-id <id> --ttl 3600`
- Invocar: `python3 ga2a_client.py invoke --target Analyst --tool train_model --zone Data-Science --arg dataset=papers-2025`

O grant emitido fica salvo em `.ga2a_grants.json` e é reusado automaticamente nas próximas invocações (chave `target:tool`), então você não precisa repassar o token.

- flow (uma tacada só): `python3 ga2a_client.py flow --from Kiro --target Analyst --zone Data-Science --tool train_model --interest "treinar" --arg dataset=papers`

O `flow` pede acesso e, se for auto-aprovado, já invoca em seguida; se ficar pendente, ele imprime os comandos para concluir manualmente.

## 5. Gerenciar autorizações

- Listar grants emitidos por um agente: `python3 ga2a_client.py grants --agent Analyst`
- Revogar um grant já emitido: `python3 ga2a_client.py revoke --agent Analyst --grant-id <id>`
- Negar um pedido pendente: `python3 ga2a_client.py deny --agent Analyst --request-id <id>`

## 6. Exemplo completo entre 2 máquinas

Passo a passo com a Máquina A (`pc-alex`) e a Máquina B (`pc-bruno`) na mesma LAN.

1. Na Máquina A, suba a instância: `python3 ga2a_server.py --port 9420 --name pc-alex`
2. Na Máquina B, suba a instância: `python3 ga2a_server.py --port 9420 --name pc-bruno`
3. Em A, registre o Kiro: `python3 ga2a_client.py join --zone AI-Research --name Kiro --role ai-assistant --tool "generate_code:Gera codigo" --interest data-analysis`
4. Em B, registre o Analyst com uma tool e auto-aprovação: `python3 ga2a_client.py join --zone Data-Science --name Analyst --role data-scientist --tool "train_model:Treina um modelo" --auto-approve`
5. Após ~15s as instâncias se descobrem. Em A, veja o mapa: `python3 ga2a_client.py explore` mostra as duas máquinas e seus agentes.
6. Em A, conecte-se ao Analyst: `python3 ga2a_client.py connect --from Kiro --agent Analyst --interest "preciso treinar um modelo" --arg dataset=papers`

Nota: na mesma LAN não é preciso `--peer`. Via VPN, uma das máquinas sobe com `--peer <ip-da-outra>:9420` para semear a malha.

## 7. Observações e limites (honesto)

- O broadcast UDP pode ser bloqueado em redes corporativas (client isolation). Por isso o auto-scan por TCP é o método principal: ele funciona onde o broadcast falha.
- O auto-scan varre um `/24` por padrão. Para faixas maiores, use `--scan-prefix 23`.
- Um agente registrado só via `join` (sem um endpoint MCP executável próprio) é DESCOBERTO e pode ser AUTORIZADO, mas a invocação real da tool retorna "no live endpoint". Para executar tools de fato, o agente precisa expor o próprio endpoint MCP (campo `endpoint` no join).
- Grants expiram (TTL padrão 1800s) e podem ser revogados a qualquer momento com `revoke`.

## Referência de métodos MCP

O schema JSON-RPC 2.0 completo dos 21 métodos do protocolo está em [`mcp_methods.json`](mcp_methods.json) — parâmetros, retornos e exemplos de request/response de cada método.
