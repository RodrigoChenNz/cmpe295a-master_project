CREATE TABLE commerce_webhook_outbox (
 event_id UUID PRIMARY KEY, payload JSONB NOT NULL, attempts INT NOT NULL DEFAULT 0,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), delivered_at TIMESTAMPTZ, last_error TEXT
);
CREATE TABLE commerce_webhook_receipts (
 event_id UUID PRIMARY KEY, payload_hash TEXT NOT NULL, payload JSONB NOT NULL,
 received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
