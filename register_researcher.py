"""
Script para registrar o agente Researcher-Sub no GA2A Zone Server
e interagir com o agente Kiro na zona AI-Research.
"""
import json
import urllib.request

BASE_URL = "http://localhost:9420"
MCP_URL = f"{BASE_URL}/mcp"

request_id = 0

def mcp_call(method: str, params: dict = None) -> dict:
    """Faz uma chamada JSON-RPC 2.0 ao endpoint MCP."""
    global request_id
    request_id += 1
    
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {}
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        MCP_URL,
        data=data,
        headers={"Content-Type": "application/json"}
    )
    
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_status() -> dict:
    """GET /status para ver o estado do servidor."""
    req = urllib.request.Request(f"{BASE_URL}/status")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print("=" * 60)
    print("  GA2A - Registrando Researcher-Sub na zona AI-Research")
    print("=" * 60)
    
    # Step 1: Initialize - verificar que o servidor está rodando
    print("\n" + "─" * 60)
    print("STEP 1: Chamando 'initialize' para verificar o servidor")
    print("─" * 60)
    result = mcp_call("initialize")
    print(json.dumps(result, indent=2))
    
    # Step 2: Registrar na zona AI-Research
    print("\n" + "─" * 60)
    print("STEP 2: Registrando Researcher-Sub na zona AI-Research")
    print("─" * 60)
    result = mcp_call("agent/register", {
        "zone": "AI-Research",
        "agent_name": "Researcher-Sub",
        "agent_role": "research-assistant",
        "tools": [
            {"name": "search_papers", "description": "Busca papers acadêmicos"},
            {"name": "summarize_paper", "description": "Resume um paper em 3 frases"}
        ]
    })
    print(json.dumps(result, indent=2))
    
    # Step 3: Descobrir agentes na zona AI-Research
    print("\n" + "─" * 60)
    print("STEP 3: Chamando 'agent/discover' na zona AI-Research")
    print("─" * 60)
    result = mcp_call("agent/discover", {
        "zone": "AI-Research"
    })
    print(json.dumps(result, indent=2))
    
    # Step 4: Enviar mensagem para Kiro
    print("\n" + "─" * 60)
    print("STEP 4: Enviando mensagem de Researcher-Sub para Kiro")
    print("─" * 60)
    result = mcp_call("agent/message", {
        "from": "Researcher-Sub",
        "to": "Kiro",
        "zone": "AI-Research",
        "message": "Oi Kiro! Acabei de entrar na zona AI-Research. Posso te ajudar com pesquisa acadêmica!"
    })
    print(json.dumps(result, indent=2))
    
    # Step 5: Verificar status geral
    print("\n" + "─" * 60)
    print("STEP 5: GET /status - verificando ambos agentes visíveis")
    print("─" * 60)
    status = get_status()
    print(json.dumps(status, indent=2))
    
    print("\n" + "=" * 60)
    print("  Todas as etapas concluídas com sucesso!")
    print("=" * 60)


if __name__ == "__main__":
    main()
