# msp432 — Layers 1-2

**Owner:** Het Bhalala · **Status:** not started

MSP432P401R LaunchPad firmware driving a 16x2 character LCD at the shelf edge.

- Receives price + reason over the serial link from [`../gateway`](../gateway)
- Line 1: SKU and price. Line 2: `reason_tag`, verbatim, max 16 chars
- Persists last price to flash so a power cycle or broker outage still shows a price

The 16-char limit is enforced upstream in [`contracts/price.schema.json`](../../contracts/price.schema.json).

**Open question:** exact part number. "MSP32" is not a TI SKU — confirm MSP432P401R vs MSP430.
