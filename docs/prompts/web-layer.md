# Agent Prompt — Layer 5 (Manager Dashboard: web + API)

> Paste everything below the line into a fresh coding-agent session, run from the repo root.
> Scope: `services/dashboard-api` and `services/dashboard-web` only.

---

## Context

You are building **Layer 5 (Manager Dashboard)** of a master's project called *Multi-Agent
System for Dynamic Retail Pricing with Edge-IoT Integration* (SJSU CMPE 295B).

The full system is five layers, connected by an MQTT message bus:

```
L5  Manager Dashboard      React + FastAPI + Postgres      ← YOU BUILD THIS
L4  AI Backend             RL pricing agent, SHAP explainer, demand forecaster
L3  Message Bus            Mosquitto (MQTT v5)
L2  Edge Nodes             MSP432 LaunchPad + Wi-Fi module
L1  Physical Displays      16x2 character LCD at the shelf edge
```

Autonomous agents set retail prices in real time. Each price decision carries a SHAP-based
explanation, so the system can always answer *why* a price moved. The dashboard is where a
store manager watches prices change live, inspects the AI's reasoning, sets guardrails, and
audits past decisions.

**Layers 1–4 do not exist yet.** Other team members are building them in parallel against the
same message contract. You must build so that the dashboard works today, driven only by a fake
publisher you write, and works unchanged when the real agents come online.

## Hard scope boundary

Build **only** the two services below. Do not implement, scaffold, or stub in-repo:

- the reinforcement-learning pricing agent
- the SHAP explainer or any ML model
- the demand / dead-stock forecaster
- MSP432 firmware or any LCD driver code

If you need data from any of those, it arrives over MQTT in the shapes defined below, and you
simulate it with the fake publisher. Never invent an alternative payload shape.

## The message contract — treat as frozen

Three MQTT topics. Payloads are JSON, UTF-8. These are shared with three other developers;
do not change a field name, add a required field, or alter a type. If something is genuinely
missing, stop and say so rather than improvising.

```jsonc
// topic: retail/price/{product_id}        QoS 1, retained
{
  "product_id":    "SKU-001",
  "price":         12.99,              // number, 2dp
  "currency":      "USD",
  "decision_id":   "9f2c...-uuid",     // joins price ↔ explanation ↔ audit row
  "reason_tag":    "Inventory high",   // <= 16 chars, renders on LCD line 2
  "agent_version": "stub-0.1",
  "ts":            "2026-09-17T10:00:00Z"
}

// topic: retail/explain/{product_id}      QoS 1
{
  "product_id":  "SKU-001",
  "decision_id": "9f2c...-uuid",       // matches the price message above
  "base_value":  12.50,                // SHAP base (expected) value
  "shap": [                            // signed contributions, descending |value|
    { "feature": "inventory_level",  "value": -0.05 },
    { "feature": "competitor_price", "value":  0.02 },
    { "feature": "day_of_week",      "value":  0.02 }
  ],
  "ts": "2026-09-17T10:00:00Z"
}

// topic: retail/inventory/{product_id}    QoS 0
{
  "product_id":       "SKU-001",
  "dead_stock_risk":  0.73,            // 0.0 - 1.0
  "predicted_sellby": "2026-10-04",
  "ts":               "2026-09-17T10:00:00Z"
}
```

`decision_id` is the join key across all three concerns. A price row without its explanation is
a valid intermediate state — the explanation may arrive up to a few seconds later, or never.
Handle that.

## What to build

### `services/dashboard-api` — FastAPI

Python 3.11. FastAPI, uvicorn, `aiomqtt`, `asyncpg`, `pydantic` v2.

Responsibilities, in one process:

1. **MQTT bridge.** Subscribe to `retail/price/+`, `retail/explain/+`, `retail/inventory/+`.
   Validate every message against a Pydantic model mirroring the contract. Log and drop
   malformed messages — never crash the subscriber loop.
2. **Persist.** Write each valid message to Postgres (schema below).
3. **Fan out.** Push every valid message to all connected WebSocket clients immediately.
4. **Serve history.** REST endpoints for everything that isn't live.

Postgres tables — create as `infra/postgres/schema.sql`, applied on container init:

| table | purpose |
|---|---|
| `products` | SKU catalog: id, name, category, cost, min_price, max_price |
| `price_history` | every price message, indexed on `(product_id, ts desc)` |
| `shap_vectors` | every explanation, keyed by `decision_id` |
| `inventory_risk` | latest + historical dead-stock risk per SKU |
| `agent_audit_log` | append-only: who/what/when for every decision |

REST API, all under `/api`:

```
GET  /health                                    liveness + mqtt/db connection state
GET  /api/products                              catalog + latest price + latest risk
GET  /api/products/{id}
GET  /api/products/{id}/price-history?range=24h|7d|30d|1y
GET  /api/products/{id}/explanation             latest SHAP vector
GET  /api/explanations/{decision_id}            specific SHAP vector
GET  /api/alerts                                SKUs above a dead-stock risk threshold
GET  /api/audit?product_id=&from=&to=&limit=    paginated decision log
PUT  /api/products/{id}/guardrails              manager sets min_price / max_price
```

