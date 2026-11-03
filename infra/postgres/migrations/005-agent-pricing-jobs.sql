CREATE TABLE agent_price_jobs (
 job_id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES agent_runs,
 product_id TEXT NOT NULL REFERENCES products, decision_id UUID NOT NULL UNIQUE,
 payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), sent_at TIMESTAMPTZ,
 UNIQUE(run_id,product_id)
);
