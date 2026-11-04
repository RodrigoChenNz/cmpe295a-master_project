# ShelfIQ commerce demo

This feature extends the pricing integration on `codex/software-integration`. It
uses the existing API, PostgreSQL database, console and MQTT workers. Main and
the shared project report are untouched. No real payments or purchases occur.

## Start on a teammate's machine

Follow [SOFTWARE.md](SOFTWARE.md) to install dependencies, create `.env`, create
MQTT identities and train/select a checkpoint. Then run:

```sh
make commerce-keys
make software-secure
make commerce-ready
make ready
```

`make commerce-keys` preserves an existing sufficiently long private order-hook
key and otherwise generates one in ignored `.env`. Recreate the API container
after changing environment configuration. Never commit `.env`, `secrets/`,
private keys or checkpoints. Set `PYTHON=.venv/bin/python` when the Python virtual
environment is not activated. On this Mac the API is http://localhost:18000 and
the frontend is http://localhost:5173; a fresh checkout defaults to API port 8000.

`commerce-ready` deliberately creates labelled demo records and opens local test
commerce. It exercises the catalogue, buyer checkout and owner-reviewed offer,
partial shipment/delivery/cancellation/return, simulated supplier purchase,
partial receipts, accounting, feedback, seven agent steps and signed order
notifications. Evidence is written to
`artifacts/verification/commerce-readiness.json`. `make ready` independently
verifies authenticated price publication through a matching emulator render.
An emulator result is not physical-display evidence.

## Show the project

1. Sign in to the staff console. Open **Commerce → Commerce settings**. The
   local test store is enabled by the acceptance script; a manager can close it.
   Closing checkout leaves inventory ledger protection active.
2. Use **Online catalogue and procurement sources** to list products, assign a
   simulated supplier, add descriptions and choose a separate online price or
   leave it blank for shelf-price parity. Online prices obey product bounds.
3. Open `/shop` in another tab. Select quantities, provide a synthetic email and
   choose pickup or shipping. Review totals. Use the test approval/decline
   selector; no card information is accepted and no provider is contacted.
4. Request a bulk discount. Outside delegated limits the offer waits in
   **Decisions inbox → deal**. The owner can approve, reject or send a counter.
   The buyer accepts a counter and completes checkout. The browser session's
   private bearer token controls checkout and order access.
5. In **Orders**, record partial shipments, deliveries, cancellations and
   returns. The buyer order page polls for changes. Stock moves once at sale;
   shipping does not deduct it a second time. Cancellation/return quantities
   are bounded and reverse the captured price, acquisition cost and tax.
6. In **Purchase orders**, create a manual draft and place the simulated supplier
   order, or approve the agent's purchase decision. Agent approval places the
   supplier order transactionally. Record partial receipts. Quantities above
   the outstanding amount fail. Supplier inventory and quotes are separate
   from the store's inventory; cancellation restores unreceived supplier stock.
7. In **Inventory**, record adjustments and shrink with reasons. Stock cannot go
   negative. Existing products receive an opening balance and every subsequent
   commerce stock change has a movement. Simulator observations do not overwrite
   the ledger, and the original product editor refuses direct stock changes.
8. In **Accounting**, inspect net merchandise revenue, captured cost of goods,
   gross profit, refunds and purchases, grouped by UTC day/category/channel.
   Taxes payable, shipping income and inventory adjustments have separate ledger
   accounts. Merchandise gross profit excludes tax and shipping. Shipping is
   explicitly nonrefundable in this demonstration.
9. In **Store agent**, run the review or enable nightly scheduling in the store
   timezone. Inspect all seven step results, recommendations, market observations
   and feedback. A missing dependency fails the run visibly. Persisted steps
   resume after API restart; one active run is permitted across API processes.
10. In **Software status**, check observations, forecasts, pricing-worker liveness
    and the display receiver separately. The pricing heartbeat remains recent
    when the worker deliberately preserves a nightly recommendation for review.

## Agent, policy and feedback

Steps are `review_outcomes`, `market_research`, `forecast_sync`, `pricing`,
`procurement`, `listings`, `accounting_close`. Pricing jobs are persisted and sent
by the configured rule/DQN/DDPG worker through the existing proposal and
explanation contracts. They keep the run-to-decision relationship without
mirroring the price queue into another source of truth. Retries reuse the
same decision id. Ordinary simulator ticks cannot supersede a pending nightly
recommendation. The configured checkpoint supplies the actual model action.

Fixed human feedback penalties damp nightly price moves: `too_aggressive` and
`margin_too_low` contribute 0.3, `bad_timing` and `stock_risk` 0.2,
`explanation_unclear` 0.1, and a rejection 0.1. The mean across up to 20 recent
verdicts is capped at 0.8. The policy wrapper itself is explained by exact
single-reference Shapley values. This changes decision-time behavior; it does
not silently retrain model weights. Offline training and held-out evaluation
remain separate commands. Observed 24-hour sale outcomes are not causal proof
of a pricing benefit or a measured real-customer churn reduction.

