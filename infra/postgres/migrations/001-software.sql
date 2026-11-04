-- Additive upgrade for existing stores; never requires deleting a volume.
CREATE TABLE IF NOT EXISTS message_outbox (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 topic TEXT NOT NULL, payload JSONB NOT NULL,
 qos SMALLINT NOT NULL DEFAULT 1, retained BOOLEAN NOT NULL DEFAULT false,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), sent_at TIMESTAMPTZ,
 attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT
);
CREATE INDEX IF NOT EXISTS message_outbox_pending ON message_outbox(id) WHERE sent_at IS NULL;
CREATE TABLE IF NOT EXISTS pending_explanations (
 decision_id UUID PRIMARY KEY, payload JSONB NOT NULL,
 received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS store_observations (
 state_id UUID PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(product_id),
 payload JSONB NOT NULL, ts TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS store_observations_product_ts ON store_observations(product_id, ts DESC);
CREATE UNIQUE INDEX IF NOT EXISTS inventory_risk_message ON inventory_risk(product_id, model_version, ts);
ALTER TABLE label_pushes ADD COLUMN IF NOT EXISTS publication_id BIGINT REFERENCES price_history(id);
ALTER TABLE label_pushes ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ;
ALTER TABLE shelf_labels ADD COLUMN IF NOT EXISTS last_publication_id BIGINT;
ALTER TABLE shelf_labels ADD COLUMN IF NOT EXISTS emulated BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE shelf_labels ADD COLUMN IF NOT EXISTS rendered_line1 TEXT;
ALTER TABLE shelf_labels ADD COLUMN IF NOT EXISTS rendered_line2 TEXT;
ALTER TABLE shelf_labels ADD COLUMN IF NOT EXISTS rendered_at TIMESTAMPTZ;
