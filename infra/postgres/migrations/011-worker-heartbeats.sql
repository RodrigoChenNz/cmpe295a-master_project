CREATE TABLE worker_heartbeats (
 role TEXT PRIMARY KEY, last_seen TIMESTAMPTZ NOT NULL DEFAULT now(), model_version TEXT
);
