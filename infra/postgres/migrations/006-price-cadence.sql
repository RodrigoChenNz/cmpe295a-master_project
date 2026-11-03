ALTER TABLE store_settings ADD COLUMN price_delivery_mode TEXT NOT NULL DEFAULT 'immediate' CHECK(price_delivery_mode IN ('immediate','daily'));
CREATE TABLE scheduled_price_publications (
 decision_id UUID PRIMARY KEY REFERENCES proposals,
 product_id TEXT NOT NULL REFERENCES products,
 reference_price NUMERIC(10,2) NOT NULL, payload JSONB NOT NULL,
 release_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','published','canceled')),
 message TEXT, published_at TIMESTAMPTZ
);
CREATE INDEX scheduled_prices_due ON scheduled_price_publications(release_at) WHERE status='queued';
