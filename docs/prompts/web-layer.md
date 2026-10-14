# Agent Prompt — Layer 5 API (`dashboard-api`)

> Paste everything below the line into a fresh coding-agent session, run from the repo root.
> Scope: `services/dashboard-api` only — the FastAPI service, the MQTT bridge and the wire
> protocol. The React app has its own brief in [`web-design.md`](web-design.md), which owns
> the design system and every screen.

---

## Context

You are building **Layer 5 (Manager Dashboard)** of a master's project called *Multi-Agent
System for Dynamic Retail Pricing with Edge-IoT Integration* (SJSU CMPE 295B).

The full system is five layers, connected by an MQTT message bus:

```
L5  Manager Dashboard      React + FastAPI + Postgres      ← the API half is YOURS
L4  AI Backend             RL pricing agent, SHAP explainer, demand forecaster
L3  Message Bus            Mosquitto (MQTT v5)
L2  Edge Nodes             MSP432 LaunchPad + Wi-Fi module
L1  Physical Displays      16x2 character LCD at the shelf edge
```

An RL agent **recommends** retail prices in real time, each recommendation carrying a
SHAP-based explanation. A store manager approves, overrides or rejects them — or delegates
the small ones to a standing policy. The dashboard is where that supervision happens.

**Your service is the control point of the whole system.** The pricing agent publishes
proposals and never prices. `dashboard-api` is the *only* publisher of `retail/price/`, and
it publishes only what a human approved or what a human's policy auto-approved. Nothing
reaches a shelf without passing through you. That is a bigger responsibility than the usual
read-model dashboard backend, and most of the work below follows from it.

**Layers 1–4 do not exist yet.** Other team members are building them in parallel against the
same message contract. You must build so that the dashboard works today, driven only by a fake
publisher you write, and works unchanged when the real agents come online.

## Hard scope boundary

Build **only** `services/dashboard-api`. Do not implement, scaffold, or stub in-repo:

- the React dashboard — it has its own brief, [`web-design.md`](web-design.md)
- the reinforcement-learning pricing agent
- the SHAP explainer or any ML model
- the demand / dead-stock forecaster
- MSP432 firmware or any LCD driver code

If you need data from any of those, it arrives over MQTT in the shapes defined below, and you
simulate it with the fake publisher. Never invent an alternative payload shape.

## The message contract — treat as frozen

Five MQTT topics. Payloads are JSON, UTF-8. These are shared with three other developers;
do not change a field name, add a required field, or alter a type. If something is genuinely
missing, stop and say so rather than improvising. The authoritative versions are in
`contracts/` — read them.

**You subscribe to `proposal`, `explain` and `inventory`. You publish `price` and
`guardrail`.** No one else publishes `price`.

```jsonc
// SUBSCRIBE  retail/proposal/{product_id}   QoS 1, NOT retained
{
  "product_id":     "SKU-001",
  "proposed_price": 12.99,             // what the agent recommends — not live
  "current_price":  12.50,             // the approved price it's moving from
  "currency":       "USD",
  "decision_id":    "9f2c...-uuid",    // joins proposal ↔ explanation ↔ audit
  "reason_tag":     "Competitor up",   // <= 16 chars, renders on LCD line 2
  "agent_version":  "stub-0.1",
  "expires_at":     "2026-09-17T14:00:00Z",
  "ts":             "2026-09-17T10:00:00Z"
}

// PUBLISH  retail/price/{product_id}        QoS 1, retained — YOU are the only publisher
{
  "product_id":    "SKU-001",
  "price":         12.99,              // number, 2dp — the live shelf price
  "currency":      "USD",
  "decision_id":   "9f2c...-uuid",     // null if a manager set this with no proposal
  "reason_tag":    "Competitor up",
  "approval":      "manager",          // "auto" | "manager" | "override"
  "approved_by":   "rchen",            // or "policy" when approval is "auto"
  "agent_version": "stub-0.1",
  "ts":            "2026-09-17T10:04:12Z"   // moment of approval, not of proposal
}

// PUBLISH  retail/guardrail/{product_id}    QoS 1, retained
{
  "product_id":       "SKU-001",
  "min_price":        9.99,
  "max_price":        15.99,
  "auto_approve_pct": 5.0,             // null = always ask a human
  "set_by":           "rchen",
  "ts":               "2026-09-17T09:00:00Z"
}

// SUBSCRIBE  retail/explain/{product_id}    QoS 1
{
  "product_id":  "SKU-001",
  "decision_id": "9f2c...-uuid",       // matches the price message above
  "base_value":  12.50,                // SHAP base (expected) value
  "shap": [                            // signed contributions, descending |value|
    { "feature": "competitor_price", "value":  0.62 },
    { "feature": "inventory_level",  "value": -0.41 },
    { "feature": "day_of_week",      "value":  0.18 },
    { "feature": "time_of_day",      "value":  0.10 }
  ],
  "ts": "2026-09-17T10:00:00Z"
}
// base_value + Σ shap = 12.50 + 0.49 = 12.99 — additivity must hold, and the
// publisher aggregates any tail beyond 20 features into an "other_factors"
// entry rather than truncating.

// SUBSCRIBE  retail/inventory/{product_id}  QoS 0, retained
{
  "product_id":       "SKU-001",
  "dead_stock_risk":  0.73,            // 0.0 - 1.0
  "predicted_sellby": "2026-10-04",
  "model_version":    "stub-0.1",
  "ts":               "2026-09-17T10:00:00Z"
}
```

