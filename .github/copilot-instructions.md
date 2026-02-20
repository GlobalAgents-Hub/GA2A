# Copilot Instructions for A2A-Protocol

- **Scope**: Repository mixes a legacy script-style module in [a2a.py](a2a.py) and an SDK-style package under [src/a2a](src/a2a); be explicit about which layer you are using when importing or changing behavior.
- **Core orchestration**: `A2A` in [src/a2a/core.py](src/a2a/core.py) coordinates zones and persistence via JSON `data.json`. `join_zone` auto-creates a zone and is idempotent; `record_interaction` appends to the same file.
- **Zones**: `ZoneManager` in [src/a2a/zones.py](src/a2a/zones.py) persists zones with `entities` and optional `metadata`. Zone data lives in the working directory; prefer tmp dirs in tests and avoid writing to the repo root unless intentional.
- **Peers / agents**: `Peer` and `AgentBuilder` in [src/a2a/agents.py](src/a2a/agents.py) wrap TCP sockets (default port 5050). `start()` spins a listener thread; `send()` JSON-encodes to a target port. Register handlers with `@peer.on("message_received")` and remember to call `stop()` to free the port.
- **Event utility**: Generic `EventHandler` in [src/a2a/events.py](src/a2a/events.py) offers `on/emit` for custom event buses.
- **Discovery**: `NetworkDiscovery` in [src/a2a/discovery.py](src/a2a/discovery.py) broadcasts/receives UDP on port 5060, caching peer/zone names only. It runs background listener + broadcaster threads; call `stop()` when done.
- **Data/json layout**: `data.json` stores `entities`, `zones`, `interactions`, `logs`; tests rely on this shape.
- **Import collisions**: When running from the repo root, `import a2a` resolves to [a2a.py](a2a.py) (functions `spawn`, `create_zone`, `interact`, `load_data`). After `pip install -e .`, `import a2a` resolves to the SDK package exporting `A2A`, `Peer`, `AgentBuilder`, `ZoneManager`, `EventHandler`, `NetworkDiscovery`. Choose the right API; tests currently cover both.
- **Examples**: [Examples/research_agent.py](Examples/research_agent.py) shows the SDK pattern (AgentBuilder + discovery + zone join). [Examples/agent_simulator.py](Examples/agent_simulator.py) loops `send_interaction` calls against the TCP endpoint for simple traffic simulation.
- **HTTP/CLI**: [cli/ga2a_cli.py](cli/ga2a_cli.py) exposes a FastAPI app backed by SQLite `ga2a-zones.db`, importing the legacy `ga2a.py`; useful as a thin HTTP facade, not part of the SDK package.
- **Testing**: `pytest` config in [pyproject.toml](pyproject.toml) runs `tests/test_*.py`. Autouse fixture in [tests/conftest.py](tests/conftest.py) chdirs each test into a temp dir, so file I/O (including `data.json`) stays isolated.
- **Behavioral tests**: [tests/test_peer_events.py](tests/test_peer_events.py) asserts loopback messaging via `Peer` and the `message_received` event. [tests/test_zones_duplicate.py](tests/test_zones_duplicate.py) enforces `join_zone` idempotency. [tests/test_a2a.py](tests/test_a2a.py) covers legacy helpers `spawn` and `create_zone`.
- **Dev workflow**: Python >=3.10. Install deps with `pip install -r requirements.txt`; for SDK imports use `pip install -e .` to put `src/a2a` on `PYTHONPATH`. Run tests with `pytest -q`. Lint with `flake8` (CI runs flake8 + pytest).
- **Persistence hygiene**: Anything writing `data.json`, `ga2a-logs.txt`, or `ga2a-zones.db` writes to CWD—set working dirs explicitly in scripts/examples to avoid polluting the repo.
- **Ports**: Default TCP peer port 5050; pick unique ports for parallel peers/tests to avoid bind errors. Discovery uses UDP 5060.
- **Threading**: Peer listeners and discovery broadcaster/listener threads are daemon threads; ensure clean shutdown in samples and tests to avoid hanging processes.
- **Release info**: Version lives in [pyproject.toml](pyproject.toml) and [src/a2a/__init__.py](src/a2a/__init__.py). CI workflows (pytest/flake8) run on pushes; publishing requires tagging and PyPI token per README.

Use these notes to orient changes before editing code or tests. If anything is unclear or missing, tell me what to expand or adjust.
