-- ShelfIQ console schema — additive migration on top of 01-schema.sql.
--
-- 01-schema.sql mirrors contracts/*.schema.json and is the MQTT interface shared by
-- all four team members. NOTHING IN THIS FILE CHANGES THAT. Everything here is
-- store-side state that the console needs and the message bus has never carried:
-- the catalogue a human maintains, the physical labels, who is signed in, and the
-- per-category pricing rules a manager edits.
--
-- Changing this file is a solo PR. Changing 01-schema.sql or contracts/ still needs
-- all four approvals.
--
-- Money is NUMERIC(10,2), never float. Timestamps are TIMESTAMPTZ, stored UTC.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Catalogue fields a human maintains
-- ─────────────────────────────────────────────────────────────────────────────
-- `product_id` stays the contract key (SKU-001). `sku` is the store's own
-- article code (DA-1007) that staff read off a delivery note — a different
-- identifier with a different audience, which is why it is a separate column.

ALTER TABLE products
    -- Line 1 of a 16x2 LCD, verbatim. The 16-character cap is the hardware,
    -- not a style choice: characters past 16 are dropped by the label firmware.
    ADD COLUMN display_name TEXT CHECK (char_length(display_name) <= 16),
    ADD COLUMN sku          TEXT,
    ADD COLUMN upc          TEXT CHECK (upc ~ '^[0-9]{8,14}$'),
    ADD COLUMN brand        TEXT,
    ADD COLUMN supplier     TEXT,
    -- Units on hand as the store's own count. `inventory_risk.units_on_hand`
    -- is the forecaster's view of the same quantity and may disagree; this one
    -- is what staff edit and what the Products table shows.
    ADD COLUMN stock_on_hand INTEGER CHECK (stock_on_hand >= 0),
    ADD COLUMN image_url    TEXT,
    ADD COLUMN field_pack   TEXT,
    -- Excluded from the agent entirely — no proposals at all for this SKU.
    -- Distinct from auto_approve_pct IS NULL, which means "propose, but always ask".
    ADD COLUMN agent_excluded BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN archived_at  TIMESTAMPTZ;

CREATE UNIQUE INDEX products_sku_idx ON products (sku) WHERE sku IS NOT NULL;
CREATE UNIQUE INDEX products_upc_idx ON products (upc) WHERE upc IS NOT NULL;
CREATE INDEX products_category_idx ON products (category) WHERE archived_at IS NULL;

-- Arbitrary extra fields supplied by a field pack (expiry, warranty, size …).
-- A JSONB bag rather than columns, because the packs are configured per store
-- and the agent only reads a documented handful of keys.
CREATE TABLE product_fields (
    product_id TEXT PRIMARY KEY REFERENCES products(product_id) ON DELETE CASCADE,
    pack       TEXT NOT NULL,
    values     JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Dashboard-side proposal metadata
-- ─────────────────────────────────────────────────────────────────────────────
-- The Price suggestions table shows a prose "Why" column and a confidence bar.
-- NEITHER IS IN contracts/proposal.schema.json. Rather than invent them, they
-- live here as nullable columns that the API fills only when the publisher
-- actually sent them, and the UI renders "Not reported" when it did not.
-- Worth raising with the team as an optional addition to the proposal contract.

ALTER TABLE proposals
    ADD COLUMN agent_confidence NUMERIC(4,3)
        CHECK (agent_confidence BETWEEN 0 AND 1),
    -- Longer prose the agent may optionally supply. When absent the API composes
    -- a sentence from reason_tag + the SHAP vector + product facts, and says so.
    ADD COLUMN rationale TEXT;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Locations — where in the store a label physically hangs
-- ─────────────────────────────────────────────────────────────────────────────
-- Each store names its own levels, top first: one floor might be
-- Zone › Aisle › Bay, a bigger store Floor › Zone › Aisle › Bay. A manager
-- configures them before anything else, and no product can be added until they
-- have (dashboard-api enforces both). Depth 1 is the top level; slots — where a
-- shelf label hangs, coded like "A3-04", which is what staff say out loud —
-- attach only to the deepest one.
--
-- Names can change at any time. The number of levels can change only while no
-- location exists, because existing nodes would otherwise sit at a depth that
-- means something different from when they were made.

CREATE TABLE location_levels (
    depth       SMALLINT PRIMARY KEY CHECK (depth BETWEEN 1 AND 6),
    name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 30),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX location_levels_name_idx ON location_levels (lower(name));

CREATE TABLE locations (
    location_id  TEXT PRIMARY KEY,
    parent_id    TEXT REFERENCES locations(location_id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    -- A child sits exactly one level below its parent; depth 1 has no parent.
    -- dashboard-api checks that on every insert and move.
    depth        SMALLINT NOT NULL REFERENCES location_levels(depth),
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT no_self_parent CHECK (parent_id IS DISTINCT FROM location_id)
);

CREATE INDEX locations_parent_idx ON locations (parent_id, sort_order);

-- Two siblings may not share a name. The Shelf labels location filter shows
-- names only, so a second "Aisle A" under one zone makes that select ambiguous.
-- An expression index rather than UNIQUE (parent_id, name): a zone's parent_id
-- is NULL, NULLs are distinct to a unique constraint, and the constraint would
-- therefore not cover zones at all.
CREATE UNIQUE INDEX locations_sibling_name_idx
    ON locations (coalesce(parent_id, ''), lower(name));

CREATE TABLE location_slots (
    slot_code    TEXT PRIMARY KEY,
    location_id  TEXT NOT NULL REFERENCES locations(location_id) ON DELETE CASCADE,
    sort_order   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX location_slots_location_idx ON location_slots (location_id, sort_order);

-- Where a product lives in the store, at any level. Optional: a catalogue is
-- usually entered first and placed afterwards. Deleting a location leaves its
-- products unassigned rather than deleting them.
ALTER TABLE products
    ADD COLUMN location_id TEXT REFERENCES locations(location_id) ON DELETE SET NULL;

CREATE INDEX products_location_idx ON products (location_id) WHERE archived_at IS NULL;

-- Every location with its full path, e.g. "Floor 1 › Grocery › Aisle 3 › Bay 1",
-- so no screen has to rebuild it from the tree.
CREATE VIEW shelfiq_location_paths AS
WITH RECURSIVE walk AS (
    SELECT location_id, depth, name::text AS path
      FROM locations WHERE parent_id IS NULL
    UNION ALL
    SELECT c.location_id, c.depth, walk.path || ' › ' || c.name
      FROM locations c JOIN walk ON c.parent_id = walk.location_id
)
SELECT location_id, depth, path FROM walk;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Shelf labels — Layer 1 hardware, as the console sees it
-- ─────────────────────────────────────────────────────────────────────────────
-- Two hardware families, and they are genuinely different devices:
--   lcd  — 16x2 character LCD. Two lines of sixteen characters, nothing else.
--   eink — Heltec Vision Master E213, 250x122 e-ink on an ESP32. A pixel
--          display, so it is NOT bound by the 16-character cap — but the
--          contract enforces 16 for every publisher so the LCD never truncates.
--
-- `status` is the console's belief about the device, derived from what the
-- gateway reports. It is never a claim about what the glass is showing right
-- now: the dashboard and the edge node are independent MQTT subscribers and
-- there is no acknowledgement topic yet, so a label can be displaying a stale
-- price from flash while this row says 'online'. The UI must say "as published".

CREATE TABLE shelf_labels (
    label_id     TEXT PRIMARY KEY CHECK (label_id ~ '^SHLF-[0-9]{3,}$'),
    kind         TEXT NOT NULL CHECK (kind IN ('lcd', 'eink')),
    -- A label may hang unpaired on a shelf; a product may have no label.
    -- Both are normal states, so both sides are nullable and the pairing is 1:1.
    product_id   TEXT REFERENCES products(product_id) ON DELETE SET NULL,
    slot_code    TEXT REFERENCES location_slots(slot_code) ON DELETE SET NULL,
    status       TEXT NOT NULL DEFAULT 'unassigned' CHECK (status IN (
                     'online',      -- seen recently, last push confirmed
                     'syncing',     -- a push is in flight
                     'failed',      -- a push did not confirm
                     'offline',     -- not seen within the stale window
                     'unassigned'   -- no product paired
                 )),
    last_seen_at TIMESTAMPTZ,
    rssi_dbm     SMALLINT CHECK (rssi_dbm BETWEEN -120 AND 0),
    battery_pct  SMALLINT CHECK (battery_pct BETWEEN 0 AND 100),
    firmware     TEXT,
    paired_at    TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One label per product, enforced rather than assumed.
CREATE UNIQUE INDEX shelf_labels_product_idx ON shelf_labels (product_id)
    WHERE product_id IS NOT NULL;
CREATE UNIQUE INDEX shelf_labels_slot_idx ON shelf_labels (slot_code)
    WHERE slot_code IS NOT NULL;
CREATE INDEX shelf_labels_status_idx ON shelf_labels (status);

-- Every attempt to push a price to a label, and what became of it. This is what
-- makes "Never reached shelf" in the activity log a fact rather than a guess.
CREATE TABLE label_pushes (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label_id     TEXT NOT NULL REFERENCES shelf_labels(label_id) ON DELETE CASCADE,
    product_id   TEXT REFERENCES products(product_id) ON DELETE SET NULL,
    -- Joins back to the decision that caused the push; NULL for a manager-set
    -- price with no proposal behind it, exactly as in price_history.
    decision_id  UUID,
    price        NUMERIC(10,2) NOT NULL CHECK (price > 0),
    line1        TEXT NOT NULL CHECK (char_length(line1) <= 16),
    line2        TEXT NOT NULL CHECK (char_length(line2) <= 16),
    result       TEXT NOT NULL DEFAULT 'pending' CHECK (result IN (
                     'pending',    -- published to MQTT, nothing heard back
                     'confirmed',  -- the label acknowledged it
                     'failed'      -- it did not, within the window
                 )),
    published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at   TIMESTAMPTZ
);

CREATE INDEX label_pushes_label_idx   ON label_pushes (label_id, published_at DESC);
CREATE INDEX label_pushes_decision_idx ON label_pushes (decision_id);

-- Connection events for the label detail screen: joins, drops, firmware updates.
CREATE TABLE label_events (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label_id   TEXT NOT NULL REFERENCES shelf_labels(label_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (event_type IN (
                   'connected', 'disconnected', 'paired', 'unpaired',
                   'firmware_updated', 'battery_low',
                   -- A manager asked the device to identify itself. Recorded so
                   -- the console's one un-acknowledged command still has a trace.
                   'blink_requested',
                   -- The slot this label hung in was deleted underneath it.
                   -- Without a row here the only evidence is absent data.
                   'slot_cleared'
               )),
    detail     JSONB NOT NULL DEFAULT '{}'::jsonb,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX label_events_label_ts_idx ON label_events (label_id, ts DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. People — because the audit log is only worth anything with a name on it
-- ─────────────────────────────────────────────────────────────────────────────
-- `username` is what lands in price_history.approved_by and proposals.decided_by,
-- which is why it is the primary key rather than a surrogate id: the audit rows
-- are append-only and must keep reading correctly after a person leaves.

CREATE TABLE users (
    username      TEXT PRIMARY KEY CHECK (username ~ '^[a-z0-9_.-]{2,32}$'),
    email         TEXT NOT NULL UNIQUE,
    full_name     TEXT NOT NULL,
    -- Argon2id. Never a plaintext or reversible value, and never logged.
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN (
                      'superadmin', -- a manager who also owns accounts; the
                                    -- first account created on a new store
                      'manager',   -- may approve prices and edit rules
                      'staff',     -- may edit the catalogue, not approve prices
                      'viewer'     -- read only
                  )),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Server-side sessions rather than a self-contained token, so that
-- deactivating a user takes effect on their next request instead of whenever
-- their token happens to expire. The cookie carries only the id.
CREATE TABLE sessions (
    session_id  UUID PRIMARY KEY,
    username    TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_agent  TEXT
);

CREATE INDEX sessions_username_idx ON sessions (username);
CREATE INDEX sessions_expiry_idx   ON sessions (expires_at);

-- Outstanding invitations. Only a SHA-256 of the token is kept, so the database
-- can recognise a link but never reproduce one; the invitee sets their own
-- password with it, and every invite for that user is deleted when they do.
CREATE TABLE user_invites (
    token_hash  TEXT PRIMARY KEY,
    username    TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    created_by  TEXT REFERENCES users(username) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX user_invites_username_idx ON user_invites (username);

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Pricing rules — the per-category layer above the per-SKU guardrail
-- ─────────────────────────────────────────────────────────────────────────────
-- products.min_price / max_price / auto_approve_pct are the per-SKU policy and
-- are what contracts/guardrail.schema.json publishes. These category rows are
-- the manager's defaults, applied when a SKU has no explicit band of its own,
-- plus a margin floor the per-SKU contract has no field for.

CREATE TABLE category_rules (
    category          TEXT PRIMARY KEY,
    -- Deliberately no `floor_pct`. A SKU's floor is derived from the margin
    -- floor below (`unit_cost / (1 - min_margin_pct/100)`) and its ceiling from
    -- `ceiling_pct`. A third percentage with no derivation and no consumer
    -- would sit frozen at whatever seeded it while the API kept returning it.
    ceiling_pct       NUMERIC(5,2) CHECK (ceiling_pct BETWEEN 0 AND 1000),
    min_margin_pct    NUMERIC(5,2) NOT NULL DEFAULT 0
                          CHECK (min_margin_pct BETWEEN 0 AND 100),
    agent_may_suggest BOOLEAN NOT NULL DEFAULT TRUE,
    set_by            TEXT REFERENCES users(username),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Store-wide change limits, one row. The CHECK keeps it that way.
CREATE TABLE change_limits (
    id                     BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    max_change_pct         NUMERIC(5,2) NOT NULL DEFAULT 20,
    max_changes_per_day    INTEGER      NOT NULL DEFAULT 3 CHECK (max_changes_per_day >= 0),
    min_hours_between      INTEGER      NOT NULL DEFAULT 4 CHECK (min_hours_between >= 0),
    -- An undecided proposal lapses after this many hours.
    proposal_ttl_hours     INTEGER      NOT NULL DEFAULT 4 CHECK (proposal_ttl_hours > 0),
    set_by                 TEXT REFERENCES users(username),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. Store settings — one row, same trick
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE store_settings (
    id             BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    store_name     TEXT NOT NULL DEFAULT 'SJSU Market',
    store_code     TEXT,
    timezone       TEXT NOT NULL DEFAULT 'America/Los_Angeles',
    currency       TEXT NOT NULL DEFAULT 'USD',
    mqtt_host      TEXT NOT NULL DEFAULT 'localhost',
    mqtt_port      INTEGER NOT NULL DEFAULT 1883 CHECK (mqtt_port BETWEEN 1 AND 65535),
    mqtt_tls       BOOLEAN NOT NULL DEFAULT FALSE,
    -- Seconds without a sighting before a label is shown as offline.
    label_stale_secs INTEGER NOT NULL DEFAULT 900 CHECK (label_stale_secs > 0),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. Views the console reads
-- ─────────────────────────────────────────────────────────────────────────────

-- One row per product with everything the Products table and cards render, so
-- the list endpoint is a single query rather than four and a join in Python.
CREATE VIEW shelfiq_products AS
SELECT
    p.product_id,
    p.name,
    p.display_name,
    p.sku,
    p.upc,
    p.brand,
    p.supplier,
    p.category,
    p.unit_cost,
    p.stock_on_hand,
    p.image_url,
    p.field_pack,
    p.min_price,
    p.max_price,
    p.auto_approve_pct,
    p.agent_excluded,
    ph.price      AS current_price,
    ph.ts         AS priced_at,
    prev.price    AS previous_price,
    -- Margin as a percent of the selling price. NULL rather than 0 when there
    -- is no price yet, so the UI shows "—" instead of a misleading 0%.
    CASE WHEN ph.price IS NOT NULL AND ph.price > 0
         THEN round((ph.price - p.unit_cost) / ph.price * 100, 1)
    END           AS margin_pct,
    -- Sign of the last price change, which is what the Price review filter
    -- ("Price increased" / "Price decreased") actually means.
    CASE WHEN prev.price IS NULL OR ph.price IS NULL THEN NULL
         WHEN ph.price > prev.price THEN 'up'
         WHEN ph.price < prev.price THEN 'down'
         ELSE 'flat'
    END           AS last_change_dir,
    -- Stock health, as the handoff defines it: out = 0, low = 1-10, in = >10.
    CASE WHEN p.stock_on_hand IS NULL THEN NULL
         WHEN p.stock_on_hand = 0  THEN 'out'
         WHEN p.stock_on_hand <= 10 THEN 'low'
         ELSE 'in'
    END           AS stock_health,
    pend.pending_count,
    l.label_id,
    l.kind        AS label_kind,
    l.status      AS label_status,
    l.slot_code,
    p.location_id,
    lp.path       AS location_path,
    ir.dead_stock_risk,
    ir.predicted_sellby
FROM products p
LEFT JOIN shelfiq_location_paths lp ON lp.location_id = p.location_id
LEFT JOIN LATERAL (
    SELECT price, ts FROM price_history
    WHERE product_id = p.product_id ORDER BY ts DESC LIMIT 1
) ph ON TRUE
LEFT JOIN LATERAL (
    SELECT price FROM price_history
    WHERE product_id = p.product_id ORDER BY ts DESC OFFSET 1 LIMIT 1
) prev ON TRUE
LEFT JOIN LATERAL (
    SELECT count(*) AS pending_count FROM proposals
    WHERE product_id = p.product_id AND status = 'pending'
) pend ON TRUE
LEFT JOIN shelf_labels l ON l.product_id = p.product_id
LEFT JOIN LATERAL (
    SELECT dead_stock_risk, predicted_sellby FROM inventory_risk
    WHERE product_id = p.product_id ORDER BY ts DESC LIMIT 1
) ir ON TRUE
WHERE p.archived_at IS NULL;

-- The review queue the console renders: review_queue from 01-schema.sql, plus
-- the catalogue and label facts the suggestions table needs in the same row.
CREATE VIEW shelfiq_review_queue AS
SELECT
    q.*,
    p.sku,
    p.display_name,
    p.category,
    p.brand,
    p.unit_cost,
    p.stock_on_hand,
    p.image_url,
    pr.agent_confidence,
    pr.rationale,
    l.label_id,
    l.kind      AS label_kind,
    l.status    AS label_status,
    l.slot_code,
    -- Margin the store would be left with if this proposal were approved.
    CASE WHEN q.proposed_price > 0
         THEN round((q.proposed_price - p.unit_cost) / q.proposed_price * 100, 1)
    END AS proposed_margin_pct,
    cr.min_margin_pct AS category_min_margin_pct
FROM review_queue q
JOIN products  p  ON p.product_id = q.product_id
JOIN proposals pr ON pr.decision_id = q.decision_id
LEFT JOIN shelf_labels   l  ON l.product_id = q.product_id
LEFT JOIN category_rules cr ON cr.category  = p.category;

-- The activity log: one row per published price, joined to the decision behind
-- it and to how the push actually went.
CREATE VIEW shelfiq_activity AS
SELECT
    h.id,
    h.ts,
    h.product_id,
    p.name            AS product_name,
    p.category,
    prev.price        AS old_price,
    h.price           AS new_price,
    CASE WHEN prev.price IS NOT NULL AND prev.price > 0
         THEN round((h.price - prev.price) / prev.price * 100, 2)
    END               AS change_pct,
    h.reason_tag,
    h.approval,
    h.approved_by,
    u.full_name       AS approved_by_name,
    h.decision_id,
    h.agent_version,
    lp.label_id,
    lp.result         AS shelf_result,
    lp.settled_at     AS shelf_settled_at
FROM price_history h
JOIN products p ON p.product_id = h.product_id
LEFT JOIN users u ON u.username = h.approved_by
LEFT JOIN LATERAL (
    SELECT price FROM price_history
    WHERE product_id = h.product_id AND ts < h.ts
    ORDER BY ts DESC LIMIT 1
) prev ON TRUE
LEFT JOIN LATERAL (
    SELECT label_id, result, settled_at FROM label_pushes
    WHERE product_id = h.product_id AND published_at >= h.ts - interval '5 seconds'
    ORDER BY published_at ASC LIMIT 1
) lp ON TRUE;
