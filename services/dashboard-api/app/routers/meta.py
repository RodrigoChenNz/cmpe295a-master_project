"""Store settings, nav badge counts, broker state, and the sample-data flag.

One request the shell makes on load and then on every stream hint, so the nav
badge and the broker banner never disagree with the screen beside them.
"""

from __future__ import annotations

import asyncio
import time
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
               WHERE archived_at IS NULL AND stock_on_hand = 0)            AS out_of_stock,
             (SELECT count(*) FROM products
               WHERE archived_at IS NULL AND location_id IS NULL)          AS unassigned_products,
             (SELECT count(*) FROM products WHERE archived_at IS NULL)     AS products,
             (SELECT count(*) FROM locations)                               AS locations"""
    )
    # Every screen names the levels the store chose, and the shell prompts a
    # manager to set them up before anything else — so they travel with meta.
    levels = await db.fetch("SELECT depth, name FROM location_levels ORDER BY depth")

    broker = bus.broker_state()
    broker["queued"] = await db.fetchval("SELECT count(*) FROM message_outbox WHERE sent_at IS NULL")

    return {
        "user": user.as_dict() if user else None,
        "store": serialize.row(store) if store else None,
        "counts": dict(counts) if counts else {},
        "broker": broker,
        "sample_data": serialize.row(sample) if sample else None,
        "layout": {
            "configured": bool(levels),
            "levels": [dict(r) for r in levels],
        },
    }


async def _has_marker_table() -> bool:
    return bool(
        await db.fetchval("SELECT to_regclass('public.sample_data_marker') IS NOT NULL")
    )


class StoreSettingsBody(BaseModel):
    store_name: str = Field(min_length=1, max_length=120)
    store_code: str | None = Field(default=None, max_length=40)
    timezone: str = Field(min_length=1, max_length=64)
    # The column has existed since the first migration and no endpoint has ever
    # accepted it, so the Settings field would be permanently read-only.
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
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
                  label_stale_secs = $7, currency = $8, updated_at = now()
            WHERE id
        RETURNING *""",
        body.store_name,
        body.store_code,
        body.timezone,
        body.mqtt_host,
        body.mqtt_port,
        body.mqtt_tls,
        body.label_stale_secs,
        body.currency,
    )
    bus.emit("settings", set_by=user.username)
    return {"store": serialize.row(row) if row else None}


@router.get("/settings/store")
async def get_store_settings(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
) -> dict:
    """The stored settings, and what the running service is actually doing.

    These are two different facts and the Settings screen has to show both:
    `update_store` records a manager's intent, but the MQTT bridge keeps using
    its process environment until the service restarts. A form that showed only
    the stored host would imply this service is talking to it.
    """
    store = await db.fetchrow("SELECT * FROM store_settings LIMIT 1")
    broker = bus.broker_state() | {"queued": bus.queued_count()}
    return {
        "store": serialize.row(store) if store else None,
        "broker": broker,
        "effective": {
            "host": broker.get("host"),
            "port": broker.get("port"),
            "tls": broker.get("tls"),
        },
        "restart_required": bool(
            store
            and (
                store["mqtt_host"] != broker.get("host")
                or store["mqtt_port"] != broker.get("port")
                or store["mqtt_tls"] != broker.get("tls")
            )
        ),
    }


class MqttTestBody(BaseModel):
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    tls: bool | None = None


@router.post("/settings/mqtt/test")
async def test_mqtt(
    body: MqttTestBody,
    _user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    """Open a real MQTT connection and close it again.

    A connect rather than a TCP probe, because an open socket proves only that
    *something* is listening; completing a CONNECT/CONNACK proves it is a
    broker. It uses its own client and never touches the running bridge's
    connection or its publish backlog.

    **Returns HTTP 200 even when the broker is down**, with `ok: false`. "Is the
    broker reachable?" has a true answer of "no"; that is not an API error, and
    a 5xx would make the UI say something went wrong with the console.
    """
    store = await db.fetchrow("SELECT mqtt_host, mqtt_port, mqtt_tls FROM store_settings LIMIT 1")
    host = body.host or (store["mqtt_host"] if store else "localhost")
    port = body.port or (store["mqtt_port"] if store else 1883)
    tls = body.tls if body.tls is not None else bool(store and store["mqtt_tls"])

    started = time.perf_counter()
    try:
        import aiomqtt
    except ImportError:
        # Fall back to proving the port answers, and say which probe ran so the
        # UI can report "TCP reachable; MQTT not verified" rather than "ok".
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=4.0
            )
            writer.close()
            await writer.wait_closed()
            ok, detail = True, "Port is open. The MQTT client library is not installed, so the broker itself was not verified."
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, str(exc) or exc.__class__.__name__
        return {
            "ok": ok, "probe": "tcp", "host": host, "port": port, "tls": tls,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "detail": detail,
        }

    try:
        tls_params = aiomqtt.TLSParameters() if tls else None
        client = aiomqtt.Client(
            hostname=host, port=port, identifier="dashboard-api-probe",
            tls_params=tls_params,
        )
        await asyncio.wait_for(client.__aenter__(), timeout=4.0)
        await client.__aexit__(None, None, None)
        ok, detail = True, "Connected and disconnected cleanly."
    except asyncio.TimeoutError:
        ok, detail = False, "No response within 4 seconds."
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, str(exc) or exc.__class__.__name__

    return {
        "ok": ok, "probe": "mqtt-connect", "host": host, "port": port, "tls": tls,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "detail": detail,
    }