WebSocket at `/ws/stream`. Every frame is an envelope — never a bare payload:

```jsonc
{ "type": "price_update",   "data": { /* price payload */ } }
{ "type": "explanation",    "data": { /* explain payload */ } }
{ "type": "inventory_risk", "data": { /* inventory payload */ } }
{ "type": "heartbeat",      "ts": "..." }
```

Send a heartbeat every 30s. Handle client disconnects without leaking tasks.

`PUT /api/products/{id}/guardrails` writes to Postgres **and** publishes to
`retail/guardrail/{product_id}` so the pricing agent can pick the limits up later. That topic is
yours to define; document it in `contracts/` as an addition, and flag it to the team.

### `services/dashboard-web` — React

React 18, Vite, TypeScript 5, Chart.js via `react-chartjs-2`. No component framework beyond
whatever you need; plain CSS modules or Tailwind, your call, but be consistent.

Two routes:

**`/` — Dashboard**
- KPI row: active SKUs, price changes today, average margin, SKUs at dead-stock risk
- Live price table: SKU, name, current price, delta vs. previous, reason tag, last-updated.
  A row flashes briefly when it updates over WebSocket.
- Dead-stock alert panel, sorted by risk descending
- Connection status indicator: live / reconnecting / offline

**`/products/:id` — Product detail**
- Price history chart with 24h / 7d / 30d / 1y range toggle
- **SHAP explanation panel**: horizontal diverging bar chart of feature contributions,
  negative left, positive right, with the base value marked. This is the centerpiece of the
  project's transparency claim — make it genuinely readable, labeled in plain language
  (`inventory_level` → "Inventory level"), not a raw dump.
- Guardrail controls: min/max price inputs, saved via the PUT endpoint
- Decision audit table for this SKU

WebSocket client requirements: auto-reconnect with exponential backoff, capped. Buffer nothing
across reconnects — on reconnect, re-fetch current state from REST, then resume streaming. The
UI must degrade to REST-only polling if the socket cannot connect at all.

### `tools/fake_publisher.py` — your unblocker

A standalone script that publishes contract-valid messages for ~8 fake SKUs on a loop: a price
change every few seconds, a matching explanation shortly after (sometimes deliberately delayed,
occasionally omitted, to prove the UI handles it), and periodic inventory risk updates. This is
how you develop and demo before Layer 4 exists. Make the interval and SKU count configurable.

## Non-negotiable constraints

- **`docker compose up` from a clean clone must bring up mosquitto, postgres, the API, and the
  web app, with no manual setup step.** Add the compose file at `infra/docker-compose.yml`.
- No external paid APIs. No SerpAPI, no live scraping — competitor prices arrive over MQTT from
  another team member's service. The fake publisher covers development.
- Never commit `node_modules/`, `dist/`, `__pycache__/`, `.venv/`, or `.env`. Write
  `.gitignore` before the first commit.
- Python dependencies pinned in `pyproject.toml` managed by `uv`. No bare `pip install`, no
  committed virtualenv.
- The dashboard must be readable in both light and dark color schemes.
- Price is money: use `Decimal` server-side and format to exactly 2 decimal places client-side.
  Never do float arithmetic on a price.
- All timestamps are UTC ISO-8601 on the wire; render in the browser's local timezone.

## Build order

Do not build these in parallel. Each step must run before the next begins.

1. `infra/docker-compose.yml` with mosquitto + postgres only. Verify with `mosquitto_sub`.
2. `tools/fake_publisher.py`. Verify messages appear in `mosquitto_sub`.
3. API: MQTT subscriber + Pydantic validation, logging received messages. No DB, no web yet.
4. API: Postgres schema + persistence. Verify rows land via `psql`.
5. API: WebSocket fan-out. Verify with `websocat` or a 10-line test client.
6. API: REST endpoints over the stored data.
7. Web: scaffold, live price table on WebSocket only. **This is the milestone that matters —
   stop and confirm it works before continuing.**
8. Web: product detail, price history chart.
9. Web: SHAP panel.
10. Web: guardrails, alerts, audit table, KPI row.

## Definition of done

- `docker compose up` on a clean clone serves the dashboard with live data from the fake
  publisher within 30 seconds, no manual steps.
- A price published by *any* MQTT client appears in the browser in under 2 seconds.
- Killing and restarting the mosquitto container leaves the UI recovering on its own, showing a
  clear connection state the whole time — no blank screen, no crash, no page reload needed.
- A price message with no matching explanation renders the row correctly and shows an explicit
  "explanation pending" state in the SHAP panel rather than an error or empty chart.
- `GET /api/products/SKU-001/price-history?range=7d` returns data that matches what the chart
  draws.
- README documents every endpoint, the WebSocket envelope types, and how to run the fake
  publisher standalone.

## Working agreement

- Small commits, conventional-commit messages, branch `feat/web-<topic>`.
- Report progress against the numbered build order above.
- Stop and ask if the contract seems to be missing something you need — do not extend it
  unilaterally. Three other people are coding against it.
