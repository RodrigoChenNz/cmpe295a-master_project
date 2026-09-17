# dashboard-api — Layer 5

**Owner:** Rodrigo Chen · **Status:** not started

FastAPI service that is the only bridge between MQTT and the browser.

- Subscribes `retail/price/+`, `retail/explain/+`, `retail/inventory/+`
- Validates every message against [`contracts/`](../../contracts/); logs and drops malformed ones
- Persists to Postgres ([`infra/postgres/schema.sql`](../../infra/postgres/schema.sql))
- Fans out to WebSocket clients on `/ws/stream`
- Serves REST history under `/api`
- Publishes `retail/guardrail/{id}` when a manager edits a price band

Python 3.11 · FastAPI · aiomqtt · asyncpg · pydantic v2 · managed by `uv`.

Build it with the prompt in [`docs/prompts/web-layer.md`](../../docs/prompts/web-layer.md).
