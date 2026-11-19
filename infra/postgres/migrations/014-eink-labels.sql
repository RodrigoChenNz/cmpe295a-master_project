-- E-ink labels over LoRa (edge/eink-e213, edge/lora-gateway, tools/lora_bridge.py).
-- Additive and idempotent: a fresh volume already has all of this from
-- 02-shelfiq.sql, an existing one gains it here.

-- The hardware behind a label: its radio's factory address. A device is one label at most.
ALTER TABLE shelf_labels ADD COLUMN IF NOT EXISTS device_id TEXT;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'shelf_labels_device_id_key') THEN
        ALTER TABLE shelf_labels ADD CONSTRAINT shelf_labels_device_id_key UNIQUE (device_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'shelf_labels_device_id_check') THEN
        ALTER TABLE shelf_labels ADD CONSTRAINT shelf_labels_device_id_check
            CHECK (device_id ~ '^[0-9A-F]{12}$');
    END IF;
END $$;

-- Tag added to / removed from a label through Shelf labels > Add labels.
ALTER TABLE label_events DROP CONSTRAINT IF EXISTS label_events_event_type_check;
ALTER TABLE label_events ADD CONSTRAINT label_events_event_type_check CHECK (event_type IN (
    'connected', 'disconnected', 'paired', 'unpaired',
    'firmware_updated', 'battery_low',
    'device_added', 'device_removed',
    'blink_requested', 'slot_cleared'
));

-- Tags heard while "Add labels" is open that are not set up yet.
CREATE TABLE IF NOT EXISTS label_discoveries (
    device_id     TEXT PRIMARY KEY CHECK (device_id ~ '^[0-9A-F]{12}$'),
    rssi_dbm      SMALLINT CHECK (rssi_dbm BETWEEN -120 AND 0),
    battery_pct   SMALLINT CHECK (battery_pct BETWEEN 0 AND 100),
    firmware      TEXT,
    button_at     TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Until when "Add labels" lists tags that aren't set up. NULL or past: closed.
ALTER TABLE store_settings ADD COLUMN IF NOT EXISTS label_pairing_until TIMESTAMPTZ;

-- E-ink labels check in every 30 minutes; 75 minutes forgives one missed
-- check-in. Stores still on the old 900-second default move with it.
ALTER TABLE store_settings ALTER COLUMN label_stale_secs SET DEFAULT 4500;
UPDATE store_settings SET label_stale_secs = 4500 WHERE label_stale_secs = 900;
