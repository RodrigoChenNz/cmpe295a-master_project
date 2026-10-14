# dashboard-api — Layer 5

**Owner:** Rodrigo Chen · **Status:** built and running

FastAPI service. **The control point of the system**, not a read model: it is
the only publisher of `retail/price/`, and it publishes only what a manager
approved or what the manager's standing policy auto-approved.

Python 3.11 · FastAPI · asyncpg · pydantic v2 · aiomqtt · argon2.

```bash
make up                       # brings this up with everything else
curl localhost:8000/health
open http://localhost:8000/docs
```

## What it does

- Subscribes `retail/proposal/+`, `retail/explain/+`, `retail/inventory/+`
- Runs **the decision gate** — inside policy → apply; outside → queue for a human
- Publishes `retail/price/+` and `retail/guardrail/+`, and nothing else does
- Expires lapsed proposals, settles unacknowledged label pushes, and ages unseen
  labels to offline, all on one 30-second timer
- Persists everything, including an audit row per decision
- Serves the queue, catalogue, labels, activity, rules and users over REST, and
  streams change hints over `/ws/stream`

## The gate

`app/gate.py`. The rule is from `contracts/README.md`, applied verbatim:

```
min_price <= proposed_price <= max_price
|proposed_price − current_price| / current_price × 100 <= auto_approve_pct
```

`auto_approve_pct` of `NULL` means always ask. `evaluate()` is pure, so it can be
tested without a database or a broker.

**`publish_price` is the only function in this codebase that publishes a price.**
It writes the row first and publishes second — on purpose. A crash between the
two leaves a price in the audit log that never reached a shelf, which the
label-push row shows as unsettled. The opposite order would put a price on a
shelf with no record of who approved it, and that is the one failure this
project cannot have.

When the broker is down, the message is queued and the caller is told
`published: false`, so the UI says "queued" rather than claiming success.

## Authentication is load-bearing

The project's central claim is that every price change has a name against it.
So `price_history.approved_by` and `proposals.decided_by` are taken from the
signed-in user **and from nowhere else** — there is no header, query parameter
or body field anywhere in this service that can set them.

Sessions are server-side rows, not self-contained tokens, so deactivating a user
takes effect on their next request rather than whenever their token expires.
The cookie is httpOnly and carries only an id.

**No committed file in this repo contains a working credential.**

- **First run (`make up`).** On an empty database `GET /api/auth/me` reports
  `setup_required: true`, and `POST /api/auth/setup` creates the first account
  as **superadmin**. It works exactly once: it takes a table lock, and refuses
  with a 409 once any user exists.
- **Demo (`make demo`).** The seed gives every user an unusable `'!'` hash. On
  startup the service sets a real Argon2id hash for `SHELFIQ_BOOTSTRAP_USER` from
  `SHELFIQ_BOOTSTRAP_PASSWORD` and makes that user superadmin. Only the demo
  overlay passes these variables.
- **Everyone else is invited.** `POST /api/users` returns a `/join?token=…`
  link. Only its SHA-256 is stored, in `user_invites`. It expires in seven days
  and is deleted once used: `POST /api/auth/invite/accept` sets the invitee's
  own password and signs them in. `POST /api/users/{username}/invite` issues a
  new link for someone who has not joined yet.

**Roles**, most privileged first: `superadmin`, `manager`, `staff`, `viewer`.
`require_manager` admits superadmin and manager; `require_editor` admits staff
as well. The **rank rule** in `routers/users.py`: you manage only accounts
below your own role and grant only roles below it, but a superadmin manages
everyone. The last active superadmin cannot be demoted or deactivated, and
nobody can lower their own role.

The schema reaches a database only when its volume is first created. If you
start the service on an older database, it stops at startup and tells you to
run `make fresh`.

## Schema

- [`infra/postgres/schema.sql`](../../infra/postgres/schema.sql) — mirrors
  `contracts/`. Changing it needs all four approvals.
- [`infra/postgres/02-shelfiq.sql`](../../infra/postgres/02-shelfiq.sql) —
  console-side state the bus has never carried: the catalogue a human maintains,
  shelf labels, locations, users, per-category pricing rules. Additive; a solo
  PR.
  - **Layout levels.** `location_levels` holds the store's own level names, top
    first (Zone › Aisle › Bay, or Floor › … for several floors).
    - A `locations` row has a `depth`, and a child is always exactly one level
      below its parent. Positions (`location_slots`) attach to the lowest level
      only. All of this is enforced in `routers/locations.py`.
    - `PUT /api/locations/levels` renames levels at any time. It changes how
      many there are only while no location exists.
    - `POST /api/products` returns 409 until levels exist.
  - **Product locations.** `products.location_id` is optional and may point at
    any level. Products are entered first and linked afterwards, through
    `POST /api/products/bulk/location`.
    - `GET /api/products?location=<id>` matches the whole subtree, and
      `?location=unassigned` matches products with no location.
    - The `shelfiq_location_paths` view gives every location's full path.
- [`infra/postgres/03-seed.sql`](../../infra/postgres/03-seed.sql) — the design
  prototype's sample catalogue, generated by `tools/gen_seed.py`. Applied only
  under `make demo`, never by `make up`. Every
  timestamp is relative to `now()`, so a fresh clone comes up with a live-looking
  store rather than a queue that expired on the day the file was written.

## Three things the contract does not carry

Worth raising with the team rather than working around further:

1. **`confidence` and a prose `rationale` on a proposal.** The design's
   suggestions table shows both. `contracts/proposal.schema.json` has neither, so
   they are nullable columns the API fills only when a publisher actually sends
   them. When absent, the UI says "Not reported" and composes a sentence from
   the reason tag plus the SHAP vector, marked as composed. Adding them as
   optional fields would be backward-compatible.
2. **A label acknowledgement topic.** There is no message on which a label says
   it is alive or that it rendered a price, so "confirmed" can only ever mean
   "we published it and heard no complaint", and label liveness is inferred from
   silence. A `retail/shelf/{label_id}` carrying
   `{label_id, decision_id, rendered_at, rssi, battery}` would buy a real third
   state — *confirmed at shelf* / *sent, unconfirmed* / *shelf offline* — which
   is both more honest and a better demo than a mirror that is always green.

3. **A command topic for the labels themselves.** `contracts/` describes the
   price path and nothing in the other direction, but Shelf labels needs one
   physical act: make *this* label flash so someone can find it on a four-metre
   run of shelving. It is published as `retail/label/{label_id}/blink`, qos 1,
   `retain=False`, through `gate.publish_label_command()` — deliberately named
   as a command rather than folded into the price path, and deliberately not
   retained, since a retained blink would re-fire on every reconnect forever.
   It is **not a contract message**; if label commands are going to grow
   (identify, reboot, firmware pin) they want a schema before the second one.

Until (2) exists, `tools/fake_publisher.py` writes label sightings straight to
Postgres and says so in its docstring. It is the one thing in that tool that is
not a contract message.

## Layout

```
app/
  main.py        lifespan, routers, the housekeeping timer, /health
  config.py      settings, all from the environment
  db.py          pool, JSON codecs, the money helpers
  auth.py        argon2, sessions, roles, the bootstrap user
  bus.py         MQTT in both directions, plus the event hub behind /ws/stream
  gate.py        the decision gate and the only publisher of retail/price/
  ingest.py      inbound messages, the expiry timer, label settling
  serialize.py   row → JSON, and why money leaves here as a string
  routers/       auth, meta, dashboard, suggestions, products, labels,
                 activity, rules, users, stream
```

Build it with [`docs/prompts/web-layer.md`](../../docs/prompts/web-layer.md).
