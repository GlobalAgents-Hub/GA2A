# GA2A — Conectando através de redes diferentes (VPN)

## Quando usar este guia

Use este guia **somente** quando as duas máquinas **não estão na mesma sub-rede LAN** e não conseguem se enxergar. Sintomas típicos: `ping` falha, `curl` dá timeout, ou aparece a mensagem "Network is unreachable".

Se as duas máquinas estão na **mesma LAN** (mesma sub-rede, tipicamente o mesmo Wi-Fi/switch), você **não precisa de VPN**: a auto-descoberta do GA2A (varredura TCP da sub-rede) já encontra as instâncias sozinha, sem nenhuma configuração de IP. Nesse caso, siga o [GA2A_NETWORK_GUIDE.md](GA2A_NETWORK_GUIDE.md).

### Como saber que você precisa deste guia

O cenário clássico é duas máquinas em segmentos diferentes de uma rede corporativa — por exemplo uma em `10.202.92.x` e outra em `10.200.66.x`. A rede corporativa muitas vezes **não roteia** o tráfego entre esses segmentos, então uma máquina simplesmente não alcança a outra.

Faça os dois testes abaixo, substituindo `<ip-do-outro>` pelo IP físico da outra máquina:

```bash
ping <ip-do-outro>
```

```bash
curl --connect-timeout 5 http://<ip-do-outro>:9420/
```

Se o `ping` falha ou o `curl` dá timeout / retorna "Network is unreachable", significa que a rede corporativa **isola os segmentos**. Nenhum ajuste de GA2A ou de firewall resolve isso — os pacotes nem chegam ao outro lado. A solução é criar uma rede virtual (overlay) por cima da rede física com uma **VPN em malha (mesh VPN)**.

A ideia da mesh VPN: cada máquina ganha um **IP virtual** que a alcança independentemente da rede física em que ela está. Depois é só apontar o GA2A para esse IP virtual com `--peer`.

## Opção A — Tailscale (recomendado)

O Tailscale é simples, gratuito para uso pessoal, e cada máquina recebe um IP na faixa `100.x.x.x` que funciona de qualquer rede.

### 1. Instalar

- **Linux:**

  ```bash
  curl -fsSL https://tailscale.com/install.sh | sh
  ```

  ```bash
  sudo tailscale up
  ```

- **Windows:** baixe o instalador em https://tailscale.com/download/windows, instale, abra o Tailscale e faça login.

**IMPORTANTE:** todas as máquinas precisam entrar na **MESMA conta / tailnet** do Tailscale. Se cada uma logar em uma conta diferente, elas não se enxergam.

### 2. Descobrir o IP Tailscale de cada máquina

Em cada máquina, rode:

```bash
tailscale ip -4
```

Isso retorna um endereço `100.x.x.x`. **Esse é o IP que atravessa as redes** — anote o de cada máquina.

### 3. Confirmar conectividade

Da máquina A, faça ping no IP Tailscale da máquina B (e vice-versa):

```bash
ping <ip-tailscale-do-outro>
```

Com o Tailscale ativo nas duas pontas, esse ping agora deve **funcionar** — mesmo que o ping no IP físico continuasse falhando.

### 4. Subir o GA2A sobre a VPN

Agora suba uma instância em cada máquina, apontando o `--peer` para o **IP Tailscale** da outra.

- **Máquina A (Linux):**

  ```bash
  python3 ga2a_server.py --port 9420 --name maquina-a --peer <ip-tailscale-de-B>:9420
  ```

- **Máquina B (Windows / PowerShell):**

  ```powershell
  python ga2a_server.py --port 9420 --name maquina-b --peer <ip-tailscale-de-A>:9420
  ```

Nota: apenas **um** dos lados precisa do `--peer` — o gossip espalha o resto da malha automaticamente. Mas apontar `--peer` nos dois lados é mais robusto e faz a descoberta convergir mais rápido.

### 5. Registrar agente e explorar

Registre um agente e depois desenhe o mapa da rede:

```bash
python3 ga2a_client.py join --zone AI-Research --name Kiro --role ai-assistant --tool "generate_code:Gera codigo" --auto-approve
```

```bash
python3 ga2a_client.py explore
```

