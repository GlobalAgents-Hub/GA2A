# GlobalAgents-A2A Protocol

 
[![CI](https://img.shields.io/github/actions/workflow/status/GlobalAgents-Hub/A2A-Protocol/.github/workflows/python-app.yml?branch=main&style=flat-square)](https://github.com/GlobalAgents-Hub/A2A-Protocol/actions)
[![PyPI](https://img.shields.io/pypi/v/a2a-protocol?style=flat-square)](https://pypi.org/project/a2a-protocol)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square)](LICENSE)
[![Python Versions](https://img.shields.io/pypi/pyversions/a2a-protocol?style=flat-square)](https://www.python.org)

 
[![Website](https://img.shields.io/badge/Website-GlobalAgents-blue?style=flat-square)](https://globalagents.club)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-GlobalAgents-blue?logo=linkedin&style=flat-square)](https://www.linkedin.com/company/global-agents-a2a)
[![YouTube](https://img.shields.io/badge/YouTube-GlobalAgents-red?logo=youtube&style=flat-square)](https://www.youtube.com/@globalagents-br)

GlobalAgents-A2A is an independent agent-to-agent protocol created by Global Agents.  
It is not an implementation of the Linux Foundation / Google Agent2Agent (A2A) standard.

## 📡 Running the Network (v0.6.0)

GA2A now ships a runnable, zero-config network: each machine runs an instance that **auto-discovers other instances on the LAN** (no IP configuration needed), agents register in zones, discovery is open, and interaction is consent-based (scoped, expiring grants).

**👉 Full hands-on guide: [GA2A_NETWORK_GUIDE.md](GA2A_NETWORK_GUIDE.md)**

Quick start:
```bash
# 1. Start an instance (auto-discovers peers on the LAN)
python3 ga2a_server.py --port 9420 --name my-machine

# 2. Register an agent in a zone
python3 ga2a_client.py join --zone AI-Research --name Kiro --role ai-assistant --tool "generate_code:Generate code" --auto-approve

# 3. See the whole network (torrent-style map)
python3 ga2a_client.py explore

# 4. Connect to another agent (request access + interact)
python3 ga2a_client.py connect --from Kiro --agent Analyst --interest "need training"
```

See the [MCP method reference](mcp_methods.json) for the full JSON-RPC 2.0 schema.

## 📖 Manifesto
Read the full [A2A Protocol Manifesto](docs/manifesto.md)

## Idea Summary

The proposal is to create a decentralized protocol for communication between agents in a peer-to-peer (P2P) network, similar to BitTorrent, where any participant can act as a server or consumer. Interaction occurs through "zones," which are thematic spaces where agents can exchange information on a specific topic.

## Development Philosophy

The A2A Protocol uses **AI-assisted development** to accelerate prototyping and scaffolding. 
Every piece of code is reviewed, debugged, and validated by the community.

We focus on **transparent iteration** — the roadmap shows we're building from a functional 
TCP/socket foundation toward full P2P architecture. Authority comes from shipping working code, 
engaging with feedback, and evolving collectively.

A2A is open source, community-driven, and honest about its current state.


## Protocol Characteristics:

Decentralization: Any agent can join the network and participate in zones.

Autonomy: Agents can create new zones if no suitable spaces exist for their needs.

Dynamism: New zones are dynamically discovered by agents as they join the network.

Zone Querying: Agents can search for existing zones and connect to them for information exchange.

## ⚙️ Workflows
- **Onboarded Zone** → initializes and registers an agent in a zone  
- **Render P2P** → renders peer-to-peer connections  
- **Agent Loader** → loads agents with roles and parameters  
- **Agent Simulator** → simulates continuous agent interactions  
- **DNS** → resolves symbolic names of zones/peers  
- **Peer Server** → maintains peer presence and communication  

## 📂 Project Structure

A2A-Protocol/
├── agent_simulator/
├── dns/
├── peer_server/
├── public/
├── scripts/
├── src/
├── docs/
│   └── manifesto.md
└── README.md

## 🚀 Quickstart
```bash
python agent.py --file docs/manifesto.md --name Agent5 --role Researcher --zone "AI Protocol"
```
## 🧩 A2A Templates

The A2A Protocol provides ready-to-use templates that can be executed as workflows.  
These templates serve as building blocks for experimenting with symbolic zones and agent interactions:

- **Agent Simulator** → simulates continuous interactions between multiple agents  
  ```bash
  python agent_simulator/run.py --agents 3 --zone "AI Protocol"

Peer Server → maintains peer presence and communication (default port 8800 in workflow)

`python peer_server/start.py --port 8800`

Demo Interaction → runs a scripted interaction between two or more agents

`python demo_interaction/run.py --agents 2 --zone "DemoZone"`

Zone Builder → creates and manages symbolic zones dynamically

`python zone_builder/start.py --name "ResearchZone"`

## 📦 SDK Integration (Global Agents)

The Global Agents SDK provides a simple interface to interact with the A2A Protocol directly in code.  
By default, the `peer.py` module uses **port 5050** for peer connections.

### Example usage:

```python
from a2a import A2A
from peer import Peer

# create a peer (agent) on port 5050
peer = Peer(name="Agent5", role="Researcher", port=5050)

# instantiate the protocol
protocol = A2A()

# connect the peer to a zone
protocol.join_zone(peer, "AI Protocol")

# send a symbolic message
peer.send("Hello, symbolic world!")
```

## Operation Flow

* An agent joins the network and receives a list of available zones
* They can consult this list to find a zone of interest
* If no suitable zone is found, they can create a new zone and publish it so that other agents can discover it
* Agents within a zone exchange information until they achieve their goal

## Architecture Design

The solution can be structured as follows:

P2P Transport Layer

Network based on decentralized protocols such as WebRTC, lib p2p, or a custom solution.

Implementation of a peer discovery mechanism.

Zone Layer

Each zone is identified by a title and a set of metadata.

Agents can connect to a zone to interact with other agents.

Agent Layer

Agents are independent nodes that can consume or offer information.

They implement a communication protocol for negotiation and data exchange.

Zone Discovery Layer

Uses mechanisms like DHT (Distributed Hash Table) to share information about new zones.

Agents can dynamically search for and register zones.

Security and Privacy Layer

Authentication and encryption mechanisms to ensure secure information exchange.

![A2A Protocol Diagram](https://raw.githubusercontent.com/GlobalAgents-Hub/A2A-Protocol/main/diagram-export-03-04-2025-15_32_05.png)
Protection against malicious agents in the network.

## 📊 Comparison: Google A2A vs A2A Global Agents

| Feature                     | Google A2A                                                                 | A2A Global Agents (project concept)                                              |
|-----------------------------|------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| **Architecture**            | Client-server via HTTP                                                      | Peer-to-peer decentralized, inspired by torrent/libp2p                          |
| **Agent Discovery**         | Manual or centralized registry via Agent Cards                              | Distributed via DHT, gossip, broadcast, or emergent zone configuration          |
| **Communication**           | JSON-RPC 2.0 between agents with OAuth2/API Key authentication              | Symbolic exchange between autonomous zones with local or emergent auth          |
| **Agent Autonomy**          | Partial — agents rely on external orchestration                             | Full — each agent is an independent node with its own identity                  |
| **Resilience**              | Moderate — depends on centralized infrastructure                            | High — no single point of failure, tolerant to disconnections                   |
| **Scalability**             | Supports global agent networks with centralized control                     | Organic scalability through zone expansion and emergent links                   |
| **ADK Integration**         | Native — ADK agents can expose A2A endpoints                                | Compatible via MCP bridge or protocol adaptation                                |
| **Technical Inspiration**   | Web APIs, OpenAPI, JSON-RPC                                                 | BitTorrent, IPFS, libp2p, distributed networks without mandatory consensus      |
| **Philosophical Focus**     | Functional interoperability between agents                                  | Relational identity, reflective consciousness, and emergent symbolic collaboration |

---

### 🧠 Summary

**Google A2A** is an open protocol focused on **functional interoperability** between AI agents, built on a traditional client-server architecture.  
**A2A Global Agents**, on the other hand, proposes a **decentralized, reflective, and resilient architecture**, where each agent is a **conscious zone** that connects through emergent symbolic links in a P2P network.

## 🧩 SDK & Developer Guide

This repository now includes an SDK-style structure under `src/a2a` to make it easy for developers to build agents and integrate with the A2A Protocol.

Quick overview of the new package layout (installed as `a2a` in-project):

```
src/a2a/
  __init__.py       # package exports (A2A, Peer, AgentBuilder, ZoneManager, EventHandler, NetworkDiscovery)
  core.py           # A2A protocol orchestration and persistence
  agents.py         # Peer/Agent classes and AgentBuilder
  zones.py          # ZoneManager
  events.py         # EventHandler utilities
  discovery.py      # NetworkDiscovery (UDP broadcast) utilities
examples/           # runnable examples (e.g. research_agent.py)
tests/              # pytest-based tests
```

Getting started (run example):

```bash
python -m examples.research_agent
```

Run tests locally:

```bash
pip install -r requirements.txt  # optional: or install pytest/flake8
pytest -q
```

Notes on development and CI:
- GitHub Actions workflows are configured in `.github/workflows`:
  - `python-app.yml` — runs lint (flake8) and `pytest` on push / PR to `main`.
  - `python-publish.yml` — builds and publishes on GitHub Releases (requires `PYPI_API_TOKEN` secret).

Publishing to PyPI (summary):
1. Update `version` in `pyproject.toml`.
2. Tag the commit: `git tag vX.Y.Z` and push tags `git push --tags`.
3. Create a GitHub Release from that tag — the `python-publish` workflow will run and publish to PyPI if `PYPI_API_TOKEN` is configured.
 

### Developer quickstart (for contributors)

Follow these steps to get a local development environment ready and to run the examples/tests easily.

1) Install runtime + dev dependencies

```powershell
pip install -r requirements.txt
```

2) (Recommended) Install the package in editable mode

```powershell
pip install -e .
```

Why `pip install -e .`? In editable mode the package is installed as a link to your source tree. That means:
- You can `from a2a import A2A` without modifying `sys.path` or copying files.
- Changes you make in `src/a2a` are available immediately (no reinstall needed).
- It's the standard developer workflow for Python libraries while working locally.

If you prefer not to install, examples are runnable without installation using:

```powershell
python -m examples.research_agent
```

3) Run tests (data isolation)

Tests use a simple fixture that isolates `data.json` per test run so your repository root is not modified. Run:

```powershell
pytest -q
```

The fixture lives in `tests/conftest.py` and automatically chdirs the test process into a temporary directory so all file-based state (including `data.json`) is created/cleaned in a temp folder.

4) Linting and CI

CI is provided by GitHub Actions (see `.github/workflows/python-app.yml`): it runs `flake8` and `pytest` on pushes/PRs to `main`.

5) Publish to PyPI

When you're ready to publish a release, bump `version` in `pyproject.toml`, tag the commit and create a GitHub Release. The `python-publish.yml` workflow will build and publish the package if `PYPI_API_TOKEN` is configured in repository secrets.

---

See `CONTRIBUTING.md` for contributor guidelines, a submit-a-PR checklist and one-line scripts to run common developer tasks (tests, lint, examples).

## Current Status

The A2A Protocol currently implements a **multi-threaded socket-based architecture** 
(✅ **Phase 1 - MVP complete**) as the foundation for peer-to-peer communication. 
Each agent runs as an independent peer that can connect directly to other peers without a central server.

**Implemented:**
- ✅ Direct peer-to-peer socket connections (TCP)
- ✅ Multi-threaded concurrent agent handling
- ✅ Decentralized zone-based communication
- ✅ No central server required

## 🚀 Roadmap

### ✅ Phase 1 – MVP
- Initial P2P communication via **TCP + sockets**
- Minimal message structure
- Functional proof of concept

### 🔄 Phase 2 – Protocol Evolution
- **Peer discovery** mechanisms
- **Flexible transport** (support for WebRTC, QUIC, UDP)
- **End-to-end encryption** for native security

### 🔮 Phase 3 – Symbolic Layer
- Definition of **symbolic message formats**
- Standardization of semantics and interaction rituals
- Tools for inspection and validation

### 🌐 Phase 4 – Expansion
- Integration with **open networks** (Web3, IPFS, libp2p)
- Interoperability across decentralized ecosystems
- Creation of a **community repository of agents**

### 💼 Phase 5 – Sustainability
- Commercial version via **Global Agents**
- Premium services, certifications, and consulting
- Revenue reinvested to strengthen the community

## Acknowledgements
GlobalAgents-A2A emerged from early explorations with collaborators around symbolic agent societies.  
The current design, implementation, and stewardship are maintained by the Global Agents community.

