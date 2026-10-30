# Contracts

The five layers of this system never call each other's functions. They exchange JSON over MQTT.
These schemas **are** the interface between them, and they are the only files in this repo that
four people depend on simultaneously.

## The agent recommends; a human decides

The pricing agent does **not** set prices. It publishes recommendations. `dashboard-api` is
the only publisher of `retail/price/`, and it publishes only what a manager approved or what
the manager's standing policy auto-approved. Nothing reaches a shelf without passing that
gate.

```
pricing-agent ──proposal──▶ dashboard-api ──▶ inside policy? ──yes──▶ retail/price ──▶ shelf
                                  │                  │
                                  │                  no
                                  │                  ▼
                                  │            review queue ──▶ manager approves,
                                  │                              overrides, or rejects
                                  ▼
                            expires unacted → shelf keeps its price
```

This is what makes the explanation load-bearing rather than decorative: the manager reads it
*before* deciding, not after the price has already moved.

## Topics

| Topic | Payload | Publisher | Subscribers | QoS | Retained |
|---|---|---|---|---|---|
| `retail/proposal/{product_id}` | [PriceProposal](proposal.schema.json) | pricing-agent | dashboard-api | 1 | no |
| `retail/explain/{product_id}` | [ExplainVector](explain.schema.json) | pricing-agent (XAI module) | dashboard-api | 1 | no |
| `retail/price/{product_id}` | [PriceUpdate](price.schema.json) | **dashboard-api only** | edge gateway, pricing-agent | 1 | yes |
| `retail/inventory/{product_id}` | [InventoryRisk](inventory.schema.json) | forecaster | dashboard-api, pricing-agent | 0 | yes |
| `retail/guardrail/{product_id}` | [PricingPolicy](guardrail.schema.json) | dashboard-api | pricing-agent | 1 | yes |
| `retail/shelf/{label_id}` | [ShelfReport](shelf.schema.json) **draft** | edge (Layer 2) | dashboard-api | 1 | no |

`{product_id}` in the topic must equal `product_id` in the payload. Subscribe with
`retail/price/+` for all SKUs.

The agent subscribes to `retail/price/` as well as publishing proposals — that is how it
learns whether its recommendation was taken, which is the feedback signal it needs and, as a
ratio per `agent_version`, an evaluation result worth reporting.

## The auto-approve rule

`dashboard-api` applies a proposal without a human if and only if **both** hold:

```
min_price <= proposed_price <= max_price
|proposed_price − current_price| / current_price × 100 <= auto_approve_pct
```

`auto_approve_pct` of `null` means always ask. Anything failing either test goes to the
review queue. An auto-approved price is still fully audited — `approval: "auto"`,
`approved_by: "policy"` — and still appears in the log the manager reviews.

All payloads are UTF-8 JSON. All timestamps are UTC ISO-8601 (`2026-09-17T10:00:00Z`). All money is
two decimal places and must be parsed as decimal, never binary float.

## `decision_id` ties it together

A recommendation emits a `PriceProposal` and, shortly after, an `ExplainVector` carrying the same
`decision_id`. If the proposal is approved, that same `decision_id` rides onto the published
`PriceUpdate`. So one key joins recommendation → explanation → decision → live price → audit row,
which is what makes the log defensible.

Two cases deliberately break the chain, and consumers must handle both:

- **`approval: "override"`** — the manager set a different price. The explanation attached to
  `decision_id` explains what the agent *wanted*, not what shipped. Never present it as explaining
  the live price.
- **`decision_id: null`** — a manager-originated price with no proposal behind it. There is no
  explanation, and the UI must say so rather than show a stale one.

The explanation is **not** guaranteed. It may lag its proposal by seconds, or never arrive if SHAP
computation fails. That is a normal state, not an error — though a manager approving a proposal
whose explanation never arrived is worth surfacing as exactly that.

## Retention and cold start

`price`, `inventory` and `guardrail` are retained, so an edge node or a dashboard connecting for
the first time immediately receives current state instead of waiting for the next publish.

`proposal` and `explain` are **not** retained. A proposal is a time-boxed recommendation about a
market that has since moved on; replaying a stale one to a dashboard that reconnects would put an
expired decision back in a manager's queue. Pending proposals survive in Postgres, not on the
broker — the dashboard rebuilds its queue from `GET /api/proposals`, never from retained messages.

## Why the 16-character limit on `reason_tag`

Layer 1 is a 16x2 character LCD. Line 1 carries SKU and price, line 2 carries the reason verbatim.
A publisher that exceeds 16 characters produces a truncated, misleading explanation at the shelf.
The constraint lives here, in the schema, so it is enforced at the publisher rather than patched in
firmware.

## Generating code from these

Do not hand-write the types on either side. Generate them, so drift is impossible:

```bash
# Python (pricing-agent, forecaster, dashboard-api)
uvx datamodel-code-generator --input contracts/price.schema.json \
  --input-file-type jsonschema --output-model-type pydantic_v2.BaseModel \
  --output services/dashboard-api/app/contracts/price.py

# TypeScript (dashboard-web)
npx json-schema-to-typescript contracts/price.schema.json \
  -o services/dashboard-web/src/contracts/price.ts
```

The MSP432 firmware cannot run a JSON Schema validator. Het reads these by hand — which is exactly
why constraints that matter to the hardware (`maxLength: 16`) are written down here.

## Changing a contract

**A PR touching this directory requires approval from all four team members.** Everything else in
the repo is a solo PR.

Backward-compatible (add an optional field, widen an enum): normal PR, mention it in standup.

Breaking (rename, remove, retype, add a required field): needs an agreed cutover — all publishers
and subscribers merge on the same day, or the change ships as a new topic and the old one is retired
once nothing reads it.

When you think something is missing, open an issue rather than adding a field locally to unblock
yourself. A field that exists on one side only is worse than a field that does not exist.

## Status

The approval gate is a **breaking change** to the original design, in which the agent published
prices directly. Every row below needs the team's sign-off together, not one at a time.

| Schema | Status | What changed |
|---|---|---|
| `proposal` | **new — needs all four** | The agent's output topic. Was `retail/price`. |
| `price` | **breaking — needs all four** | Now published by dashboard-api only. Adds `approval`, `approved_by`; `decision_id` becomes nullable. |
| `guardrail` | **breaking — needs all four** | Promoted from a clamp to the automation policy. Adds `auto_approve_pct`. No longer optional to the design. |
| `explain` | agreed | Now explains a *proposal*, not a live price. Shape unchanged. |
| `inventory` | agreed | — |
| `price` (reason_tag) | **widened: mention at standup** | `reason_tag` may now be empty (`minLength` 1 → 0), for a manager-set price with no reason; the label shows the price alone. Backward-compatible for anything that renders the string, but a consumer that rejected empty strings must stop. |
| `shelf` | **new, draft: needs all four** | What a label's glass shows, and its signal, battery and firmware, reported by the edge at every check-in. Lets the console confirm a push and show real label health. See the "Label reports and pairing" proposal. |

What each owner needs to know:

- **pricing-agent** — publish to `retail/proposal/{id}`, not `retail/price/{id}`. Include
  `current_price` and `expires_at`. Subscribe to `retail/price/{id}` to learn whether your
  recommendation was taken; approval rate per `agent_version` is an evaluation result.
- **edge / firmware** — **nothing changes.** `retail/price/{id}` still means "this is the live
  price, show it." It simply has a different publisher and two extra fields you can ignore.
- **forecaster** — unaffected.
- **dashboard-api** — you are now the only publisher of `retail/price/`, and the auto-approve rule
  above lives in you. That makes this service the system's control point rather than a read model.
