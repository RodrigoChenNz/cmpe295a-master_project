"""Connection pool and JSON/Decimal handling.

Money is NUMERIC in Postgres and Decimal in Python, and it is serialised to
JSON as a *string* with two decimal places. A float would round $12.99 into
something that is not $12.99, and this service is the only publisher of prices
that reach a shelf, so that is not an acceptable class of bug.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    # asyncpg hands back JSONB as a string unless told otherwise.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def connect() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=2,
            max_size=10,
            init=_init_connection,
        )
    return _pool


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool not initialised")
    return _pool


async def fetch(query: str, *args: Any) -> list[dict[str, Any]]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [dict(r) for r in rows]


async def fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(query, *args)
    return dict(row) if row else None


async def fetchval(query: str, *args: Any) -> Any:
    async with pool().acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args: Any) -> str:
    async with pool().acquire() as conn:
        return await conn.execute(query, *args)


def money(value: Decimal | float | int | None) -> str | None:
    """Render money for the wire: a two-decimal string, never a float."""
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def money_num(value: Decimal | float | int | None) -> float | None:
    """Render money as a JSON number, for the MQTT contracts, which specify
    `"type": "number", "multipleOf": 0.01`. Quantised first so the float is
    always an exact two-decimal value."""
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal("0.01")))
