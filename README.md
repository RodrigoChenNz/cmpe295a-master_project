# ShelfIQ — an AI agent that runs a small store, with the owner in the loop

SJSU CMPE 295A/295B master's project. Advisor: Prof. Vidyacharan Bhaskar.
**Team:** Het Bhalala · Rodrigo Chen · Parth Gala · Aruna Samhitha Mutyala

## What it is

A store that sells on the floor and online. Every night an agent reviews how yesterday's
decisions turned out, researches competitor prices and seasonality, reprices every product
with a plain-language explanation, plans what to buy from suppliers, and curates the online
listings. **Nothing it decides takes effect until the owner approves it in one inbox, or has
delegated that kind of decision by policy.** Every verdict, with the owner's reason, feeds the
next run, so the agent learns the owner's preferences the way a new employee would.

Approved shelf prices reach the labels over MQTT. The store also exposes a
[Universal Commerce Protocol](https://ucp.dev) server, so other people's shopping agents can
browse, buy and bargain for bulk, and the store's own agent uses the same protocol to buy from
suppliers. The owner ends up doing the work of a buyer, a pricing analyst, a web merchandiser
and a bookkeeper by reading one summary a day and approving or rejecting what the agent wants
to do. Moving the goods is the only job left on the floor.

What changed from the 295A design: the store sells online, the agent runs nightly instead of
continuously, every kind of decision (not only prices) goes through the owner's inbox, the
inbox produces training signal, and the UCP server is new.

## Architecture

```
owner console (React)  ──REST/WS──▶  backend (FastAPI + Postgres)  ◀──REST──  store agent (nightly run)
storefront (React)     ──UCP─────▶      /api  /ucp/v1  /.well-known/ucp        forecaster · simulator
buyer agents           ──UCP─────▶            │
supplier agents        ◀──UCP────  agent      │ retail/price (MQTT, retained, daily)
                                              ▼
                                   shelf labels (ESP32 e-ink · 16x2 LCD)  ──retail/shelf──▶ backend
```

| Part | Tech | Owner | Status |
|---|---|---|---|
| Console (`services/dashboard-web`) | React 19 · Vite · TypeScript | Rodrigo | built: review queue, catalogue, labels, locations, rules, users, activity |
| Backend (`services/dashboard-api`) | FastAPI · asyncpg · aiomqtt | Rodrigo | built: the decision gate, MQTT bridge, REST, WebSocket |
| Store agent (`services/pricing-agent`) | PyTorch · SB3 · gymnasium · SHAP · web search | Parth | stub |
| Forecaster (`services/forecaster`) | Prophet · LSTM · scikit-learn | Aruna | stub |
| Simulator (`services/simulator`) | gym environment, demand and competitor feed, supplier and buyer UCP stand-ins | Parth | stub |
| UCP server and storefront | FastAPI · React | unassigned | new |
| Shelf labels | Heltec E213 e-ink on ESP32; 16x2 LCD on MSP432 via gateway | Het | not started |

## The contract

**[`api-spec/README.md`](api-spec/README.md)** is the single interface document, split by
team: hardware (§2), frontend (§3), backend (§4), UCP (§5), AI (§6). It marks what is built,
what is new, and what needs all four approvals. Its §9 lists simplifications to decide before
building. [`contracts/`](contracts/) holds the MQTT JSON schemas; changing those needs all four
of us.

## Quick start

Host prerequisites: Docker Desktop, Node 20+, `uv`.

```bash
make up          # the real system on an empty database
make demo        # sample catalogue + fake publisher standing in for the agent and forecaster
make fresh       # wipe the real database and start over
make help        # everything else
```

Open <http://localhost:5173>. On an empty database the first account you create is the Super
admin; in the demo sign in with the account in `.env` (`admin@admin.com` / `admin` by default).
Ports: 5173 console, 8000 API (`/docs`, `/health`), 5432 Postgres, 1883 MQTT, 9101 MQTT over
WebSocket. [COMMANDS.md](COMMANDS.md) has the rest.

## Layout

```
api-spec/        ★ the API spec, one file, by team
contracts/       ★ MQTT payload schemas — four approvals to change
infra/           docker-compose, mosquitto config, postgres schema and demo seed
services/
  dashboard-api/ FastAPI backend                      Rodrigo
  dashboard-web/ React console                        Rodrigo
  pricing-agent/ the store agent (stub)               Parth
  forecaster/    demand and dead-stock risk (stub)    Aruna
  simulator/     fake store, gym env, UCP stand-ins   Parth
tools/           fake publisher, seed generator
docs/            workbook, meeting notes, agent prompts
```

Planned, not yet in the repo: `services/ucp-server/`, `services/storefront-web/`, `edge/` (firmware).

## How we work

Walking skeleton first: the thinnest path through every layer, then thicken each behind the
contract. Branches `<type>/<initials>/<short-name>` off `dev`, small PRs, squash-merge, weekly
promotion `dev → stage → main`. Details in [CONTRIBUTING.md](CONTRIBUTING.md). Meeting notes
go in [`docs/meetings/`](docs/meetings/).

## Open decisions

| Question | Owner | Where |
|---|---|---|
| Agent ↔ backend over MQTT (as built) or REST (recommended) | all four | spec §9 S1 |
| One label family (ESP32 e-ink) or two | Het | spec §9 S2 |
| Nightly batch policy instead of real-time TTLs and rate limits | Rodrigo, Parth | spec §9 S3 |
| Money as integer minor units everywhere | all four | spec §9 S4 |
| Separate UCP service or one backend process | Rodrigo | spec §9 S5 |
| UCP namespace we control for our negotiation extension | Rodrigo | spec O1 |
| Who owns the UCP server and the storefront | team | spec O2 |
| Does approval gating of purchases and deals change Chapter 2 | all + advisor | spec O6 |
| Hardware order placed? | Het | |

## Docs

- [API spec](api-spec/README.md)
- [Workbook (295A)](docs/workbook_assignment_1.md)
- [Meeting notes](docs/meetings/)
- [Agent prompts](docs/prompts/)
