# GA2A: viabilidade e uso de ferramentas externas

Relatório de decisões e achados sobre as ferramentas externas usadas e avaliadas no GA2A (bibliotecas, VPNs mesh e ferramentas do ambiente). Os preços e limites de terceiros mudam com frequência: confirme nas páginas oficiais antes de decidir.

## Resumo

- A camada MCP do GA2A é viável e roda só com bibliotecas leves (`uvicorn`, `httpx`). Não depende do SDK oficial do MCP.
- O gargalo real do projeto é a rede, não o código. Entre segmentos isolados de uma rede corporativa é preciso uma VPN mesh.
- As VPNs avaliadas têm planos gratuitos com restrições de uso comercial. Entre máquinas reais só o Tailscale foi exercitado, e apenas no caminho cliente-servidor.

## 1. Bibliotecas usadas no código

| Ferramenta | Uso | Situação |
|---|---|---|
| `uvicorn` | Servidor HTTP/ASGI do MCP e do `ga2a_server.py` | Em uso, funciona |
| `httpx` | Cliente HTTP (client pool, bridge, proxy entre instâncias) | Em uso, funciona |
| `pytest`, `pytest-asyncio` | Testes (269) | Em uso. Um `.venv` sem `pytest-asyncio` faz 33 testes assíncronos falharem; com o Python do sistema passam os 269 |
| `hypothesis` | Testes de propriedade | Declarada, mas sem uso. As 11 propriedades do design não viraram testes |
| `mcp>=2.0.0` (SDK oficial) | Prevista no design | Declarada no `pyproject.toml` e nunca importada |

### Divergência entre design e implementação

O design escolheu o SDK oficial do MCP, mas a implementação ficou como servidor JSON-RPC próprio, escrito direto sobre `uvicorn` e `httpx`.

- Vantagem: menos dependência, controle total e nenhum problema de compatibilidade.
- Custo: o servidor implementa só um subconjunto do MCP (`initialize`, `tools`, `resources`, `prompts`). Não há negociação completa de capabilities nem transportes padrão como Streamable HTTP.
- Pendência: escolher entre remover a dependência `mcp` e atualizar o `design.md`, ou migrar para o SDK oficial.

## 2. Infraestrutura de rede e VPN

### Evolução

1. A descoberta por broadcast UDP funcionou nos testes locais, mas não é confiável em redes corporativas.
2. A varredura TCP da sub-rede contorna o isolamento de clientes dentro de um mesmo segmento.
3. O teste entre duas máquinas em segmentos diferentes (`10.202.92.x` e `10.200.66.x`) deu `Network is unreachable`. A rede corporativa não roteia entre esses segmentos.
4. Isso exige uma VPN mesh. Com o Tailscale, um cliente em outra máquina alcançou o servidor pelo endereço da VPN e entrou em uma zona compartilhada (veja "Resultado observado com Tailscale").
5. Pelo trial e pelas restrições de uso corporativo, passaram a ser avaliadas alternativas open source.

### Resultado observado com Tailscale

Um cliente GA2A em uma máquina diferente da do servidor executou `join` contra o servidor pelo endereço Tailscale (`100.x.x.x:9420`) na zona `AI-Research` com o agente `agente-victor`. O servidor respondeu `status: joined`, listou `Arthur-Agent` (registrado por outra pessoa) como peer da zona e devolveu `matches: []`, pois nenhum interesse declarado coincidiu com tools ou papel de outro agente.

O que isso comprova:

- Uma VPN mesh (Tailscale) permite que máquinas em segmentos que não roteiam entre si alcancem um servidor GA2A.
- O `join` de um cliente em um servidor remoto funciona entre máquinas.
- Duas pessoas podem compartilhar uma zona em um mesmo servidor.

O que ainda não foi comprovado:

- Federação servidor a servidor entre máquinas físicas (`--peer` mais gossip).
- O `explore` mostrando duas instâncias.
- `access/request`, `grant` e `invoke` entre máquinas.
- Execução de tools: o `invoke` ainda retorna `no live endpoint` para agentes sem endpoint próprio.
- Outras VPNs: só o Tailscale foi exercitado. ZeroTier, NetBird, Headscale e Nebula não foram.

### Comparação

