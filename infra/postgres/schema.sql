-- Manager dashboard schema. Applied automatically on first container start.
-- Mirrors contracts/*.schema.json — if you change a contract, change this in the same PR.
--
-- Money is NUMERIC(10,2), never float. Timestamps are TIMESTAMPTZ, stored UTC.

CREATE TABLE products (
    product_id   TEXT PRIMARY KEY CHECK (product_id ~ '^SKU-[0-9]{3,}$'),
    name         TEXT NOT NULL,
    category     TEXT,
    unit_cost    NUMERIC(10,2) NOT NULL CHECK (unit_cost >= 0),
    min_price    NUMERIC(10,2) CHECK (min_price > 0),
    max_price    NUMERIC(10,2) CHECK (max_price > 0),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT price_band_ordered CHECK (
        min_price IS NULL OR max_price IS NULL OR min_price <= max_price
    )
);

-- Every PriceUpdate ever seen. Append-only; this is the evaluation dataset.
CREATE TABLE price_history (
    decision_id    UUID PRIMARY KEY,
    product_id     TEXT NOT NULL REFERENCES products(product_id),
    price          NUMERIC(10,2) NOT NULL CHECK (price > 0),
    currency       TEXT NOT NULL DEFAULT 'USD',
    reason_tag     TEXT NOT NULL CHECK (char_length(reason_tag) <= 16),
    agent_version  TEXT NOT NULL,
    ts             TIMESTAMPTZ NOT NULL,
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Drives the range-toggle chart; the ordering matters more than the size here.
CREATE INDEX price_history_product_ts_idx ON price_history (product_id, ts DESC);
CREATE INDEX price_history_agent_idx      ON price_history (agent_version, ts DESC);

-- Every ExplainVector. decision_id is FK to price_history: an explanation without
-- a price is meaningless, but a price without an explanation is a valid state,
-- so the relationship is optional in that direction only.
CREATE TABLE shap_vectors (
    decision_id  UUID PRIMARY KEY REFERENCES price_history(decision_id) ON DELETE CASCADE,
    product_id   TEXT NOT NULL REFERENCES products(product_id),
    base_value   NUMERIC(10,4) NOT NULL,
    shap         JSONB NOT NULL,
    ts           TIMESTAMPTZ NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX shap_vectors_product_ts_idx ON shap_vectors (product_id, ts DESC);

-- Every InventoryRisk. Latest row per SKU drives the alert panel; history
-- supports the forecaster comparison (Prophet vs LSTM) in the paper.
CREATE TABLE inventory_risk (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id        TEXT NOT NULL REFERENCES products(product_id),
    dead_stock_risk   NUMERIC(4,3) NOT NULL CHECK (dead_stock_risk BETWEEN 0 AND 1),
    predicted_sellby  DATE NOT NULL,
    units_on_hand     INTEGER CHECK (units_on_hand >= 0),
    model_version     TEXT NOT NULL,
    ts                TIMESTAMPTZ NOT NULL,
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX inventory_risk_product_ts_idx ON inventory_risk (product_id, ts DESC);

-- Append-only record of anything that changed system behaviour: an agent
-- decision, a manager guardrail edit. This is what makes the system auditable
-- rather than a black box, which is the project's central claim.
CREATE TABLE agent_audit_log (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type   TEXT NOT NULL CHECK (event_type IN (
                     'price_decision', 'guardrail_set', 'agent_started', 'agent_stopped'
                 )),
    product_id   TEXT REFERENCES products(product_id),
    decision_id  UUID,
    actor        TEXT NOT NULL,          -- agent_version, or a manager username
    detail       JSONB NOT NULL DEFAULT '{}'::jsonb,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX agent_audit_log_ts_idx         ON agent_audit_log (ts DESC);
CREATE INDEX agent_audit_log_product_ts_idx ON agent_audit_log (product_id, ts DESC);

-- Convenience view: current state of every SKU, one row each. The products
-- list endpoint reads this instead of assembling three queries.
CREATE VIEW current_product_state AS
SELECT
    p.product_id,
    p.name,
    p.category,
    p.unit_cost,
    p.min_price,
    p.max_price,
    ph.price          AS current_price,
    ph.reason_tag     AS current_reason,
    ph.decision_id    AS current_decision_id,
    ph.ts             AS priced_at,
    ir.dead_stock_risk,
    ir.predicted_sellby
FROM products p
LEFT JOIN LATERAL (
    SELECT * FROM price_history
    WHERE product_id = p.product_id
    ORDER BY ts DESC LIMIT 1
) ph ON TRUE
LEFT JOIN LATERAL (
    SELECT * FROM inventory_risk
    WHERE product_id = p.product_id
    ORDER BY ts DESC LIMIT 1
) ir ON TRUE;
