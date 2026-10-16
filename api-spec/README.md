# ShelfIQ API Spec v2 — the agent runs the store, the owner approves

**Date:** 2026-10-14 · **Base:** `main` @ `922761a` · **Status:** draft for team sign-off

v2 extends what is already built on `main` (contracts, `dashboard-api`, the console) to the new scope: a store that sells online as well as on the floor, a nightly agent that researches the market and reprices with explanations, a UCP server so other agents can buy from us and we can buy from suppliers, and an owner inbox where every agent decision is approved or rejected and that verdict feeds the next cycle.

Legend used on every row: **built** = exists on `main` today · **new** = add · **contract** = touches `contracts/` and needs all four approvals.

Read only your part: §2 hardware, §3 frontend, §4 backend, §5 UCP, §6 AI. §1 applies to everyone.

---

## 0. The system in one picture

```
                 nightly 02:00 ─────────────────────────────────────────────┐
                                                                            ▼
  simulator / web ──market data──▶  agent (L4)  ──price proposals (MQTT)──▶ dashboard-api ──gate──▶ decisions inbox ◀── owner (console)
  forecaster ──inventory risk────▶    │         ──other decisions (REST)──▶      │                      │ approve / override / reject + reason
                                      │                                          │ approved price       ▼
                                      ◀──────── feedback export (REST) ──────────┤               feedback → next run (RLHF)
                                                                                 ▼
                                                              retail/price/{sku} (MQTT, retained, daily) ──▶ gateway ──▶ 16x2 LCD / e-ink
                                                                                 ▲
  buyer agents / our storefront ──UCP checkout + offers──▶ ucp-server ──orders──▶ │  (stock, ledger)
  supplier agents (simulator) ◀──UCP checkout as buyer──── agent procurement ─────┘  purchase orders
```

Ownership stays as on `main`: edge = Het, console + API = Rodrigo, agent + simulator = Parth, forecaster = Aruna. `ucp-server` and `storefront-web` are new and unowned.

## 1. Ground rules

| Topic | Rule |
|---|---|
| Base URLs | Console API `http://localhost:8000/api/…` (built). UCP server `https://<store-host>/ucp/v1/…` plus `/.well-known/ucp` (new, separate process, same database). MQTT `mqtt://localhost:1883`, WebSocket `/ws/stream`. |
| Auth | Console: httpOnly session cookie, roles `superadmin · manager · staff · viewer` (built). Services (agent, forecaster) calling REST: `X-Service-Key` header, one key per service in `.env` (new). UCP callers: `UCP-Agent` header, optional RFC 9421 signature (new). Storefront and `/api/public/*`: anonymous, rate-limited. |
| Money | Three representations, never converted in firmware or the browser: MQTT contracts = JSON number, 2 dp (`12.99`); console REST = string (`"12.99"`); UCP = integer minor units with currency (`{"amount":1299,"currency":"USD"}`). Postgres `NUMERIC(10,2)`. |
| Time | UTC ISO-8601 with `Z`. Store timezone lives in `store_settings` and drives "nightly" and "daily". |
| IDs | `product_id` `SKU-001` (contract key), `label_id` `SHLF-0142`, `decision_id` UUID (joins proposal → explanation → price → audit), new records UUID. |
| Lists | `{rows, total, page, page_size, pages}` with `page`, `page_size` ≤ 200 (built). |
| Errors | `{"detail": "..."}` with 400/401/403/404/409 (built). 409 on any verdict for a decision that is no longer pending. |
| Stream | `/ws/stream` carries hints to refetch, never data (built). New hint types: `decision`, `order`, `purchase_order`, `offer`, `run`. |
| Changing contracts | `contracts/*.schema.json` and `infra/postgres/schema.sql` need four approvals. Everything else is a solo PR. Additive first: optional fields, new topics, new endpoints. |

---

## 2. Hardware team: shelf labels (MQTT only)

Nothing here calls REST. The gateway (MSP432 + Wi-Fi module for the 16x2 LCD; ESP32 for the Heltec E213 e-ink) speaks four topics.

