-- Additive commerce state. Old stores and price contracts remain valid.
ALTER TABLE products ADD COLUMN online BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE products ADD COLUMN description TEXT NOT NULL DEFAULT '';
ALTER TABLE products ADD COLUMN images JSONB NOT NULL DEFAULT '[]';
ALTER TABLE products ADD COLUMN online_price NUMERIC(10,2) CHECK (online_price > 0);
CREATE TABLE suppliers (
 supplier_id UUID PRIMARY KEY, name TEXT NOT NULL, contact TEXT,
 ucp_profile_url TEXT, simulated BOOLEAN NOT NULL DEFAULT true,
 payment_terms TEXT NOT NULL DEFAULT 'test payment', lead_time_days INT NOT NULL DEFAULT 3 CHECK(lead_time_days >= 0)
);
ALTER TABLE products ADD COLUMN supplier_id UUID REFERENCES suppliers;
ALTER TABLE store_settings ADD COLUMN nightly_run_time TIME NOT NULL DEFAULT '02:00';
ALTER TABLE store_settings ADD COLUMN shelf_update_time TIME NOT NULL DEFAULT '06:00';
ALTER TABLE store_settings ADD COLUMN commerce_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE store_settings ADD COLUMN nightly_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE store_settings ADD COLUMN shipping_minor INT NOT NULL DEFAULT 500 CHECK(shipping_minor >= 0);
ALTER TABLE store_settings ADD COLUMN tax_bps INT NOT NULL DEFAULT 0 CHECK(tax_bps BETWEEN 0 AND 10000);
CREATE TABLE commerce_policy (
 id BOOLEAN PRIMARY KEY DEFAULT true CHECK(id),
 policy JSONB NOT NULL DEFAULT '{"purchase_order":{"mode":"ask","auto_max_total":"500.00"},"deal":{"mode":"ask","min_margin_pct":20,"max_discount_pct":10,"min_quantity":5},"listing":{"mode":"ask"}}',
 updated_by TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO commerce_policy(id,updated_by) VALUES(true,'migration');
CREATE TABLE agent_runs (
 run_id UUID PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed')),
 requested_by TEXT NOT NULL, scheduled_date DATE UNIQUE,
 steps JSONB NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX one_active_agent_run ON agent_runs ((true)) WHERE status IN ('queued','running');
CREATE TABLE commerce_decisions (
 decision_id UUID PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('purchase_order','deal','listing')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','auto_approved','overridden','rejected','expired')),
 subject JSONB NOT NULL, proposal JSONB NOT NULL, context JSONB NOT NULL DEFAULT '{}', explanation JSONB NOT NULL,
 gate JSONB NOT NULL DEFAULT '{}', verdict JSONB, outcome JSONB,
 run_id UUID REFERENCES agent_runs, agent_version TEXT NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX commerce_pending ON commerce_decisions(expires_at) WHERE status='pending';
CREATE TABLE decision_feedback (
 decision_id UUID PRIMARY KEY, tags JSONB NOT NULL DEFAULT '[]', reason TEXT,
 outcome JSONB, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE market_observations (
 observation_id UUID PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products,
 source TEXT NOT NULL CHECK(source IN ('simulator','supplier','manual','web')),
 competitor TEXT NOT NULL, price NUMERIC(10,2) NOT NULL CHECK(price > 0),
 currency TEXT NOT NULL DEFAULT 'USD', url TEXT, observed_at TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX market_product_time ON market_observations(product_id,observed_at DESC);
CREATE TABLE checkout_sessions (
 checkout_id UUID PRIMARY KEY, owner_hash TEXT NOT NULL, status TEXT NOT NULL,
 payload JSONB NOT NULL, expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '1 hour',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE orders (
 order_id UUID PRIMARY KEY, checkout_id UUID NOT NULL UNIQUE REFERENCES checkout_sessions,
 owner_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'processing', channel TEXT NOT NULL CHECK(channel IN ('storefront','ucp')),
 payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE order_events (
 event_id UUID PRIMARY KEY, order_id UUID NOT NULL REFERENCES orders,
 type TEXT NOT NULL, payload JSONB NOT NULL, actor TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE offers (
 offer_id UUID PRIMARY KEY, checkout_id UUID NOT NULL REFERENCES checkout_sessions,
 status TEXT NOT NULL, payload JSONB NOT NULL, decision_id UUID REFERENCES commerce_decisions,
 expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_live_offer ON offers(checkout_id) WHERE status IN ('pending_review','countered','accepted');
CREATE TABLE purchase_orders (
 po_id UUID PRIMARY KEY, supplier_id UUID NOT NULL REFERENCES suppliers,
 decision_id UUID UNIQUE REFERENCES commerce_decisions, status TEXT NOT NULL DEFAULT 'draft',
 lines JSONB NOT NULL, deliver_by DATE, supplier_order_id TEXT, supplier_checkout_id UUID,
 created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE supplier_checkouts (
 checkout_id UUID PRIMARY KEY, po_id UUID NOT NULL UNIQUE REFERENCES purchase_orders,
 status TEXT NOT NULL, payload JSONB NOT NULL, order_id UUID UNIQUE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE inventory_movements (
 movement_id UUID PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products,
 type TEXT NOT NULL CHECK(type IN ('sale','receipt','adjustment','return','shrink')),
 quantity INT NOT NULL CHECK(quantity<>0), order_id UUID REFERENCES orders, po_id UUID REFERENCES purchase_orders,
 note TEXT NOT NULL, actor TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE ledger_entries (
 entry_id UUID PRIMARY KEY, type TEXT NOT NULL CHECK(type IN ('sale','refund','purchase','adjustment')),
 amount NUMERIC(12,2) NOT NULL, cogs NUMERIC(12,2) NOT NULL DEFAULT 0, units INT NOT NULL DEFAULT 0,
 product_id TEXT REFERENCES products, category TEXT, channel TEXT,
 order_id UUID REFERENCES orders, po_id UUID REFERENCES purchase_orders,
 memo TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ledger_time ON ledger_entries(created_at);
CREATE TABLE commerce_audit (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 event TEXT NOT NULL, subject TEXT NOT NULL, actor TEXT NOT NULL, detail JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE commerce_requests (
 caller_hash TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
 response JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(caller_hash,key)
);
CREATE TABLE commerce_rate_limits (
 caller TEXT NOT NULL, minute TIMESTAMPTZ NOT NULL, count INT NOT NULL, PRIMARY KEY(caller,minute)
);
