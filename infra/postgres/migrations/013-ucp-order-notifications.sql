ALTER TABLE commerce_webhook_outbox ADD COLUMN target_url TEXT;
ALTER TABLE commerce_webhook_outbox ADD COLUMN signing_profile TEXT NOT NULL DEFAULT 'local_hmac' CHECK(signing_profile IN ('local_hmac','ucp_ed25519'));
