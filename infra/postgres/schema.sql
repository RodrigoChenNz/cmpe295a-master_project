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
    -- Max move, as a percent of current price, applied without a human.
    -- NULL means always ask: every proposal for this SKU goes to the queue.
    auto_approve_pct NUMERIC(5,2) CHECK (auto_approve_pct BETWEEN 0 AND 100),
    policy_set_by    TEXT,
    policy_set_at    TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT price_band_ordered CHECK (
        min_price IS NULL OR max_price IS NULL OR min_price <= max_price
    )
);

-- Every recommendation the agent has made, and what became of it.
-- The review queue is rebuilt from here on restart, never from retained MQTT.
CREATE TABLE proposals (
    decision_id     UUID PRIMARY KEY,
    product_id      TEXT NOT NULL REFERENCES products(product_id),
    proposed_price  NUMERIC(10,2) NOT NULL CHECK (proposed_price > 0),
    current_price   NUMERIC(10,2) NOT NULL CHECK (current_price > 0),
    reason_tag      TEXT NOT NULL CHECK (char_length(reason_tag) <= 16),
    agent_version   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                        'pending',        -- waiting on a human
                        'auto_approved',  -- inside policy, applied with no human
                        'approved',       -- human took the agent's price as-is
                        'overridden',     -- human set a different price
                        'rejected',       -- human declined; shelf keeps its price
                        'expired',        -- lapsed unacted; shelf keeps its price
                        'superseded'      -- a newer proposal arrived for this SKU
                     )),
    -- Set only once decided. final_price differs from proposed_price on override.
    decided_by      TEXT,
    decided_at      TIMESTAMPTZ,
    final_price     NUMERIC(10,2) CHECK (final_price > 0),
    decline_reason  TEXT,
    expires_at      TIMESTAMPTZ NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT decided_fields_together CHECK (
        (status = 'pending') = (decided_at IS NULL)
    )
);

-- The queue view: pending, soonest to expire first.
CREATE INDEX proposals_pending_idx ON proposals (expires_at)
    WHERE status = 'pending';
CREATE INDEX proposals_product_ts_idx ON proposals (product_id, ts DESC);
-- Approval rate per agent version is an evaluation result, so index for it.
CREATE INDEX proposals_agent_status_idx ON proposals (agent_version, status);

-- Every approved price ever published to a shelf. Append-only; the evaluation dataset.
-- Its own id, because a manager-originated price has no decision_id at all.
CREATE TABLE price_history (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id     TEXT NOT NULL REFERENCES products(product_id),
    price          NUMERIC(10,2) NOT NULL CHECK (price > 0),
    currency       TEXT NOT NULL DEFAULT 'USD',
    -- NULL when a manager set the price with no proposal behind it.
    decision_id    UUID REFERENCES proposals(decision_id),
    reason_tag     TEXT NOT NULL CHECK (char_length(reason_tag) <= 16),
    approval       TEXT NOT NULL CHECK (approval IN ('auto', 'manager', 'override')),
    approved_by    TEXT NOT NULL,
    agent_version  TEXT,
    ts             TIMESTAMPTZ NOT NULL,
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Drives the range-toggle chart; the ordering matters more than the size here.
CREATE INDEX price_history_product_ts_idx ON price_history (product_id, ts DESC);
CREATE INDEX price_history_agent_idx      ON price_history (agent_version, ts DESC);
CREATE INDEX price_history_decision_idx   ON price_history (decision_id);

-- Every ExplainVector. These explain a PROPOSAL, not necessarily a live price:
-- the proposal may have been rejected, or overridden with a different number.
-- A proposal without an explanation is a valid state, so the relationship is
-- optional in that direction only.
CREATE TABLE shap_vectors (
    decision_id  UUID PRIMARY KEY REFERENCES proposals(decision_id) ON DELETE CASCADE,
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
                     'proposal_received',
                     'proposal_auto_approved',   -- applied by the manager's standing policy
                     'proposal_approved',        -- a human took the agent's price
                     'proposal_overridden',      -- a human set a different price
                     'proposal_rejected',        -- a human declined it
                     'proposal_expired',         -- lapsed unacted
                     'price_published',          -- reached the shelf
                     'guardrail_set',            -- a human changed the policy
                     'agent_started', 'agent_stopped'
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
    p.auto_approve_pct,
    ph.price          AS current_price,
    ph.reason_tag     AS current_reason,
    ph.decision_id    AS current_decision_id,
    ph.approval       AS current_approval,
    ph.approved_by    AS current_approved_by,
    ph.ts             AS priced_at,
    -- The price before the current one, so the board can render a delta without
    -- client-side memory that a reconnect would wipe.
    prev.price        AS previous_price,
    prev.ts           AS previous_ts,
    -- How many recommendations are waiting on a human for this SKU.
    pend.pending_count,
    ir.dead_stock_risk,
    ir.predicted_sellby
FROM products p
LEFT JOIN LATERAL (
    SELECT * FROM price_history
    WHERE product_id = p.product_id
    ORDER BY ts DESC LIMIT 1
) ph ON TRUE
LEFT JOIN LATERAL (
    SELECT price, ts FROM price_history
    WHERE product_id = p.product_id
    ORDER BY ts DESC OFFSET 1 LIMIT 1
) prev ON TRUE
LEFT JOIN LATERAL (
    SELECT count(*) AS pending_count FROM proposals
    WHERE product_id = p.product_id AND status = 'pending'
) pend ON TRUE
LEFT JOIN LATERAL (
    SELECT * FROM inventory_risk
    WHERE product_id = p.product_id
    ORDER BY ts DESC LIMIT 1
) ir ON TRUE;

-- The review queue, ready to render: proposal joined to its explanation (which
-- may be absent) and to the SKU's policy, soonest to expire first.
CREATE VIEW review_queue AS
SELECT
    pr.decision_id,
    pr.product_id,
    p.name,
    pr.current_price,
    pr.proposed_price,
    pr.proposed_price - pr.current_price                        AS delta,
    round((pr.proposed_price - pr.current_price)
          / pr.current_price * 100, 2)                          AS delta_pct,
    pr.reason_tag,
    pr.agent_version,
    pr.expires_at,
    pr.ts,
    p.min_price,
    p.max_price,
    p.auto_approve_pct,
    -- Why this needs a human: outside the band, or too big a move, or both.
    (p.min_price IS NOT NULL AND pr.proposed_price < p.min_price)
      OR (p.max_price IS NOT NULL AND pr.proposed_price > p.max_price)        AS out_of_band,
    sv.base_value,
    sv.shap,
    (sv.decision_id IS NULL)                                    AS explanation_missing
FROM proposals pr
JOIN products p      ON p.product_id = pr.product_id
LEFT JOIN shap_vectors sv ON sv.decision_id = pr.decision_id
WHERE pr.status = 'pending'
ORDER BY pr.expires_at ASC;