`decision_id` is the join key across all of it. A proposal without its explanation is a valid
intermediate state — the explanation may arrive seconds later, or never. Handle that.

## The decision gate — the core of this service

Every proposal you receive gets evaluated against the SKU's stored policy:

```
auto_approve  ⟺  min_price <= proposed_price <= max_price
             AND  |proposed_price − current_price| / current_price × 100 <= auto_approve_pct
```

`auto_approve_pct` of `NULL` means always ask.

- **Passes** → write `proposals` with `status='auto_approved'`, publish to `retail/price/`
  with `approval:"auto"`, `approved_by:"policy"`, append `proposal_auto_approved` and
  `price_published` to the audit log, push both over the socket.
- **Fails** → write `status='pending'` and push it to the review queue. Nothing goes to the
  shelf. The shelf keeps the price it has.

Then the human decisions, each of which publishes to `retail/price/` and writes an audit row:

| Action | `proposals.status` | Published `approval` | `price` |
|---|---|---|---|
| Approve | `approved` | `manager` | the proposed price |
| Override | `overridden` | `override` | the manager's price |
| Reject | `rejected` | — **nothing published** | shelf unchanged |
| Lapse past `expires_at` | `expired` | — **nothing published** | shelf unchanged |

**Expiry is your job, not the client's.** Run a periodic sweep that moves lapsed pending rows
to `expired` and emits the audit row and socket event. A queue that only expires when someone
happens to be looking at it is not a queue.

**A newer proposal for a SKU supersedes any pending one** — mark the old row `superseded`, do
not leave two competing recommendations for the same shelf.

An override must still be validated against `min_price`/`max_price` — a manager may set any
price inside their own band; reject one outside it with a clear error rather than silently
publishing it.

## What to build

### `services/dashboard-api` — FastAPI

Python 3.11. FastAPI, uvicorn, `aiomqtt`, `asyncpg`, `pydantic` v2.

Responsibilities, in one process:

1. **MQTT bridge.** Subscribe to `retail/proposal/+`, `retail/explain/+`,
   `retail/inventory/+`. Validate every message against a Pydantic model mirroring the
   contract. Log and drop malformed messages — never crash the subscriber loop.
2. **Decide.** Run each proposal through the gate above: auto-apply or queue.
3. **Publish.** You are the only publisher of `retail/price/` and `retail/guardrail/`.
4. **Persist.** Write everything to Postgres, including the audit trail.
5. **Expire.** Sweep lapsed proposals on a timer.
6. **Fan out.** Push every state change to connected WebSocket clients immediately.
7. **Serve.** REST for the queue, history, alerts and audit.

The schema is already written at `infra/postgres/schema.sql` and applied on container init.
Read it — it is the spec for steps 2, 4 and 5. It has been verified against Postgres 16.

