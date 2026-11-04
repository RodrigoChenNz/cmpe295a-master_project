"""MQTT bridge and the in-process event hub that feeds /ws/stream.

Two directions, deliberately separate:

  inbound   retail/proposal/+, retail/explain/+, retail/inventory/+
            persisted, then offered to the decision gate

  outbound  retail/price/+, retail/guardrail/+
            published by this service and by nothing else in the system

The hub in between exists so that an HTTP handler can announce a change without
knowing whether anyone is listening. A browser that is not connected misses
nothing it cannot refetch: every stream event is a hint to refetch, never the
only copy of a fact. That is why a dropped socket is not a correctness problem.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from .config import settings

log = logging.getLogger("shelfiq.bus")

# ─── event hub ──────────────────────────────────────────────────────────────

_subscribers: set[asyncio.Queue] = set()

# Last known broker state, so a newly connected browser is told the truth
# immediately rather than after the next publish.
_broker_state: dict[str, Any] = {
    "connected": False,
    "since": None,
    "error": None,
    "enabled": settings.mqtt_enabled,
    # The bridge's last traffic in either direction. Nothing else tracks this,
    # and `since` is the connect time, which answers a different question.
    "last_message_at": None,
    # What this process is *actually* connected to, which is not necessarily
    # what store_settings says: a manager can edit the stored host and the
    # running bridge keeps using its environment until it restarts. A card
    # captioned "Broker" has to show the real one.
    "host": settings.mqtt_host,
    "port": settings.mqtt_port,
    "tls": settings.mqtt_tls,
}


def _mark_traffic() -> None:
    _broker_state["last_message_at"] = datetime.now(timezone.utc).isoformat()


def broker_state() -> dict[str, Any]:
    return dict(_broker_state)


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def emit(event_type: str, **payload: Any) -> None:
    """Fan an event out to every connected browser.

    Never blocks and never raises. A subscriber whose queue has filled up is
    too far behind to be worth waiting for, so it is sent a `resync` marker and
    the backlog is dropped — the client refetches and is correct again.
    """
    event = {
        "type": event_type,
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                while True:
                    q.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait({"type": "resync", "ts": event["ts"]})


# ─── outbound publishing ────────────────────────────────────────────────────

_client: Any = None
_publish_backlog: list[tuple[str, bytes, int, bool]] = []
# Bounded, because a broker that has been down for an hour must not turn into
# an out-of-memory crash. Oldest entries are dropped first: a stale price for a
# SKU is worthless once a newer one exists for the same SKU.
_BACKLOG_MAX = 500


async def send_now(topic: str, payload: dict, *, qos=1, retain=False) -> bool:
    """Attempt publication without a volatile fallback; durable worker owns retry."""
    from retail.contracts import dumps
    if _client is None:
        return False
    try:
        await _client.publish(topic,dumps(payload).encode(),qos=qos,retain=retain)
        _mark_traffic()
        return True
    except Exception:
        log.exception('Broker publication failed')
        return False


async def publish(topic: str, payload: dict, *, qos: int = 1, retain: bool = False) -> bool:
    """Publish to MQTT. Returns True if it left the process.

    On failure the message is queued and replayed on reconnect, and the caller
    is told it did not go out so the UI can say "queued, not on the shelf yet"
    instead of claiming success. The database write has already happened by this
    point and is what makes the replay safe.
    """
    body = json.dumps(payload, separators=(",", ":")).encode()
    if _client is None:
        _enqueue(topic, body, qos, retain)
        return False
    try:
        await _client.publish(topic, body, qos=qos, retain=retain)
        _mark_traffic()
        return True
    except Exception as exc:  # noqa: BLE001 - any broker error means "queue it"
        log.warning("publish to %s failed (%s); queued", topic, exc)
        _enqueue(topic, body, qos, retain)
        return False


def _enqueue(topic: str, body: bytes, qos: int, retain: bool) -> None:
    # Supersede any queued message for the same topic: only the newest price
    # for a SKU is worth sending once the link comes back.
    for i, (t, *_rest) in enumerate(_publish_backlog):
        if t == topic:
            _publish_backlog[i] = (topic, body, qos, retain)
            return
    if len(_publish_backlog) >= _BACKLOG_MAX:
        _publish_backlog.pop(0)
    _publish_backlog.append((topic, body, qos, retain))
    emit("broker", queued=len(_publish_backlog), connected=_broker_state["connected"])


def queued_count() -> int:
    return len(_publish_backlog)


async def _drain_backlog() -> None:
    while _publish_backlog and _client is not None:
        topic, body, qos, retain = _publish_backlog[0]
        try:
            await _client.publish(topic, body, qos=qos, retain=retain)
        except Exception as exc:  # noqa: BLE001
            log.warning("replay of %s failed (%s); will retry", topic, exc)
            return
        _publish_backlog.pop(0)
    emit("broker", queued=0, connected=True)


# ─── inbound loop ───────────────────────────────────────────────────────────


async def run(stop: asyncio.Event) -> None:
    """Hold an MQTT connection open, reconnecting with backoff, until `stop`.

    Importing aiomqtt lazily keeps the service importable (and testable, and
    runnable against Postgres alone) on a machine with no broker library.
    """
    global _client

    if not settings.mqtt_enabled:
        log.info("MQTT disabled; the console runs against Postgres only")
        _broker_state.update(connected=False, enabled=False, error=None)
        return

    try:
        import aiomqtt
    except ImportError:
        log.warning("aiomqtt is not installed; running without the message bus")
        _broker_state.update(connected=False, enabled=False, error="aiomqtt not installed")
        return

    from . import ingest, db
    from retail.mqtt import options

    backoff = 1.0
    while not stop.is_set():
        try:
            # TLS is wired rather than merely stored: a Settings switch that
            # edits a column the bridge ignores would claim an encrypted link
            # this service does not open.
            async with aiomqtt.Client(
                hostname=settings.mqtt_host,
                port=settings.mqtt_port,
                identifier=settings.mqtt_client_id,
                **options(tls=settings.mqtt_tls,cafile=settings.mqtt_ca_file or None,
                    certfile=settings.mqtt_cert_file or None,keyfile=settings.mqtt_key_file or None,
                    username=settings.mqtt_username or None,password=settings.mqtt_password or None),
            ) as client:
                _client = client
                backoff = 1.0
                _broker_state.update(
                    connected=True,
                    since=datetime.now(timezone.utc).isoformat(),
                    error=None,
                )
                emit("broker", connected=True, queued=len(_publish_backlog))
                log.info("MQTT connected to %s:%s", settings.mqtt_host, settings.mqtt_port)

                await _drain_backlog()
                for topic in ("retail/proposal/+", "retail/explain/+", "retail/inventory/+", "retail/state/+", "retail/shelf/+"):
                    await client.subscribe(topic, qos=1)

                # Retained policy snapshots also reach agents after broker restarts.
                for product in await db.fetch("SELECT product_id,min_price,max_price,auto_approve_pct,policy_set_by FROM products WHERE min_price IS NOT NULL AND max_price IS NOT NULL"):
                    await send_now(f"retail/guardrail/{product['product_id']}", dict(
                        product_id=product['product_id'], min_price=float(product['min_price']), max_price=float(product['max_price']),
                        auto_approve_pct=float(product['auto_approve_pct']) if product['auto_approve_pct'] is not None else None,
                        set_by=product['policy_set_by'] or 'system',ts=datetime.now(timezone.utc).isoformat()),retain=True)
                async for message in client.messages:
                    if stop.is_set():
                        break
                    _mark_traffic()
                    await ingest.handle(str(message.topic), message.payload)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconnect on anything
            _broker_state.update(connected=False, error=str(exc))
            emit("broker", connected=False, queued=len(_publish_backlog), error=str(exc))
            log.warning("MQTT connection lost (%s); retrying in %.0fs", exc, backoff)
        finally:
            _client = None

        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, 30.0)

    _broker_state.update(connected=False)
