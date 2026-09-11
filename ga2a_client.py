#!/usr/bin/env python3
"""
GA2A CLI Client — a convenience wrapper around the GA2A server's MCP HTTP endpoint.

Talks to a running GA2A instance (see ga2a_server.py) by POSTing JSON-RPC 2.0
requests to http://<host>:<port>/mcp. Uses ONLY the Python standard library
(urllib.request, json, argparse) — no external dependencies.

Global option:
  --server URL    Base address of the GA2A instance. Accepts a full URL
                  (http://host:port) or just host:port (http:// is assumed).
                  The MCP endpoint is <base>/mcp. Default: http://localhost:9420

Examples:

  # Join a zone declaring tools + interests, auto-approving all access requests
  ga2a_client.py join --zone AI-Research --name TestBot --role ai \\
      --tool "gen:generates text" --interest "training data" --auto-approve

  # List every agent (local + remote) known to the instance
  ga2a_client.py agents

  # List discovered GA2A instances on the network
  ga2a_client.py peers

  # Find an agent or a tool across the network
  ga2a_client.py find --agent Analyst
  ga2a_client.py find --tool train_model

  # See capabilities available in a zone
  ga2a_client.py discover --zone Data-Science
  ga2a_client.py discover --zone Data-Science --tool train_model

  # Request access to another agent's tool (auto-approved grants are cached)
  ga2a_client.py request --from TestBot --target Analyst --zone Data-Science \\
      --interest "need training" --tool train_model

  # Owner-side approval flow
  ga2a_client.py pending --agent Analyst
  ga2a_client.py approve --agent Analyst --request-id <id> --ttl 3600
  ga2a_client.py deny --agent Analyst --request-id <id>
  ga2a_client.py grants --agent Analyst
  ga2a_client.py revoke --agent Analyst --grant-id <id>

  # Invoke a tool (grant loaded from cache if --grant omitted)
  ga2a_client.py invoke --target Analyst --tool train_model --zone Data-Science \\
      --arg dataset=papers --arg epochs=3

  # One-shot happy path: request access, then invoke if auto-approved
  ga2a_client.py flow --from TestBot --target Analyst --zone Data-Science \\
      --tool train_model --interest "need training" --arg dataset=papers

  # Send a message between agents
  ga2a_client.py message --from TestBot --to Analyst --zone Data-Science \\
      --message "hello"

The grant cache lives in ./.ga2a_grants.json, keyed by "target:tool", so that
`invoke` and `flow` can transparently reuse auto-approved grant tokens.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_SERVER = "http://localhost:9420"
GRANT_CACHE_FILE = ".ga2a_grants.json"


# ─── Server address / JSON-RPC transport ───────────────────────

def normalize_server(server: str) -> str:
    """Normalize a --server value into a base URL (no trailing /mcp, no slash)."""
    server = (server or "").strip()
    if "://" not in server:
        server = "http://" + server
    return server.rstrip("/")


def mcp_endpoint(server: str) -> str:
    """Return the full MCP endpoint URL for a (normalized) base server URL."""
    base = normalize_server(server)
    if base.endswith("/mcp"):
        return base
    return base + "/mcp"


_JSONRPC_ID = 0


def call(server: str, method: str, params: dict) -> dict:
    """
    Perform a JSON-RPC 2.0 POST to the server's /mcp endpoint and return the
    "result" dict. Raises RuntimeError with a clear message on HTTP/connection
    errors or JSON-RPC-level errors.
    """
    global _JSONRPC_ID
    _JSONRPC_ID += 1
    endpoint = mcp_endpoint(server)
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": _JSONRPC_ID,
        "method": method,
        "params": params or {},
    }).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise RuntimeError(
            f"HTTP {e.code} calling {method} at {endpoint}: {body or e.reason}"
        )
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach GA2A server at {endpoint}: {e.reason}. "
            f"Is the server running? (python3 ga2a_server.py --port ...)"
        )

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        raise RuntimeError(f"Invalid JSON response from {endpoint}: {raw[:200]!r}")

    if isinstance(data, dict) and "error" in data and data.get("result") is None:
        err = data["error"]
        if isinstance(err, dict):
            raise RuntimeError(
                f"JSON-RPC error from {method}: "
                f"{err.get('code', '?')} {err.get('message', err)}"
            )
        raise RuntimeError(f"JSON-RPC error from {method}: {err}")

    if not isinstance(data, dict) or "result" not in data:
        raise RuntimeError(f"Malformed JSON-RPC response from {method}: {data!r}")

    return data["result"]


# ─── Grant cache ───────────────────────────────────────────────

def _grant_cache_path() -> str:
    return os.path.join(os.getcwd(), GRANT_CACHE_FILE)


def _load_cache() -> dict:
    path = _grant_cache_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError, OSError):
        return {}


def save_grant(key: str, token: str) -> None:
    """Persist a grant token under `key` in ./.ga2a_grants.json."""
    cache = _load_cache()
    cache[key] = token
    try:
        with open(_grant_cache_path(), "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except OSError as e:
        print(f"⚠️  Could not save grant cache: {e}", file=sys.stderr)


def load_grant(key: str):
    """Return the cached grant token for `key`, or None."""
    return _load_cache().get(key)


# ─── Parsing helpers ───────────────────────────────────────────

def parse_tool(spec: str) -> dict:
    """Parse 'name:description' (or 'name') into {name, description}."""
    if ":" in spec:
        name, desc = spec.split(":", 1)
        return {"name": name.strip(), "description": desc.strip()}
    return {"name": spec.strip(), "description": ""}


def parse_arg(spec: str):
    """
    Parse 'key=value' into (key, value). The value is JSON-parsed when possible
    so numbers, booleans, null, and JSON objects/arrays work; otherwise it stays
    a string.
    """
    if "=" not in spec:
        raise ValueError(f"--arg must be key=value, got: {spec!r}")
    key, raw = spec.split("=", 1)
    key = key.strip()
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        value = raw
    return key, value


def build_arguments(arg_specs) -> dict:
    args = {}
    for spec in (arg_specs or []):
        key, value = parse_arg(spec)
        args[key] = value
    return args


def dump(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


# ─── Subcommand handlers ───────────────────────────────────────

def cmd_join(args):
    tools = [parse_tool(t) for t in (args.tool or [])]
    params = {
        "zone": args.zone,
        "agent_name": args.name,
        "agent_role": args.role,
        "tools": tools,
        "interests": args.interest or [],
    }
    if args.auto_approve:
        params["auto_approve"] = True

    result = call(args.server, "agent/join", params)
    if "error" in result:
        print(f"❌ {result['error']}")
        return 1

    print(f"✅ {result.get('agent')} joined zone '{result.get('zone')}' "
          f"(status: {result.get('status')})")
    peers = result.get("peers", [])
    print(f"   Peers in zone: {', '.join(peers) if peers else '(none)'}")
    matches = result.get("matches", [])
    if matches:
        print("   Matches for your interests:")
        for m in matches:
            tnames = ", ".join(t.get("name", "") for t in m.get("matching_tools", []))
            print(f"     • {m.get('agent')} ({m.get('role')}) score={m.get('score')} "
                  f"tools=[{tnames}]")
    else:
        print("   Matches: (none)")
    print("\n" + dump(result))
    return 0


def cmd_agents(args):
    result = call(args.server, "network/agents", {})
    local = result.get("local_agents", [])
    remote = result.get("remote_agents", [])

    print(f"Agents (total {result.get('total', len(local) + len(remote))})\n")
    print(f"Local agents ({len(local)}):")
    if not local:
        print("  (none)")
    for a in local:
        tools = ", ".join(a.get("tools", []))
        print(f"  • {a.get('agent'):<20} role={a.get('role', ''):<12} "
              f"zone={a.get('zone', ''):<14} tools=[{tools}]")

    print(f"\nRemote agents ({len(remote)}):")
    if not remote:
        print("  (none)")
    for a in remote:
        tools = ", ".join(a.get("tools", []))
        host = a.get("peer_host", "")
        inst = a.get("instance", "")
        print(f"  • {a.get('agent'):<20} role={a.get('role', ''):<12} "
              f"zone={a.get('zone', ''):<14} @ {inst} ({host}) tools=[{tools}]")
    return 0


def cmd_peers(args):
    result = call(args.server, "network/peers", {})
    me = result.get("self", {})
    peers = result.get("peers", [])
    print(f"This instance: {me.get('instance', '')} "
          f"@ {me.get('mcp_endpoint', '')}\n")
    print(f"Discovered instances ({result.get('peer_count', len(peers))}):")
    if not peers:
        print("  (none discovered yet)")
    for p in peers:
        zones = ", ".join(p.get("zones", []))
        print(f"  • {p.get('instance'):<24} {p.get('mcp_endpoint', ''):<30} "
              f"agents={p.get('agent_count', 0)} zones=[{zones}]")
    return 0


def cmd_find(args):
    if not args.agent and not args.tool:
        print("❌ find requires --agent or --tool", file=sys.stderr)
        return 2
    params = {}
    if args.agent:
        params["agent"] = args.agent
    if args.tool:
        params["tool"] = args.tool

    result = call(args.server, "network/find", params)
    results = result.get("results", [])
    print(f"Found {result.get('count', len(results))} match(es):")
    if not results:
        print("  (none)")
    for r in results:
        src = r.get("source", "")
        loc = r.get("instance", "") or r.get("peer_endpoint", "")
        tools = r.get("tool") or ", ".join(r.get("tools", []))
        print(f"  • {r.get('agent'):<20} role={r.get('role', ''):<12} "
              f"zone={r.get('zone', ''):<14} [{src}] {loc} tools=[{tools}]")
    return 0


def cmd_discover(args):
    params = {"zone": args.zone}
    if args.tool:
        params["tool"] = args.tool
    result = call(args.server, "agent/discover", params)
    if "error" in result:
        print(f"❌ {result['error']}")
        return 1

    local = result.get("capabilities", [])
    remote = result.get("remote_capabilities", [])
    print(f"Zone '{result.get('zone')}' capabilities\n")
    print(f"Local ({len(local)}):")
    if not local:
        print("  (none)")
    for c in local:
        tools = ", ".join(t.get("name", "") for t in c.get("tools", []))
        print(f"  • {c.get('agent'):<20} role={c.get('role', ''):<12} tools=[{tools}]")

    print(f"\nRemote ({len(remote)}):")
    if not remote:
        print("  (none)")
    for c in remote:
        tools = ", ".join(t.get("name", "") for t in c.get("tools", []))
        print(f"  • {c.get('agent'):<20} role={c.get('role', ''):<12} "
              f"@ {c.get('peer_instance', '')} tools=[{tools}]")
    return 0


def cmd_request(args):
    params = {
        "from_agent": args.from_agent,
        "target": args.target,
        "zone": args.zone,
        "interest": args.interest,
        "tool": args.tool,
    }
    result = call(args.server, "access/request", params)
    if "error" in result:
        print(f"❌ {result['error']}")
        return 1

    status = result.get("status")
    print(f"access/request: {args.from_agent} → {args.target} "
          f"(tool: {args.tool or '*'}) = {status}")

    if status == "approved" and result.get("grant"):
        token = result["grant"]
        key = f"{args.target}:{args.tool}"
        save_grant(key, token)
        print(f"🔑 Auto-approved. Grant saved under '{key}'.")
        print(f"   Grant token: {token}")
    elif status == "pending":
        rid = result.get("request_id")
        print(f"⏳ Pending approval. request_id = {rid}")
        print(f"   Ask the owner to run: "
              f"ga2a_client.py approve --agent {args.target} --request-id {rid}")
    print("\n" + dump(result))
    return 0


def cmd_pending(args):
    result = call(args.server, "access/pending", {"agent_name": args.agent})
    if "error" in result:
        print(f"❌ {result['error']}")
        return 1
    pending = result.get("pending", [])
    print(f"Pending requests for '{args.agent}' ({result.get('count', len(pending))}):")
    if not pending:
        print("  (none)")
    for p in pending:
        print(f"  • request_id={p.get('request_id')} "
              f"from={p.get('from_agent', p.get('from', ''))} "
              f"tool={p.get('tool', '*')} interest={p.get('interest', '')}")
    return 0


def cmd_approve(args):
    params = {"agent_name": args.agent, "request_id": args.request_id}
    if args.scope is not None:
        params["scope"] = args.scope
    if args.ttl is not None:
        params["ttl"] = args.ttl

    result = call(args.server, "access/approve", params)
    if "error" in result:
        print(f"❌ {result['error']}")
        return 1

    token = result.get("grant_token")
    print(f"✅ Approved request {args.request_id} for '{args.agent}' "
          f"(status: {result.get('status')})")
    if token:
        key = f"{args.agent}:{args.request_id}"
        save_grant(key, token)
        print(f"🔑 Grant token: {token}")
        print(f"   Cached under '{key}'.")
    return 0


def cmd_deny(args):
    result = call(args.server, "access/deny",
                  {"agent_name": args.agent, "request_id": args.request_id})
    if "error" in result:
        print(f"❌ {result['error']}")
        return 1
    print(f"access/deny: {result.get('status')} (request_id={result.get('request_id')})")
    return 0


def cmd_revoke(args):
    result = call(args.server, "access/revoke",
                  {"agent_name": args.agent, "grant_id": args.grant_id})
    if "error" in result:
        print(f"❌ {result['error']}")
        return 1
    print(f"access/revoke: {result.get('status')} (grant_id={result.get('grant_id')})")
    return 0


def cmd_grants(args):
    result = call(args.server, "access/grants", {"agent_name": args.agent})
    if "error" in result:
        print(f"❌ {result['error']}")
        return 1
    grants = result.get("grants", [])
    print(f"Grants issued by '{args.agent}' ({result.get('count', len(grants))}):")
    if not grants:
        print("  (none)")
    for g in grants:
        print(f"  • {dump(g)}")
    return 0


def cmd_invoke(args):
    grant = args.grant
    key = f"{args.target}:{args.tool}"
    if not grant:
        grant = load_grant(key)
        if grant:
            print(f"(using cached grant for '{key}')")

    params = {
        "target": args.target,
        "tool": args.tool,
        "arguments": build_arguments(args.arg),
        "zone": args.zone,
    }
    if grant:
        params["grant"] = grant

    result = call(args.server, "agent/invoke", params)
    return _print_invoke_result(result, args.target, args.tool)


def _print_invoke_result(result: dict, target: str, tool: str) -> int:
    if result.get("error"):
        err = result.get("error")
        if err == "authorization_required":
            print(f"🔒 authorization_required for {target}.{tool}")
            print(f"   Run: ga2a_client.py request --from <you> --target {target} "
                  f"--zone <zone> --tool {tool}")
            print("   Then retry invoke (an auto-approved grant is cached automatically).")
        else:
            print(f"❌ invoke failed: {err}")
        print("\n" + dump(result))
        return 1

    status = result.get("status")
    print(f"invoke {target}.{tool} → status: {status}")
    if "result" in result:
        print(dump(result.get("result")))
    else:
        print(dump(result))
    return 0


def cmd_flow(args):
    # Step (a): request access
    req_params = {
        "from_agent": args.from_agent,
        "target": args.target,
        "zone": args.zone,
        "interest": args.interest,
        "tool": args.tool,
    }
    print(f"→ requesting access: {args.from_agent} → {args.target} (tool: {args.tool})")
    req = call(args.server, "access/request", req_params)
    if "error" in req:
        print(f"❌ access/request failed: {req['error']}")
        return 1

    status = req.get("status")
    print(f"  access/request status: {status}")

    if status == "approved" and req.get("grant"):
        # Step (b): invoke immediately with the fresh grant
        grant = req["grant"]
        save_grant(f"{args.target}:{args.tool}", grant)
        print("  grant obtained, invoking...")
        inv_params = {
            "target": args.target,
            "tool": args.tool,
            "arguments": build_arguments(args.arg),
            "zone": args.zone,
            "grant": grant,
        }
        inv = call(args.server, "agent/invoke", inv_params)
        return _print_invoke_result(inv, args.target, args.tool)

    # Step (c): pending — needs manual approval
    rid = req.get("request_id")
    print(f"⏳ Request is pending (request_id={rid}); manual approval needed.")
    print(f"   Owner runs: ga2a_client.py approve --agent {args.target} --request-id {rid}")
    print(f"   Then run:   ga2a_client.py invoke --target {args.target} "
          f"--tool {args.tool} --zone {args.zone} "
          + " ".join(f"--arg {a}" for a in (args.arg or [])))
    if args.approver_agent:
        print(f"   (note: --approver-agent {args.approver_agent} given, but manual "
              f"approval must be issued by the target's owner)")
    print("\n" + dump(req))
    return 0


def cmd_message(args):
    params = {
        "from": args.from_agent,
        "to": args.to,
        "zone": args.zone,
        "message": args.message,
    }
    result = call(args.server, "agent/message", params)
    if "error" in result:
        print(f"❌ {result['error']}")
        return 1
    print(f"message: {result.get('status')} "
          f"({result.get('from')} → {result.get('to')} in '{result.get('zone')}')")
    return 0


# ─── Argument parser ───────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ga2a_client.py",
        description="CLI client for a GA2A server (MCP JSON-RPC over HTTP).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--server", default=DEFAULT_SERVER,
        help=f"GA2A server URL or host:port (default: {DEFAULT_SERVER})",
    )

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    # join
    p = sub.add_parser("join", help="Join a zone with tools + interests")
    p.add_argument("--zone", default="General")
    p.add_argument("--name", required=True)
    p.add_argument("--role", default="agent")
    p.add_argument("--tool", action="append",
                   help='Repeatable. Format "name:description" or "name"')
    p.add_argument("--interest", action="append", help="Repeatable")
    p.add_argument("--auto-approve", action="store_true",
                   help="Auto-approve all incoming access requests")
    p.set_defaults(func=cmd_join)

    # agents
    p = sub.add_parser("agents", help="List all agents (local + remote)")
    p.set_defaults(func=cmd_agents)

    # peers
    p = sub.add_parser("peers", help="List discovered GA2A instances")
    p.set_defaults(func=cmd_peers)

    # find
    p = sub.add_parser("find", help="Find an agent or tool across the network")
    p.add_argument("--agent")
    p.add_argument("--tool")
    p.set_defaults(func=cmd_find)

    # discover
    p = sub.add_parser("discover", help="Show capabilities in a zone")
    p.add_argument("--zone", required=True)
    p.add_argument("--tool")
    p.set_defaults(func=cmd_discover)

    # request
    p = sub.add_parser("request", help="Request access to an agent/tool")
    p.add_argument("--from", dest="from_agent", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--zone", required=True)
    p.add_argument("--interest", default="")
    p.add_argument("--tool", default="")
    p.set_defaults(func=cmd_request)

    # pending
    p = sub.add_parser("pending", help="List pending access requests for an agent")
    p.add_argument("--agent", required=True)
    p.set_defaults(func=cmd_pending)

    # approve
    p = sub.add_parser("approve", help="Approve a pending access request")
    p.add_argument("--agent", required=True)
    p.add_argument("--request-id", dest="request_id", required=True)
    p.add_argument("--scope")
    p.add_argument("--ttl", type=float)
    p.set_defaults(func=cmd_approve)

    # deny
    p = sub.add_parser("deny", help="Deny a pending access request")
    p.add_argument("--agent", required=True)
    p.add_argument("--request-id", dest="request_id", required=True)
    p.set_defaults(func=cmd_deny)

    # revoke
    p = sub.add_parser("revoke", help="Revoke an issued grant")
    p.add_argument("--agent", required=True)
    p.add_argument("--grant-id", dest="grant_id", required=True)
    p.set_defaults(func=cmd_revoke)

    # grants
    p = sub.add_parser("grants", help="List grants issued by an agent")
    p.add_argument("--agent", required=True)
    p.set_defaults(func=cmd_grants)

    # invoke
    p = sub.add_parser("invoke", help="Invoke a tool on an agent")
    p.add_argument("--target", required=True)
    p.add_argument("--tool", required=True)
    p.add_argument("--zone", required=True)
    p.add_argument("--arg", action="append",
                   help='Repeatable. Format "key=value" (value JSON-parsed if possible)')
    p.add_argument("--grant",
                   help="Grant token. If omitted, loaded from .ga2a_grants.json (target:tool)")
    p.set_defaults(func=cmd_invoke)

    # flow
    p = sub.add_parser("flow", help="One-shot: request access then invoke (happy path)")
    p.add_argument("--from", dest="from_agent", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--zone", required=True)
    p.add_argument("--tool", required=True)
    p.add_argument("--interest", default="")
    p.add_argument("--arg", action="append",
                   help='Repeatable. Format "key=value"')
    p.add_argument("--approver-agent",
                   help="Optional; only auto-approve targets complete the happy path")
    p.set_defaults(func=cmd_flow)

    # message
    p = sub.add_parser("message", help="Send a message between agents")
    p.add_argument("--from", dest="from_agent", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--zone", required=True)
    p.add_argument("--message", required=True)
    p.set_defaults(func=cmd_message)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        rc = args.func(args)
    except RuntimeError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        sys.exit(130)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