Procurement compares seven-day demand to supplier lead time plus three buffer
days, excludes existing open purchases, and uses supplier catalogue quotes.
Listings propose listing available products and delisting out-of-stock ones.
Market import preserves simulator/manual provenance; no synthetic observation
is presented as an external website search result.

Autonomy defaults to **ask** for purchases, deals and listings. Managers can
set bounded automatic purchase totals and deal margin/discount/quantity limits.
Only managers/superadmins cast owner verdicts. Staff can fulfill, receive and
adjust inventory; viewers read. Accounting and feedback export require manager
access. An optional `COMMERCE_AGENT_KEY` of at least 32 characters enables the
separate recommendation/market/feedback service principal. It cannot approve
owner decisions, manage users or authorize real purchases. No service key is
required for the built-in persisted scheduler.

## Price cutover

Immediate publication remains the default. A manager can select **daily shelf
cutover** and a local time. Approvals then queue a publication rather than
claiming the shelf changed. At cutover the latest approval per product wins.
Publication rechecks its reference price and current price bounds; stale work is
canceled with a recorded reason. A manual product price remains immediate. The
cutover uses store-local calendar time; a nonexistent DST time resolves via its
UTC instant, and a repeated time uses the first occurrence. Keep approval,
publication, and physical confirmation distinct in the presentation.

## API and development protocol boundary

The existing console remains `/api/*`; new groups are `/api/commerce`,
`/api/decisions`, `/api/agent`, `/api/market`, `/api/suppliers`, `/api/orders`,
`/api/purchase-orders`, `/api/inventory`, `/api/accounting`, `/api/offers` and
`/api/rules/autonomy`. OpenAPI is at `/docs`. Mutation clients send a unique
`Idempotency-Key`; reusing a key with changed content returns 409. The cache is
scoped to the authenticated caller, method, path and payload hash.

The browser's local development binding is `/api/storefront/*`. The public
`/ucp/v1/*` adapter separately implements pinned standard catalog, checkout,
fulfillment and order messages with signed platform negotiation. Discovery is
`/.well-known/ucp`. See [DEPLOYMENT.md](DEPLOYMENT.md) for the isolated HTTPS
acceptance stack, supported protocol scope, domain setup and schema limitations.
Client-generated random buyer tokens are hashed in storage. Platform signatures
and buyer ownership are checked separately.

The supplier simulator is an in-process business adapter with an independent
catalogue, stock and immutable checkout quote. Its authenticated inspection
routes are `/api/simulator/supplier/{supplier_id}/catalog`,
`/api/simulator/supplier/checkouts/{checkout_id}` and
`/api/simulator/supplier/orders/{order_id}`. Procurement uses the same business
operations transactionally. It is not a connection to a real UCP supplier.

## Signed order notifications

Completion and fulfillment append full buyer-visible order snapshots to a
transactional outbox; acquisition costs are removed. With
`COMMERCE_WEBHOOK_KEY` configured, the worker signs POST notifications using an
[RFC 9421](https://www.rfc-editor.org/rfc/rfc9421.html) HMAC-SHA256 profile covering
method, target URI, Content-Digest, Webhook-Id and Webhook-Timestamp. The receiver
rejects altered/expired signatures, accepts identical duplicates once and
rejects event-id reuse with changed content. Failed deliveries remain queued.

With no `COMMERCE_WEBHOOK_URL`, delivery goes through the ASGI HTTP adapter to
the local signed receiver. An operator can configure a fixed HTTPS receiver URL
with an out-of-band shared key. External delivery requires TLS 1.3 and does not
follow redirects. The buyer cannot choose this destination. This is a private
shared-key profile, not public-key platform discovery. The status endpoint shows
whether hooks are configured and how many are queued/delivered.

## Remaining acceptance outside this local demo

- Physical board/gateway work requires the teammate's hardware, compatible
  publication identity and actual render acknowledgements. See the PR review
  and hardware handoff; do not merge PR #5's price-hash ACK logic unchanged.
- Public hosting, an owned namespace domain, live DNS/certificate issuance and
  external platform acceptance remain pending. The signed HTTPS binding is tested
  locally; no certification or arbitrary-platform compatibility is claimed.
- Real supplier integration, real payment processing and external market
  research credentials are not configured. The proposed demo uses a supplier
  simulator and test payments. No measured real-store profit/churn claims exist.

These boundaries must remain explicit in the report and presentation. Green
local tests do not imply physical acceptance, public interoperability or team
approval to merge into main.
