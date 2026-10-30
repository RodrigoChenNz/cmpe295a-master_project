-- Upgrade pre-onboarding volumes without resetting existing catalogue or users.
CREATE TABLE IF NOT EXISTS store_categories (
 name TEXT PRIMARY KEY CHECK (char_length(name) BETWEEN 1 AND 60),
 sort_order INTEGER NOT NULL DEFAULT 0,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS store_categories_lower_idx ON store_categories(lower(name));
ALTER TABLE store_settings ADD COLUMN IF NOT EXISTS store_type TEXT
 CHECK (store_type IN ('grocery','convenience','pharmacy','electronics','hardware','apparel','other'));
ALTER TABLE store_settings ADD COLUMN IF NOT EXISTS onboarded_at TIMESTAMPTZ;
ALTER TABLE store_settings ADD COLUMN IF NOT EXISTS onboarded_by TEXT REFERENCES users(username);
ALTER TABLE store_settings ALTER COLUMN store_name DROP NOT NULL;
ALTER TABLE store_settings ALTER COLUMN store_name DROP DEFAULT;
-- Existing configured stores stay usable; fresh stores still complete onboarding.
UPDATE store_settings SET onboarded_at=now()
 WHERE onboarded_at IS NULL AND store_name IS NOT NULL
 AND EXISTS (SELECT 1 FROM products);
INSERT INTO store_categories(name,sort_order)
 SELECT name,row_number() OVER (ORDER BY name)::integer-1
 FROM (SELECT DISTINCT ON (lower(category)) category AS name FROM products
       WHERE category IS NOT NULL AND char_length(category) BETWEEN 1 AND 60
       ORDER BY lower(category),category) categories
 ON CONFLICT DO NOTHING;