| Topic | Dir | QoS | Retained | Status | Payload |
|---|---|---|---|---|---|
| `retail/price/{product_id}` | in | 1 | yes | **built, contract** | `PriceUpdate` (`contracts/price.schema.json`) |
| `retail/shelf/{label_id}` | out | 1 | no | **new, contract** | `ShelfReport` below. Closes the "confirmed at shelf" gap the README lists. |
| `retail/label/{label_id}/{command}` | in | 1 | no | **built, not a contract yet** | `{label_id, command, ...args, requested_by, ts}`; commands `blink {seconds}`, `refresh {}` |
| `retail/guardrail/+`, `retail/proposal/+`, `retail/explain/+`, `retail/inventory/+` | — | — | — | built | **Ignore.** Not for firmware. |

Price payload the label renders (unchanged):

```json
{"product_id":"SKU-001","price":12.99,"currency":"USD","decision_id":"9f2c…","reason_tag":"Competitor up",
 "approval":"manager","approved_by":"rchen","agent_version":"stub-0.1","ts":"2026-10-14T06:00:00Z"}
```

LCD: line 1 = `display_name` (sent separately? No: the gateway keeps a `product_id → display_name` map it receives on pairing, see below), line 2 = price + `reason_tag` (≤ 16 chars, enforced at the publisher). E-ink: free layout; recommended name, price, `reason_tag`, and a QR to `https://<store-host>/p/{product_id}` (the storefront's "why this price" page).

Pairing: `POST /api/labels/{label_id}/pair` (console, built) publishes a retained `retail/label/{label_id}/pair` `{label_id, product_id, display_name, kind, ts}` so a rebooting label knows its product and line 1 without a database. **new, contract.**

`ShelfReport` (what the label says back):

```json
{"label_id":"SHLF-0142","product_id":"SKU-001","decision_id":"9f2c…","price":12.99,"rendered_at":"2026-10-14T06:00:02Z",
 "rssi_dbm":-61,"battery_pct":87,"firmware":"lcd-0.3.1","ts":"2026-10-14T06:00:02Z"}
```

Send it after every render (with `decision_id` and `price`) and as a heartbeat every 60 s (with `decision_id: null`, `price: null`). `dashboard-api` turns the first into `label_pushes.result = confirmed` and the second into `last_seen_at`; the fake publisher's direct database writes go away.

Daily cadence: approved prices are published at `store_settings.shelf_update_time` (default 06:00 store time) in one batch, plus immediately for manual changes. Because `retail/price` is retained, a label that boots at any time receives the current price without waiting. Nothing else about the firmware changes between "real time" and "daily".

Done when: boot shows the retained price within 3 s; a price publish produces one `ShelfReport` with the matching `decision_id`; heartbeat every 60 s; `blink` and `refresh` answered; power loss shows the last price from flash.

---

## 3. Frontend team: console and storefront

### 3.1 Console (`services/dashboard-web`, built) — new screens and the endpoints behind them

| Screen | Endpoints | Stream hints |
|---|---|---|
| **Decisions inbox** (replaces Suggestions as the home of approvals; price rows keep their current UI) | `GET /api/decisions?status=pending&kind=`, `GET /api/decisions/{id}`, `POST /api/decisions/{id}/verdict` | `decision` |
| Decision detail | explanation block (§4.3), evidence links to market observations, forecast, order or offer | |
| Agent | `GET /api/agent/runs`, `GET /api/agent/runs/{id}`, `POST /api/agent/runs` (run now), `GET /api/rules` (+`autonomy`), `PUT /api/rules/autonomy` | `run`, `rules` |
| Market | `GET /api/market/observations?product_id=` on Product detail; `GET /api/market/summary` | |
| Orders | `GET /api/orders?status=&channel=`, `GET /api/orders/{id}`, `POST /api/orders/{id}/events` | `order` |
| Offers (bargaining) | `GET /api/offers?status=`, offer rows link to their `deal` decision | `offer` |
| Purchase orders | `GET /api/purchase-orders`, `GET /api/purchase-orders/{id}`, `POST /api/purchase-orders/{id}/receive` | `purchase_order` |
| Inventory | `GET /api/inventory/movements?product_id=`, `POST /api/inventory/movements` (staff adjustments) | `products` |
| Accounting | `GET /api/accounting/summary?from&to&group_by=`, `GET /api/accounting/ledger?from&to` | |
| Settings (+) | `PUT /api/settings/store` gains `shelf_update_time`, `nightly_run_time`, `storefront_url` | `settings` |

Everything already built stays: Dashboard, Suggestions (now `kind=price` of the inbox), Products, Labels, Locations, Activity, Rules, Users, Settings.

Rendering rules carried over: money is a string, null is an em dash, "approved" ≠ "on the shelf" until a `ShelfReport` confirms, a decision without an explanation is a normal state.

### 3.2 Storefront (`services/storefront-web`, new, unowned)

A public React app. It is **a UCP client of our own server**, so there is no second commerce API to build or maintain:

| Need | Call |
|---|---|
| Browse, search | UCP catalog `search` and `lookup` (§5.2) |
| Cart and checkout | UCP checkout sessions (§5.3) with the test payment handler |
| Ask for a bulk price | UCP negotiation offers (§5.4) |
| "Why is it this price?" | `GET /api/public/products/{product_id}/why` → `{price, changed_at, reason_tag, sentence}` (new, anonymous, jargon-free sentence composed by `dashboard-api` from the decision's explanation) |
| Order status | `GET /ucp/v1/orders/{id}` with the buyer token from checkout |

---

## 4. Backend team: `dashboard-api` by functionality

### 4.1 Auth, users, settings, meta, activity — built, unchanged

`/api/auth/{signin,signout,me,setup,invite/{token},invite/accept}` · `/api/users…` · `/api/settings/store`, `/api/settings/mqtt/test` · `/api/meta` · `/api/dashboard` · `/api/activity`, `/api/activity/export.csv`, `/api/activity/audit`.

New service principal: requests with a valid `X-Service-Key` act as user `agent:<service>` with the rights listed per endpoint below. New columns on `store_settings`: `shelf_update_time TIME`, `nightly_run_time TIME`, `storefront_url TEXT`, `ucp_enabled BOOL`.

### 4.2 Catalog, labels, locations — built, plus inventory movements

Built: `/api/products…`, `/api/products/facets`, `/api/products/{id}/price` (manual price), `/api/labels…`, `/api/locations…`, `/api/rules…`.

New, so stock is a ledger rather than a number staff overwrite:

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/api/inventory/movements?product_id=&since=` | viewer+ | |
| POST | `/api/inventory/movements` | staff+, service | `{product_id, type: sale \| receipt \| adjustment \| return \| shrink, quantity (signed), ref: {order_id \| po_id \| note}}`; updates `products.stock_on_hand` in the same transaction |

Sales and receipts are written automatically by orders (§4.5) and purchase orders (§4.6); staff post adjustments and shrink.

Product additions: `online BOOL` (listed on the storefront and UCP catalog), `description TEXT`, `images JSONB`, `online_price NUMERIC(10,2) NULL` (null = same as shelf). `suppliers` becomes a table: `{supplier_id, name, contact, ucp_profile_url, payment_terms, lead_time_days}`; `products.supplier` becomes a foreign key.

### 4.3 Decisions inbox and feedback — new (generalizes the built review queue)

One inbox for everything the agent wants to do. Price decisions keep arriving over MQTT (`retail/proposal`, built, contract) and appear here as `kind: price`; `/api/suggestions…` keeps working as the `kind=price` view. Other kinds arrive over REST from the agent.

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/api/decisions?status=&kind=&run_id=&page=` | viewer+ | default `status=pending`, soonest `expires_at` first |
| GET | `/api/decisions/{decision_id}` | viewer+ | full object below |
| POST | `/api/decisions/{decision_id}/verdict` | manager+ | `{action: approve \| override \| reject, changes?: {…kind-specific}, reason?: string ≤ 500, tags?: [string]}`; 409 unless pending |
| POST | `/api/agent/decisions` | service | agent submits a non-price decision (kinds `purchase_order`, `deal`, `listing`); returns it with `gate` evaluated and, if auto, already applied |
| POST | `/api/decisions/{decision_id}/outcome` | service | `{metrics: {…}, measured_at}`; the nightly run writes yesterday's results (units sold, margin, sell-through) against yesterday's decisions |
| GET | `/api/agent/feedback?since=&kind=` | service | the RLHF export: every decided decision with `proposal`, `context`, `explanation`, `verdict`, `outcome` |

Decision object:

```json
{
  "decision_id": "9f2c…", "kind": "price", "status": "pending",
  "subject": {"product_id": "SKU-001"},
  "proposal": {"proposed_price": "12.99", "current_price": "12.50", "reason_tag": "Competitor up", "channel": "shelf+online"},
  "context": {"stock_on_hand": 18, "dead_stock_risk": 0.21, "competitor_min": "12.49", "season": "fall", "last_7d_units": 41},
  "explanation": {
    "summary": "Competitor B raised milk to $13.29 yesterday; our stock covers 4 days, so a $0.49 rise keeps us cheapest and lifts margin.",
    "factors": [{"name": "competitor_price", "effect": "+0.62", "weight": 0.41}, {"name": "inventory_level", "effect": "-0.41", "weight": 0.27}],
    "evidence": [{"type": "market_observation", "id": "obs_…"}, {"type": "forecast", "id": "…"}],
    "shap": {"base_value": 12.5, "shap": [{"feature": "competitor_price", "value": 0.62}]},
    "confidence": 0.82
  },
  "gate": {"auto": false, "reasons": ["A 3.9% move exceeds the 2% you delegated."]},
  "verdict": null,
  "outcome": null,
  "run_id": "run_…", "agent_version": "ddpg-2026-10-10", "expires_at": "2026-10-14T10:00:00Z", "created_at": "2026-10-14T02:14:08Z"
}
```

Kinds and their `proposal` / `changes` shapes:

| `kind` | `proposal` | `changes` on override | Applied by |
|---|---|---|---|
| `price` | built `PriceProposal` fields | `{price}` | built gate → `retail/price` |
| `purchase_order` | `{supplier_id, lines: [{product_id, quantity, unit_cost}], total, deliver_by, offer_id?}` | `{lines, deliver_by}` | creates a purchase order (§4.6) and completes the supplier checkout if one is open |
| `deal` | `{offer_id, buyer, lines: [{product_id, quantity, list_unit_price, offered_unit_price}], counter_unit_price?, margin_pct_after}` | `{counter_unit_price}` or `{accept: true}` | answers the UCP offer (§5.4) |
| `listing` | `{product_id, action: list \| delist \| update, fields: {description?, online_price?, images?}}` | `{fields}` | updates the product |

`explanation.shap` is present only for `kind: price` from the RL model; the rest explain with `summary`, `factors`, `evidence`. `summary` is one or two sentences, no model terms. `factors[].effect` is in money for prices and in percent or units otherwise.

Verdict `tags` vocabulary (the part of the feedback that is learnable without reading prose): `too_aggressive`, `too_timid`, `bad_timing`, `wrong_supplier`, `margin_too_low`, `stock_risk`, `explanation_unclear`, `agree`.

Gate for non-price kinds uses `rules.autonomy` (§4.4): inside the limits → `status: auto_approved`, applied, audited as `approved_by: policy`; otherwise pending until `expires_at`, then `expired`.

### 4.4 Agent runs, autonomy policy, market research — new

| Method | Path | Who | Notes |
|---|---|---|---|
| POST | `/api/agent/runs` | manager+, service | manager: run now → publishes `retail/agent/cmd` `{command: "run", run_id, requested_by, ts}`; service: registers a run it started |
| GET | `/api/agent/runs?page=` | viewer+ | |
| GET | `/api/agent/runs/{run_id}` | viewer+ | steps, counts, produced decision ids |
| PATCH | `/api/agent/runs/{run_id}` | service | `{status: running \| succeeded \| failed, steps: [{name, status, summary, started_at, finished_at}], summary}` |
| PUT | `/api/rules/autonomy` | manager+ | `{purchase_order: {mode: ask \| auto, auto_max_total: "500.00"}, deal: {mode, min_margin_pct, max_discount_pct, min_quantity}, listing: {mode}}`; `price` stays per-SKU guardrail + category rules (built) |
| POST | `/api/market/observations` | service | `{items: [{product_id, source: web \| supplier \| manual, competitor, price, currency, url, observed_at}]}` |
| GET | `/api/market/observations?product_id=&since=` | viewer+ | |
| GET | `/api/market/summary` | viewer+ | per product: our price, min and median competitor, position, last observed |

Run steps are fixed names so the Agent screen can render them: `review_outcomes`, `market_research`, `forecast_sync`, `pricing`, `procurement`, `listings`, `accounting_close`.

### 4.5 Orders and fulfillment — new

Orders are created only by `ucp-server` (§5) from completed checkout sessions. The order object **is** the UCP order object (§5.5) plus `channel: ucp | storefront`, `buyer_agent` (the `UCP-Agent` profile URL), `created_at`.

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/api/orders?status=&channel=&since=&page=` | viewer+ | status derived from line items: `processing · partial · fulfilled · canceled` |
| GET | `/api/orders/{order_id}` | viewer+ | |
| POST | `/api/orders/{order_id}/events` | staff+ | `{type: shipped \| delivered \| canceled \| returned_to_sender, line_items: [{id, quantity}], tracking_number?}`; appends a fulfillment event, posts the order webhook (§5.5), writes `sale` or `return` inventory movements and ledger entries |

### 4.6 Procurement — new

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/api/purchase-orders?status=&page=` | viewer+ | `draft · ordered · partially_received · received · canceled` |
| GET | `/api/purchase-orders/{po_id}` | viewer+ | lines, supplier, linked decision, supplier order id |
| POST | `/api/purchase-orders` | manager+ | manual PO `{supplier_id, lines, deliver_by}`; bypasses the agent |
| POST | `/api/purchase-orders/{po_id}/receive` | staff+ | `{lines: [{product_id, quantity_received}]}` → `receipt` movements, `purchase` ledger entry, status update |
| POST | `/api/purchase-orders/{po_id}/cancel` | manager+ | |

Agent-originated POs are created by approving a `purchase_order` decision; the agent then completes the supplier-side UCP checkout (§5.6) and patches `supplier_order_id`.

### 4.7 Accounting — new

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/api/accounting/summary?from&to&group_by=day \| category \| channel` | manager+ | `{revenue, cogs, gross_profit, margin_pct, orders, units, refunds, purchases, groups: [...]}`; strings for money |
| GET | `/api/accounting/ledger?from&to&type=&page=` | manager+ | entries `{entry_id, ts, type: sale \| refund \| purchase \| adjustment, amount, product_id?, order_id?, po_id?, memo}` |

Entries are written by order events and PO receipts; nothing posts to the ledger by hand except `adjustment`.

### 4.8 Tables to add

`decisions` (generalizes `proposals`; price rows mirror into it), `decision_outcomes`, `agent_runs`, `market_observations`, `suppliers`, `inventory_movements`, `orders`, `order_line_items`, `order_events`, `checkout_sessions`, `offers`, `purchase_orders`, `purchase_order_lines`, `ledger_entries`, plus the `products` and `store_settings` columns in §4.1 and §4.2. Add them in a new `infra/postgres/04-commerce.sql`; `01-schema.sql` is untouched.

---

## 5. UCP server: agentic commerce (new service `services/ucp-server`)

Implements the Universal Commerce Protocol (`ucp.dev`, version `2026-08-25`) so any UCP-speaking agent can buy from the store, bargain for bulk, and track orders, and so our own agent can do the same against suppliers. FastAPI, same database, public HTTPS. Amounts are integer minor units everywhere in this section.

### 5.1 Discovery — `GET /.well-known/ucp`

```json
{"ucp": {
  "version": "2026-08-25",
  "services": {"dev.ucp.shopping": {"version": "2026-08-25", "spec": "https://ucp.dev/latest/specification/overview/",
                "transport": "rest", "endpoint": "https://<store-host>/ucp/v1", "schema": "https://ucp.dev/latest/schema/..."}},
  "capabilities": {
    "dev.ucp.shopping.catalog.search": [{"version": "2026-08-25", "spec": "…", "schema": "…"}],
    "dev.ucp.shopping.catalog.lookup": [{"version": "2026-08-25", "spec": "…", "schema": "…"}],
    "dev.ucp.shopping.checkout":       [{"version": "2026-08-25", "spec": "…", "schema": "…"}],
    "dev.ucp.shopping.order":          [{"version": "2026-08-25", "spec": "…", "schema": "…"}],
    "<ns>.shelfiq.negotiation":        [{"version": "2026-10-14", "spec": "<repo>/api-spec/README.md#54-negotiation-extension",
                                         "schema": "https://<ns-host>/schemas/negotiation.json", "extends": "dev.ucp.shopping.checkout"}]
  },
  "payment_handlers": {"<ns>.shelfiq.test_payment": [{"id": "test_1", "version": "2026-10-14", "spec": "…", "schema": "…", "available_instruments": ["card"]}]}
 },
 "keys": []}
```

`<ns>` must be the reverse of a domain we control that also hosts the schema file (UCP rejects a schema whose host does not match the namespace). Proposed: `io.github.rodarkhen` with the schema on GitHub Pages. **Open question O1.**

Required request headers on every `/ucp/v1` call: `UCP-Agent: profile="<url>"`, `Idempotency-Key`, `Request-Id`. Responses carry the `ucp` envelope `{version, capabilities}`. Errors use UCP `messages[]` (`type`, `code`, `path`, `content`, `severity`).

### 5.2 Catalog

Capabilities `dev.ucp.shopping.catalog.search` and `.lookup`. Product fields served from `products` where `online = true`: `id` (= `product_id`), `title`, `description`, `url` (`<storefront_url>/p/{id}`), `categories`, `price_range`, `media`, `variants: [{id (= product_id), sku, price: {amount, currency}, availability: {available}, metadata: {reason_tag, priced_at}}]`. Price shown is `online_price` if set, else the shelf price. **REST paths follow the UCP catalog REST binding page; confirm them against the spec before implementing** (the overview confirms identifiers and object shapes, not routes).

### 5.3 Checkout (confirmed REST binding)

| Method | Path | Does |
|---|---|---|
| POST | `/ucp/v1/checkout-sessions` | `{line_items: [{item: {id}, quantity}]}` → session `status: incomplete` with `line_items[].item.price`, `totals[]` (`subtotal`, `tax`, `total`), `messages[]` asking for `buyer` and `fulfillment` |
| GET | `/ucp/v1/checkout-sessions/{id}` | |
| PUT | `/ucp/v1/checkout-sessions/{id}` | buyer, fulfillment destination and option, quantities → `ready_for_complete` when nothing is missing |
| POST | `/ucp/v1/checkout-sessions/{id}/complete` | `{payment: {instruments: [...]}, signals}` with the test handler → reserves stock, creates the order, returns `status: completed`, `order: {id, permalink_url}` |
| POST | `/ucp/v1/checkout-sessions/{id}/cancel` | |

Stock rule: a line whose quantity exceeds `stock_on_hand` gets `code: quantity_adjusted` (or `out_of_stock`) in `messages[]`. Fulfillment methods offered: `pickup` and `shipping` (flat rate from settings). Sessions expire after 60 minutes.

### 5.4 Negotiation extension (ours) — `<ns>.shelfiq.negotiation`, extends checkout

The bargaining feature. A buyer agent makes an offer on an open checkout session; the store's agent answers inside the owner's policy or escalates to the owner.

| Method | Path | Does |
|---|---|---|
| POST | `/ucp/v1/checkout-sessions/{id}/offers` | `{line_items: [{id, quantity, unit_price: {amount, currency}}], terms: {deliver_by?, payment_terms?}, note?, expires_at}` → offer |
| GET | `/ucp/v1/checkout-sessions/{id}/offers/{offer_id}` | poll; `Retry-After` while `pending_review` |
| POST | `/ucp/v1/checkout-sessions/{id}/offers/{offer_id}/accept` | buyer accepts the store's counter |
| POST | `/ucp/v1/checkout-sessions/{id}/offers/{offer_id}/withdraw` | |

Offer object: `{id, status: open | countered | pending_review | accepted | declined | expired | withdrawn, line_items, counter: {line_items} | null, decided_by: policy | owner | null, response_note, expires_at}`.

Store-side rule: compute margin after the offer; if within `rules.autonomy.deal` (`min_margin_pct`, `max_discount_pct`, `min_quantity`) the server accepts or counters at once (`decided_by: policy`); otherwise it opens a `deal` decision (§4.3) and the offer is `pending_review` until the owner's verdict or `deal_review_timeout` (default 2 h → `declined`). An accepted offer rewrites the affected `line_items[].item.price` and adds a `{type: "discount", amount}` total on the session; the session then completes normally. Every accepted deal is an order with `channel: ucp` and a ledger entry at the negotiated price.

### 5.5 Orders and webhooks (confirmed)

`GET /ucp/v1/orders/{id}` returns the order snapshot: `{ucp, id, checkout_id, permalink_url, currency, line_items: [{id, item, quantity: {original, total, fulfilled}, totals, status: processing | partial | fulfilled | removed}], fulfillment: {expectations, events: [{id, occurred_at, type, line_items, tracking_number}]}, totals, adjustments?}`.

On every change `ucp-server` POSTs the full snapshot to the platform's `webhook_url` (taken from the caller's `dev.ucp.shopping.order` capability config) with `Webhook-Id`, `Webhook-Timestamp`, and an RFC 9421 signature. Fulfillment event types used: `processing`, `shipped`, `delivered`, `canceled`, `returned_to_sender`. Our own storefront registers a webhook too, so it is treated like any platform.

### 5.6 Procurement: the store as a UCP buyer

The agent service uses the same protocol in the other direction. For each `suppliers.ucp_profile_url`: read `/.well-known/ucp`, `lookup` the supplier's items, open a checkout session, and if the supplier advertises `<ns>.shelfiq.negotiation` make an offer for the period's quantity. The resulting cart becomes a `purchase_order` decision (§4.3). On approval the agent `complete`s the supplier checkout and stores `supplier_order_id`; supplier order webhooks land on `POST /ucp/v1/webhooks/orders` (our inbound endpoint, verified by signature) and advance the PO.

For development and the demo, `services/simulator` runs a supplier UCP server with the same capability set, so procurement and negotiation are testable with no third party.

### 5.7 Security

TLS 1.3 only; `UCP-Agent` required; signature verification optional in development, required in any public deployment; checkout and order ids are unguessable; buyer access to an order needs the token issued at completion; rate limit per `UCP-Agent` profile.

---

## 6. AI team: agent, forecaster, simulator

### 6.1 Agent (`services/pricing-agent`, grows into the store agent)

Inputs: the feedback export (`GET /api/agent/feedback`), the catalogue and stock (`GET /api/products`, movements), market observations it gathers itself, forecaster risk (`retail/inventory/+`, built), guardrails (`retail/guardrail/+`, built), autonomy rules (`GET /api/rules`), orders and POs for demand and lead times.

Outputs: price proposals on `retail/proposal/{id}` + `retail/explain/{id}` (built contracts, unchanged; publish them with `expires_at` set to the morning review window), other decisions via `POST /api/agent/decisions`, observations via `POST /api/market/observations`, outcomes via `POST /api/decisions/{id}/outcome`, run progress via `PATCH /api/agent/runs/{id}`, and the shadow topic `retail/debug/pricing` for developers.

The nightly run, at `store_settings.nightly_run_time`, or on `retail/agent/cmd`:

1. `review_outcomes`: measure yesterday's decisions, post outcomes, reload feedback.
2. `market_research`: competitor prices (simulator feed in development; web search tool in production), seasonality calendar; post observations.
3. `forecast_sync`: read the latest risk per SKU.
4. `pricing`: RL policy (DDPG, DQN and rule baseline, selectable by `agent_version`) per SKU → proposals + SHAP explanations. Feedback enters as reward shaping: a rejected or overridden decision of the same shape is a negative term; `tags` map to fixed penalties.
5. `procurement`: sell-through vs lead time → purchase-order decisions, with supplier offers when a supplier speaks UCP.
6. `listings`: list, delist or update online items (dead stock online, out-of-stock delisted).
7. `accounting_close`: nothing to compute; the ledger is written by events. The step just verifies totals and writes the run summary.

The owner reads one summary per run (`agent_runs.summary`, two or three sentences) and then the inbox. That summary plus the per-decision `explanation.summary` are the "neat and crisp explanation" the brief asks for.

### 6.2 Forecaster (`services/forecaster`)

Unchanged contract: `retail/inventory/{id}` (`InventoryRisk`, built). Add `units_on_hand` from inventory movements when available. Optional: `POST /api/market/observations` with `source: forecast` for seasonality notes the agent can cite as evidence.

### 6.3 Simulator (`services/simulator`)

Three roles: the gym environment for training; the demo's demand and competitor feed; and a **supplier UCP server** plus a **buyer agent** so checkout, negotiation and procurement run end to end on one laptop. The fake publisher's label heartbeat moves to the simulator and uses `retail/shelf/{label_id}` once that contract is approved.

---

## 7. Build order

| Phase | Deliverable | Needs |
|---|---|---|
| 1 | `retail/shelf` ack + `pair` topic in `contracts/`; decisions inbox over the built queue; agent runs; market observations; feedback export; nightly run with rule-based pricing | contract approvals for §2 |
| 2 | `ucp-server` discovery + catalog + checkout + orders; storefront; inventory movements; ledger | O1 namespace |
| 3 | negotiation extension; procurement against the simulator's supplier; accounting screen; RL policy consuming feedback | phases 1 and 2 |

## 8. Open questions

| # | Question | Proposed default | Owner |
|---|---|---|---|
| O1 | UCP namespace and schema host for our extension and payment handler | `io.github.rodarkhen.shelfiq.*`, schema on GitHub Pages | Rodrigo |
| O2 | Who owns `ucp-server` and `storefront-web` | split: server with the API owner, storefront with whoever finishes the console stubs first | team |
| O3 | Does the LCD gateway keep a `product_id → display_name` map (needs the `pair` topic) or does line 1 come inside `PriceUpdate` (needs a contract change)? | `pair` topic; `PriceUpdate` stays frozen | Het |
| O4 | Real payment handler for the Expo demo or test handler only | test handler; Stripe test mode via UCP if time allows | team |
| O5 | Where market research runs in production: web search API in the agent, or a separate scraper | in the agent, behind a tool interface, so the simulator can replace it | Parth |
| O6 | Does approval gating of purchases and deals need Chapter 2 wording changes like the price gate did | yes, same paragraph | all + advisor |

## 9. Recommended simplifications (decide before phase 1)

Sections 2 to 6 build on what is already on `main`. Five things on `main` do not fit the v2 scope well, and changing them is cheap now because the agent, forecaster and firmware have not been written. Each would shorten this spec further.

| # | Today | Recommendation | Why | Cost now |
|---|---|---|---|---|
| S1 | The agent talks to the backend over MQTT (`proposal`, `explain`, `guardrail`, `inventory`), which is why "an explanation may never arrive", `superseded`, topic-equals-payload checks and a 30 s housekeeping loop exist. v2 then adds REST for every other decision kind, so the agent needs both transports. | **MQTT for devices only** (`price` out, `shelf` in, label commands). Agent and forecaster use REST: `POST /api/agent/decisions` carries the price proposal **with its explanation inline**; guardrails are `GET /api/rules` at run start; forecaster posts risk to `POST /api/forecasts`. | One transport per direction, synchronous validation errors, no missing-explanation state, curl-testable. The `contracts/` folder shrinks to the two device topics. | Rework `ingest.py` into a router, delete three schemas, repoint `fake_publisher.py` at REST. About a day. |
| S2 | Two label families: 16x2 LCD on an MSP432 that has no Wi-Fi (needs a gateway, design unsettled, part number unconfirmed) and a Heltec E213 e-ink on ESP32. | **One family: the ESP32 e-ink label.** Wi-Fi on die, MQTT direct, holds the image unpowered, room for name, price, reason and a QR to the storefront. Keep the LCD only if the boards are already bought, as a second subscriber with no gateway work of its own. | Removes a gateway, a serial link and a second toolchain. The 16-character `reason_tag` cap stops being a hardware constraint. | Nothing is built yet for either. Zero. |
| S3 | Real-time machinery: proposals expire after `proposal_ttl_hours`, `max_changes_per_day`, `min_hours_between`, a per-SKU guardrail, per-category rules, store-wide limits, and v2's `autonomy` on top. Four policy layers. | **Nightly batch, one policy object.** Decisions expire at the next run or at `shelf_update_time`. Policy is `{kind: {mode, limits}}` with per-category and per-SKU overrides for `price` only. Drop `max_changes_per_day` and `min_hours_between` (one change per day by construction). | Fewer knobs for an owner who is supposed to be freed from this, and one Rules screen instead of three sections plus `autonomy`. | Collapse `change_limits` + `category_rules` + `autonomy` into one table and one `PUT /api/rules`. Half a day. |
| S4 | Money in three forms: number on MQTT, string on REST, integer minor units on UCP. | **Integer minor units everywhere** (`1299`), the UCP convention. The console formats cents; firmware never parses a float; no string parsing anywhere. | One representation; removes the "never parseFloat" rule and the `multipleOf: 0.01` schema checks. | A contract change to `price.schema.json` and `format.ts`. Half a day, while only the fake publisher produces money. |
| S5 | v2 proposes `ucp-server` as a separate process next to `dashboard-api`, and the README lists `edge/`, `notebooks/`, `design_handoff_shelfiq/` that do not exist. | **One backend process** (`services/api`) serving `/api`, `/ucp/v1` and `/.well-known/ucp`; a reverse proxy exposes only the UCP paths publicly. Delete or create the phantom directories. | Four people, one database, one deployment. Split later only if the UCP side needs to scale separately. | None; it is a decision about where new code goes. |

Not worth changing: the session-cookie console auth, the location hierarchy (built and harmless, just do not extend it), `product_id` as `SKU-nnn`, the audit log, the walking-skeleton rule in CONTRIBUTING.
