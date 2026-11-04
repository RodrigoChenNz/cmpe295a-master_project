"""Checksummed, transactionally applied, additive database migrations."""

import hashlib
from pathlib import Path
from . import db


async def apply():
    candidates = [Path("/srv/migrations")] + [
        parent / "infra/postgres/migrations" for parent in Path(__file__).resolve().parents
    ]
    directory = next((p for p in candidates if p.exists()), candidates[0])
    if not directory.exists():
        raise RuntimeError("Database migrations directory is missing")
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(2952026)")
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            for path in sorted(directory.glob("*.sql")):
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                previous = await conn.fetchval(
                    "SELECT sha256 FROM schema_migrations WHERE name=$1", path.name
                )
                if previous and previous != digest:
                    raise RuntimeError(f"Applied migration was edited: {path.name}")
                if previous:
                    continue
                await conn.execute(path.read_text())
                await conn.execute(
                    "INSERT INTO schema_migrations(name,sha256) VALUES($1,$2)",
                    path.name,
                    digest,
                )
