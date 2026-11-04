ALTER TABLE scheduled_price_publications ADD COLUMN approved_at TIMESTAMPTZ NOT NULL DEFAULT now();
