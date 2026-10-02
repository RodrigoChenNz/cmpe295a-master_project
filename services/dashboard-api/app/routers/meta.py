"""Store settings, nav badge counts, broker state, and the sample-data flag.

One request the shell makes on load and then on every stream hint, so the nav
badge and the broker banner never disagree with the screen beside them.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import auth, bus, db, serialize

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta")
async def meta(
    user: Annotated[auth.CurrentUser | None, Depends(auth.optional_user)],
) -> dict:
    store = await db.fetchrow("SELECT * FROM store_settings LIMIT 1")

    # While the seed's marker row exists, every figure on screen is sample
    # data and the console says so in a banner. Nothing here invents a number.
    # The table is absent in any database that was never seeded, so its
    # existence is checked rather than assumed.
    sample = None
    if await _has_marker_table():
        sample = await db.fetchrow("SELECT note, seeded_at FROM sample_data_marker LIMIT 1")

    counts = await db.fetchrow(
        """SELECT
             (SELECT count(*) FROM proposals WHERE status = 'pending')     AS pending,
             (SELECT count(*) FROM shelf_labels WHERE status = 'failed')   AS labels_failed,
             (SELECT count(*) FROM shelf_labels WHERE status = 'offline')  AS labels_offline,
             (SELECT count(*) FROM products
               WHERE archived_at IS NULL AND stock_on_hand = 0)            AS out_of_stock"""
    )

    broker = bus.broker_state()
    broker["queued"] = bus.queued_count()

    return {
        "user": user.as_dict() if user else None,
        "store": serialize.row(store) if store else None,
        "counts": dict(counts) if counts else {},
        "broker": broker,
        "sample_data": serialize.row(sample) if sample else None,
    }


async def _has_marker_table() -> bool:
    return bool(
        await db.fetchval("SELECT to_regclass('public.sample_data_marker') IS NOT NULL")
    )


class StoreSettingsBody(BaseModel):
    store_name: str = Field(min_length=1, max_length=120)
    store_code: str | None = Field(default=None, max_length=40)
    timezone: str = Field(min_length=1, max_length=64)
    mqtt_host: str = Field(min_length=1, max_length=255)
    mqtt_port: int = Field(ge=1, le=65535)
    mqtt_tls: bool = False
    label_stale_secs: int = Field(ge=30, le=86400)


@router.put("/settings/store")
async def update_store(
    body: StoreSettingsBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    # Changing mqtt_host here records the manager's intent; the running bridge
    # still uses the process environment, because a bad value typed into a form
    # must not be able to cut the service off from the bus until someone
    # restarts it deliberately.
    row = await db.fetchrow(
        """UPDATE store_settings
              SET store_name = $1, store_code = $2, timezone = $3,
                  mqtt_host = $4, mqtt_port = $5, mqtt_tls = $6,
                  label_stale_secs = $7, updated_at = now()
            WHERE id
        RETURNING *""",
        body.store_name,
        body.store_code,
        body.timezone,
        body.mqtt_host,
        body.mqtt_port,
        body.mqtt_tls,
        body.label_stale_secs,
    )
    bus.emit("settings", set_by=user.username)
    return {"store": serialize.row(row) if row else None}