| table / view | purpose |
|---|---|
| `products` | SKU catalog **plus the pricing policy**: `min_price`, `max_price`, `auto_approve_pct` |
| `proposals` | every recommendation and its fate; the review queue lives here, not on the broker |
| `price_history` | every approved price published to a shelf, with `approval` and `approved_by` |
| `shap_vectors` | explanations, keyed by `decision_id`, FK to `proposals` |
| `inventory_risk` | latest + historical dead-stock risk per SKU |
| `agent_audit_log` | append-only: proposal received, auto-approved, approved, overridden, rejected, expired, published, guardrail set |
| `current_product_state` | one row per SKU: current price, **previous price**, approval provenance, pending count, risk |
| `review_queue` | pending proposals joined to explanation and policy, soonest to expire first |

REST API, all under `/api`:

```
GET  /health                                    liveness + mqtt/db connection state

GET  /api/proposals?status=pending              the review queue (from review_queue)
GET  /api/proposals/{decision_id}               one proposal + its explanation
POST /api/proposals/{decision_id}/approve       {approved_by}      → publishes price
POST /api/proposals/{decision_id}/override      {price, approved_by, reason_tag?}
POST /api/proposals/{decision_id}/reject        {approved_by, decline_reason?}

GET  /api/products                              catalog + current + previous + risk
GET  /api/products/{id}
GET  /api/products/{id}/price-history?range=24h|7d|30d|1y
GET  /api/products/{id}/explanation             latest SHAP vector
GET  /api/explanations/{decision_id}            specific SHAP vector
GET  /api/alerts?threshold=0.7                  SKUs at dead-stock risk
GET  /api/audit?product_id=&event_type=&from=&to=&limit=&cursor=
PUT  /api/products/{id}/policy                  {min_price, max_price, auto_approve_pct, set_by}
```

The three decision endpoints are the only write path to a shelf. Make them **idempotent** —
a double-click, or a retry after a dropped response, must not publish twice. Key off
`proposals.status`: a proposal that is not `pending` returns a conflict describing what
already happened to it, rather than deciding it again.

`GET /api/alerts` takes its `threshold` as a parameter and echoes it in the response, because
the UI has to state the threshold it used; nothing in this project defines a default, so pick
one, document it, and make it configurable.

WebSocket at `/ws/stream`. Every frame is an envelope — never a bare payload:

```jsonc
{ "type": "proposal_new",      "data": { /* proposal + policy verdict */ } }
{ "type": "proposal_decided",  "data": { "decision_id":…, "status":…, "decided_by":…,
                                         "final_price":… } }
{ "type": "proposal_expired",  "data": { "decision_id":…, "product_id":… } }
{ "type": "price_update",      "data": { /* the published price payload */ } }
{ "type": "explanation",       "data": { /* explain payload */ } }
{ "type": "inventory_risk",    "data": { /* inventory payload */ } }
{ "type": "policy_changed",    "data": { /* guardrail payload */ } }
{ "type": "heartbeat",         "ts": "..." }
```

`proposal_decided` fires for auto-approvals too — a manager watching the board must see the
policy acting on their behalf, not just discover it later in the log. An auto-approval emits
both `proposal_decided` and `price_update`.

Send a heartbeat every 30s. Handle client disconnects without leaking tasks.

`PUT /api/products/{id}/policy` writes to Postgres **and** publishes to
`retail/guardrail/{product_id}`, so the pricing agent can clamp its own proposals to the band
the manager set.

### `services/dashboard-web` — not in this prompt

The React app has its own brief: **[`web-design.md`](web-design.md)**, which owns the design
system, both color schemes, every screen, and the SHAP explanation panel. Do not design or
build the UI from this document — the two would disagree.

What you owe the web app from here is the contract it consumes, and only that:

- REST endpoints exactly as listed above
- WebSocket at `/ws/stream`, envelope types as listed above
- Auto-reconnect is the client's job, but the server must tolerate it: no leaked tasks on
  disconnect, and a reconnecting client re-fetches current state from REST rather than
  expecting replay. Buffer nothing server-side — the queue rebuilds from `GET /api/proposals`,
  which is exactly why pending proposals live in Postgres and not in retained MQTT.

The web app is built against a mock adapter first and swaps to this service with a one-line
env change, so it is not blocked on you and you are not blocked on it.

### `tools/fake_publisher.py` — your unblocker

