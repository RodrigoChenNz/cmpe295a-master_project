"""Durable at-least-once delivery, ordered by commit within each product.

The database transaction that records a price also inserts its outbound job.
A process crash can cause a replay after publication; publication_id lets shelf
consumers deduplicate that replay. One advisory lock serializes delivery workers.
"""

import asyncio
import contextlib
import logging
from . import db, bus

log = logging.getLogger("shelfiq.outbox")


async def enqueue(conn, topic, payload, *, qos=1, retained=False):
    return await conn.fetchval(
        "INSERT INTO message_outbox(topic,payload,qos,retained) VALUES($1,$2,$3,$4) RETURNING id",
        topic,
        payload,
        qos,
        retained,
    )


async def drain():
    if not bus.broker_state().get("connected"):
        return 0
    count = 0
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(2952027)"):
                return 0
            rows = await conn.fetch(
                "SELECT * FROM message_outbox WHERE sent_at IS NULL ORDER BY id LIMIT 100 FOR UPDATE"
            )
            for row in rows:
                delivered = await bus.send_now(
                    row["topic"], row["payload"], qos=row["qos"], retain=row["retained"]
                )
                await conn.execute(
                    "UPDATE message_outbox SET attempts=attempts+1,last_error=$2 WHERE id=$1",
                    row["id"],
                    None if delivered else "Broker unavailable",
                )
                if not delivered:
                    break
                await conn.execute(
                    "UPDATE message_outbox SET sent_at=now() WHERE id=$1", row["id"]
                )
                if row["topic"].startswith("retail/price/"):
                    await conn.execute(
                        "UPDATE label_pushes SET sent_at=COALESCE(sent_at,now()) WHERE publication_id=$1",
                        row["payload"].get("publication_id"),
                    )
                count += 1
    if count:
        bus.emit("price", published=count)
    return count


async def run(stop):
    while not stop.is_set():
        try:
            await drain()
        except Exception:
            log.exception("Outbox delivery failed; jobs remain durable")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=0.25)
