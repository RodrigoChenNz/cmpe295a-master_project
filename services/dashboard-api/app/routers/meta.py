"""Store settings, nav badge counts, broker state, and the sample-data flag.

One request the shell makes on load and then on every stream hint, so the nav
badge and the broker banner never disagree with the screen beside them.
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
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
        "store": _store_for(user, store),
        "counts": dict(counts) if counts else {},
        # Whether the shelf network is up is everyone's business (the banner);
        # where it lives is the super admin's.
        "broker": broker if _sees_network(user) else _without(broker, _BROKER_ADDRESS),
        "sample_data": serialize.row(sample) if sample else None,
        "layout": {
            "configured": bool(levels),
            "levels": [dict(r) for r in levels],
        },
        "onboarding": {"completed": bool(store and store["onboarded_at"])},
    }


# The broker's address is infrastructure, set once by whoever installed the
# system. Managers and staff see whether the shelf network is up, never where it
# is or how to change it.
_NETWORK_FIELDS = ("mqtt_host", "mqtt_port", "mqtt_tls")
_BROKER_ADDRESS = ("host", "port", "tls")


def _sees_network(user: auth.CurrentUser | None) -> bool:
    return bool(user and user.role == "superadmin")


def _without(d: dict, keys: tuple[str, ...]) -> dict:
    return {k: v for k, v in d.items() if k not in keys}


def _store_for(user: auth.CurrentUser | None, row) -> dict | None:
    if not row:
        return None
    store = serialize.row(row)
    return store if _sees_network(user) else _without(store, _NETWORK_FIELDS)


async def _has_marker_table() -> bool:
    return bool(
        await db.fetchval("SELECT to_regclass('public.sample_data_marker') IS NOT NULL")
    )


StoreType = Literal[
    "grocery", "convenience", "pharmacy", "electronics", "hardware", "apparel", "other"
]


class StoreSettingsBody(BaseModel):
    store_name: str = Field(min_length=1, max_length=120)
    store_code: str | None = Field(default=None, max_length=40)
    store_type: StoreType | None = None
    timezone: str = Field(min_length=1, max_length=64)
    # USD only, because the shared MQTT contracts are: price.schema.json and
    # proposal.schema.json both pin `currency` to ["USD"], and gate.py publishes
    # exactly that. Storing anything else would describe a store whose every
    # published price breaks the contract. Widening it is a contract change.
    currency: Literal["USD"] = "USD"
    label_stale_secs: int = Field(ge=30, le=86400)


@router.put("/settings/store")
async def update_store(
    body: StoreSettingsBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    # The broker is not in here: it has its own endpoint, super admin only.
    row = await db.fetchrow(
        """UPDATE store_settings
              SET store_name = $1, store_code = $2, timezone = $3,
                  label_stale_secs = $4, currency = $5, store_type = $6,
                  updated_at = now()
            WHERE id
        RETURNING *""",
        body.store_name,
        body.store_code,
        body.timezone,
        body.label_stale_secs,
        body.currency,
        body.store_type,
    )
    bus.emit("settings", set_by=user.username)
    return {"store": _store_for(user, row)}


class NetworkSettingsBody(BaseModel):
    mqtt_host: str = Field(min_length=1, max_length=255)
    mqtt_port: int = Field(ge=1, le=65535)
    mqtt_tls: bool = False


@router.put("/settings/network")
async def update_network(
    body: NetworkSettingsBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_superadmin)],
) -> dict:
    # This records intent; the running bridge still uses the process
    # environment, because a bad value typed into a form must not be able to
    # cut the service off from the bus until someone restarts it deliberately.
    row = await db.fetchrow(
        """UPDATE store_settings
              SET mqtt_host = $1, mqtt_port = $2, mqtt_tls = $3, updated_at = now()
            WHERE id
        RETURNING *""",
        body.mqtt_host,
        body.mqtt_port,
        body.mqtt_tls,
    )
    bus.emit("settings", set_by=user.username)
    return {"store": _store_for(user, row)}


@router.post("/onboarding/complete")
async def complete_onboarding(
    user: Annotated[auth.CurrentUser, Depends(auth.require_superadmin)],
) -> dict:
    """Close the first-run store setup wizard and open the console.

    Refused until the two things the wizard cannot skip are done: the store has
    a name, and its layout levels exist (no product can be added without them).
    Everything else the wizard offers has a working default. Idempotent: a
    second call keeps the first completion's time and author.
    """
    store = await db.fetchrow("SELECT store_name, onboarded_at FROM store_settings LIMIT 1")
    if store is None or not store["store_name"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Name the store before finishing setup.")
    if not await db.fetchval("SELECT EXISTS (SELECT 1 FROM location_levels)"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Set up the store layout before finishing setup."
        )
    if store["onboarded_at"] is None:
        await db.execute(
            """UPDATE store_settings
                  SET onboarded_at = now(), onboarded_by = $1, updated_at = now()
                WHERE id AND onboarded_at IS NULL""",
            user.username,
        )
        await db.execute(
            """INSERT INTO agent_audit_log (event_type, actor, detail)
               VALUES ('guardrail_set', $1, $2)""",
            user.username,
            {"scope": "store", "onboarding": "completed"},
        )
        bus.emit("settings", set_by=user.username)
    row = await db.fetchrow("SELECT * FROM store_settings LIMIT 1")
    return {"store": _store_for(user, row)}


@router.get("/settings/store")
async def get_store_settings(
    user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
) -> dict:
    """The stored settings, and what the running service is actually doing.

    These are two different facts and the Settings screen has to show both:
    `update_store` records a manager's intent, but the MQTT bridge keeps using
    its process environment until the service restarts. A form that showed only
    the stored host would imply this service is talking to it.

    Everyone but a super admin gets the store without the broker's address and
    without the stored-versus-running comparison, which is all about it.
    """
    store = await db.fetchrow("SELECT * FROM store_settings LIMIT 1")
    broker = bus.broker_state() | {"queued": bus.queued_count()}
    if not _sees_network(user):
        return {
            "store": _store_for(user, store),
            "broker": _without(broker, _BROKER_ADDRESS),
        }
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
    _user: Annotated[auth.CurrentUser, Depends(auth.require_superadmin)],
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