O `explore` agora deve listar **2 instâncias** (a local e a remota, via VPN), com seus agentes.

## Opção B — ZeroTier (alternativa)

O ZeroTier segue o mesmo conceito de rede virtual em malha. Passos gerais:

1. Instale o ZeroTier a partir de https://www.zerotier.com/download/ (Linux, Windows, etc.).
2. Crie uma rede em https://my.zerotier.com/ e copie o **Network ID** (16 caracteres).
3. Em cada máquina, entre na mesma rede:

   ```bash
   sudo zerotier-cli join <network-id>
   ```

4. Autorize cada máquina no painel do ZeroTier (marque o membro como autorizado).
5. Descubra o IP virtual atribuído a cada máquina:

   ```bash
   sudo zerotier-cli listnetworks
   ```

6. Use esses IPs virtuais no `--peer`, exatamente como no passo 4 do Tailscale:

   ```bash
   python3 ga2a_server.py --port 9420 --name maquina-a --peer <ip-zerotier-de-B>:9420
   ```

## Windows + WSL (importante)

Se a máquina Windows roda o GA2A **dentro do WSL**, o IP do Tailscale precisa ser alcançável de dentro do WSL — e por padrão o WSL fica em uma rede virtual separada do Windows.

Caminho mais simples: **instale o Tailscale no WINDOWS e rode o GA2A no próprio Windows / PowerShell**. Assim tudo fica na mesma camada de rede e funciona sem malabarismo.

Rodar o GA2A dentro do WSL exige uma de duas coisas mais complexas: instalar o Tailscale **dentro do WSL** também, ou configurar port-forwarding do Windows para o WSL (`netsh interface portproxy`). Ambas dão mais trabalho e mais pontos de falha — prefira rodar direto no Windows.

## Diagnóstico rápido (checklist)

Rode os passos na ordem. Onde parar de funcionar é onde está o problema.

1. Os dois lados estão online na VPN?

   ```bash
   tailscale status
   ```

   Se a outra máquina **não aparece** ou aparece offline: ela não subiu o Tailscale ou logou em outra conta/tailnet. Corrija antes de seguir.

2. A VPN roteia entre elas?

   ```bash
   ping <ip-tailscale-do-outro>
   ```

   Se o ping **falha**: a malha VPN não está estabelecida. Reveja os passos 1 e 2 do Tailscale (login na mesma conta, `tailscale up`).

3. O servidor GA2A remoto está de pé e respondendo?

   ```bash
   curl --connect-timeout 5 http://<ip-tailscale-do-outro>:9420/
   ```

   Se **não retorna JSON**: o `ga2a_server.py` não está rodando na outra máquina, ou está em outra porta. Suba o servidor lá (passo 4) e confira a `--port`.

4. Consigo consultar a instância remota diretamente?

   ```bash
   python3 ga2a_client.py --server <ip-tailscale-do-outro>:9420 peers
   ```

   Se **falha aqui** mas o `curl` do passo 3 funcionou: verifique se está usando o endereço certo (IP Tailscale + porta do servidor).

5. O mapa mostra as duas instâncias?

   ```bash
   python3 ga2a_client.py explore
   ```

   Se ainda mostra **só 1 instância**: confirme que ao menos um dos servidores subiu com `--peer <ip-tailscale-do-outro>:9420`. Sem o `--peer`, entre segmentos diferentes as instâncias não se encontram (a varredura de sub-rede só cobre a LAN local).

## Por que isto é necessário (resumo honesto)

Redes corporativas costumam **segmentar** os clientes em faixas diferentes (por exemplo `/23` ou `/16` distintas) sem rotear tráfego entre os segmentos, por política de segurança. A auto-descoberta do GA2A varre apenas a **sua própria sub-rede** via TCP — então ela nunca vê uma máquina que está em outro segmento. Uma mesh VPN resolve isso sobrepondo uma **rede virtual plana** por cima da rede física: cada instância passa a se alcançar pelo IP `100.x.x.x`, e o GA2A federa por cima disso via `--peer` + gossip.

---

Para uso na **mesma LAN** (sem VPN), veja o [GA2A_NETWORK_GUIDE.md](GA2A_NETWORK_GUIDE.md).