| Opção | Plano gratuito | Restrição | Self-host |
|---|---|---|---|
| Tailscale | Personal: 6 usuários, dispositivos ilimitados ([doc](https://tailscale.com/kb/1154/free-plans/)) | Só uso não comercial | Não (cliente aberto, coordenação proprietária) |
| ZeroTier | Personal: 10 dispositivos, 1 rede, 1 admin ([fonte](https://freetier.co/directory/products/zerotier)) | Só uso não comercial | Não, no plano gratuito |
| NetBird | Cloud: até 5 usuários e 100 máquinas ([docs](https://docs.netbird.io/how-to/plans-and-billing)) | Os limites vêm do plano | Sim, código aberto |
| Headscale | Não verificado neste relatório (licença BSD, pelo conhecimento prévio) | Exige servidor próprio acessível pelas duas pontas | Sim |
| Nebula | Não verificado neste relatório | Configuração manual de certificados e nó lighthouse | Sim |

### Correções a informações anteriores

- ZeroTier: foi dito 25 dispositivos gratuitos, número de material antigo. As fontes atuais indicam 10 dispositivos.
- NetBird: foi dito "5 peers". O limite do plano é 5 usuários e 100 máquinas.
- Tailscale: o trial de 7 dias não é a única opção. O plano Personal é gratuito, mas só para uso não comercial. Não foi verificado por que a conta de domínio corporativo caiu no trial.
- Licenças: os planos gratuitos do Tailscale e do ZeroTier são não comerciais. Confirme se o uso entre colegas de uma empresa se enquadra antes de adotar qualquer um.
- NetBird self-hosted: há menção a uma licença comercial paga para self-hosting em empresas (Commercial Starter, 2.000 €/ano para até 50 usuários). Confirme os termos antes de usar em ambiente corporativo.

## 3. Ferramentas do ambiente

| Ferramenta | Resultado |
|---|---|
| Git/GitHub (HTTPS) | O `push` travava por falta de credenciais. Funcionou após configurar um token com escopo `repo`; `workflow` não era necessário |
| Kiro IDE | Atualizada via `dpkg`; exige reiniciar o IDE para valer |
| Kiro CLI | Instalada e funcionando |
| Kiro Crew | Roda pelo AppImage em `~/Applications`. O sandbox AppArmor não foi instalado porque o diretório home tinha permissão `775` e o Crew recusa anexar o perfil nesse caso. O modo serviço systemd falhou com `203/EXEC` e foi removido. Node v18 está abaixo do 22 recomendado |

## 4. Referências que não foram lidas

- O site `globalagents2a.lovable.app`, o vídeo do YouTube e o artigo de Ricardo Martins não foram lidos: a extração de conteúdo falhou.
- O diagrama de arquitetura não foi visualizado como imagem; foi usada a descrição do README.
- Apenas a [especificação oficial do MCP](https://modelcontextprotocol.io/specification/2025-11-25) foi lida.
- O design reflete a descrição do projeto e o README, não o conteúdo dessas fontes. Se elas trazem requisitos específicos, vale conferir.

## 5. Viabilidade e recomendações

| Tema | Veredito |
|---|---|
| Protocolo e descoberta na mesma LAN | Viável, validado apenas com instâncias na mesma máquina |
| Troca de contexto entre redes isoladas | Validado em parte: cliente em uma máquina entrou em servidor remoto pelo Tailscale. A federação servidor a servidor ainda não foi testada |
| Uso corporativo das VPNs gratuitas | Incerto, as licenças são não comerciais |
| Execução real das tools | Não resolvido: falta um endpoint por agente ou hospedar as tools no servidor |

Ordem sugerida:

1. Decidir a VPN. Para uso corporativo, NetBird ou Headscale (ambos self-host) evitam a restrição de uso não comercial, mas exigem um servidor acessível pelas duas pontas.
2. Resolver a divergência da dependência `mcp` (remover ou adotar).
3. Testar a federação servidor a servidor (`--peer` e gossip, `explore`, `access/request` e `invoke`) entre duas máquinas físicas pela VPN.
4. Corrigir o ambiente de testes do `.venv`.
5. Atualizar o `GA2A_VPN_GUIDE.md` com a alternativa escolhida.
