# Contracts

The five layers of this system never call each other's functions. They exchange JSON over MQTT.
These schemas **are** the interface between them, and they are the only files in this repo that
four people depend on simultaneously.

## Topics

| Topic | Payload | Publisher | Subscribers | QoS | Retained |
|---|---|---|---|---|---|
| `retail/price/{product_id}` | [PriceUpdate](price.schema.json) | pricing-agent | dashboard-api, edge gateway | 1 | yes |
| `retail/explain/{product_id}` | [ExplainVector](explain.schema.json) | pricing-agent (XAI module) | dashboard-api | 1 | no |
| `retail/inventory/{product_id}` | [InventoryRisk](inventory.schema.json) | forecaster | dashboard-api, pricing-agent | 0 | yes |
| `retail/guardrail/{product_id}` | [Guardrail](guardrail.schema.json) — **proposed** | dashboard-api | pricing-agent | 1 | yes |

`{product_id}` in the topic must equal `product_id` in the payload. Subscribe with `retail/price/+`
for all SKUs.

All payloads are UTF-8 JSON. All timestamps are UTC ISO-8601 (`2026-09-17T10:00:00Z`). All money is
two decimal places and must be parsed as decimal, never binary float.

## `decision_id` ties it together

A price decision emits a `PriceUpdate` and, shortly after, an `ExplainVector` carrying the same
`decision_id`. That key is what lets the dashboard answer "why is this SKU $12.99?" and what makes
the audit log defensible.

The explanation is **not** guaranteed. It may lag the price by seconds, or never arrive if SHAP
computation fails. Every consumer must render a price with a pending or missing explanation as a
normal state, not an error.

## Retention and cold start

`price` and `inventory` are retained so that an edge node or a dashboard connecting for the first
time immediately receives current state instead of waiting for the next publish. `explain` is not
retained — a stale explanation attached to a superseded price is worse than none.

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

| Schema | Status |
|---|---|
| `price` | agreed |
| `explain` | agreed |
| `inventory` | agreed |
| `guardrail` | **proposed** — blocked on pricing-agent owner confirming the agent will clamp to this band |
