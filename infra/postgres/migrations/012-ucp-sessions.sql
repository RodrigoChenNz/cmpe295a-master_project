CREATE TABLE ucp_sessions (
 checkout_id UUID PRIMARY KEY REFERENCES checkout_sessions,
 profile_url TEXT NOT NULL, capabilities JSONB NOT NULL, context JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