A standalone script that publishes contract-valid **proposals** for ~8 fake SKUs on a loop,
with a matching explanation shortly after — sometimes deliberately delayed, occasionally
omitted, to prove the pipeline handles it — plus periodic inventory risk. This is how you
develop and demo before Layer 4 exists. Make interval and SKU count configurable.

It must emit a deliberate mix, or you will only ever exercise one path: small in-band moves
that auto-approve, large moves that queue, at least one proposal outside the band, and one
that is left to expire.

## Non-negotiable constraints

- **`docker compose up` from a clean clone must bring up mosquitto, postgres and the API with
  no manual setup step.** Add the compose file at `infra/docker-compose.yml`.
- No external paid APIs. No SerpAPI, no live scraping — competitor prices arrive over MQTT from
  another team member's service. The fake publisher covers development.
- Never commit `node_modules/`, `dist/`, `__pycache__/`, `.venv/`, or `.env`. Write
  `.gitignore` before the first commit.
- Python dependencies pinned in `pyproject.toml` managed by `uv`. No bare `pip install`, no
  committed virtualenv.
- Price is money: use `Decimal` server-side and `NUMERIC(10,2)` in Postgres. Never float
  arithmetic on a price, and never serialize one through a float.
- All timestamps are UTC ISO-8601 on the wire.

## Build order

Do not build these in parallel. Each step must run before the next begins.

1. `infra/docker-compose.yml` with mosquitto + postgres. Both already written and verified —
   confirm `make up` works and the schema applies.
2. `tools/fake_publisher.py` emitting proposals. Verify with `mosquitto_sub`.
3. API: MQTT subscriber + Pydantic validation, logging received proposals. No DB yet.
4. API: persistence — proposals, explanations, risk. Verify rows via `psql`.
5. **API: the decision gate.** Evaluate policy, auto-approve, publish to `retail/price/`.
   Verify with `mosquitto_sub -t 'retail/price/#'` that only approved prices appear and that
   an out-of-policy proposal publishes nothing. **This is the milestone that matters — stop
   and confirm it before continuing.**
6. API: the three decision endpoints, idempotent, each publishing and auditing correctly.
7. API: the expiry sweep.
8. API: WebSocket fan-out. Verify with `websocat` or a 10-line client.
9. API: remaining REST — queue, history, alerts, audit, policy.

The web app's build order lives in [`web-design.md`](web-design.md) and runs independently
against a mock adapter — the two tracks meet when the web app swaps `mockAdapter` for
`httpAdapter`.

## Definition of done

- `docker compose up` on a clean clone brings up mosquitto, postgres and the API within 30
  seconds, no manual steps.
- **No proposal ever reaches `retail/price/` without passing the gate.** Subscribe to
  `retail/price/#` while the fake publisher floods proposals: every message there has an
  `approval` and an `approved_by`, and out-of-policy proposals produce nothing.
- A small in-band proposal auto-approves and publishes within 2 seconds; a large one lands in
  `GET /api/proposals?status=pending` and publishes nothing.
- Approve, override and reject each publish (or don't) correctly, write the right audit rows,
  and are **idempotent** — calling approve twice publishes one price and returns a conflict
  the second time.
- An override outside the SKU's own band is rejected with a clear error, not published.
- A proposal past `expires_at` moves to `expired` on its own, without anyone looking at it,
  and the shelf keeps its price.
- A newer proposal supersedes a pending one rather than queueing both for the same shelf.
- Restarting the API rebuilds the pending queue from Postgres with nothing lost.
- Killing and restarting mosquitto leaves the API reconnecting on its own — no crash, no
  leaked tasks, no restart needed.
- An explanation arriving late, or never, is stored and served correctly — a proposal with no
  `shap_vectors` row is a valid response, not an error.
- A malformed MQTT message is logged and dropped without killing the subscriber loop.
- `GET /api/products/SKU-001/price-history?range=7d` returns the same rows `psql` shows.
- README documents every endpoint, the WebSocket envelope types, the auto-approve rule, and
  how to run the fake publisher standalone.

## Working agreement

- Small commits, conventional-commit messages, branch `feat/web-<topic>`.
- Report progress against the numbered build order above.
- Stop and ask if the contract seems to be missing something you need — do not extend it
  unilaterally. Three other people are coding against it.
