-- Closing the storefront must not re-enable direct simulator stock overwrites.
ALTER TABLE store_settings ADD COLUMN inventory_ledger_enabled BOOLEAN NOT NULL DEFAULT false;
UPDATE store_settings SET inventory_ledger_enabled=commerce_enabled;
CREATE TABLE inventory_opening_balances (
 product_id TEXT PRIMARY KEY REFERENCES products,
 quantity INT NOT NULL,
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO inventory_opening_balances(product_id,quantity)
 SELECT p.product_id,p.stock_on_hand-coalesce((SELECT sum(quantity) FROM inventory_movements WHERE product_id=p.product_id),0)
 FROM products p WHERE stock_on_hand IS NOT NULL AND (SELECT inventory_ledger_enabled FROM store_settings WHERE id);
