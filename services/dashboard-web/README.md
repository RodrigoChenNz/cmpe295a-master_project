# dashboard-web — Layer 5

**Owner:** Rodrigo Chen · **Status:** not started

React dashboard. Two routes: live price table, and per-product detail with the SHAP
explanation panel — the centrepiece of the project's transparency claim.

React 18 · Vite · TypeScript 5 · Chart.js.

Talks only to `dashboard-api` (REST + WebSocket). It never connects to MQTT directly
and never holds business logic.

Build it with the prompt in [`docs/prompts/web-layer.md`](../../docs/prompts/web-layer.md).
