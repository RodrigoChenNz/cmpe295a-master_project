"""Inbound label traffic, from the edge (tools/lora_bridge.py today).

  retail/shelf/{label_id}     what a set-up label's glass shows, and its health.
                              A *draft* contract topic (contracts/shelf.schema.json):
                              it needs all four signatures before it merges.
  retail/label/discovered     a tag that isn't set up checked in. Console-owned,
                              like blink: only the console and the edge read it.
  retail/label/+/config       this service's own retained assignments, read back
                              to put right any that disagree with the database.

This is what turns the Labels screen from belief into observation: a label is
online because it reported in, and a push is confirmed because the label said
its glass shows that price, not because nobody complained.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from . import bus, db, gate

log = logging.getLogger("shelfiq.labels_in")

DEVICE_RE = re.compile(r"^[0-9A-F]{12}$")
LOW_BATTERY_PCT = 20


def _when(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _int_in(value: Any, lo: int, hi: int) -> int | None:
    """An optional integer field, dropped (not clamped) when out of range."""
    if value is None or isinstance(value, bool):
        return None
    v = int(value)
    return v if lo <= v <= hi else None


async def shelf_report(topic_label_id: str, p: dict) -> None:
    label_id = p["label_id"]
    if label_id != topic_label_id:
        log.warning("dropping shelf report: topic says %s, payload says %s",
                    topic_label_id, label_id)
        return

    ts = _when(p["ts"])
    in_sync = bool(p["in_sync"])
    rssi = _int_in(p.get("rssi_dbm"), -120, 0)
    battery = _int_in(p.get("battery_pct"), 0, 100)
    firmware = p.get("firmware")
    price = Decimal(str(p["price"])) if p.get("price") is not None else None
    decision_id = p.get("decision_id")
    rendered_at = _when(p["rendered_at"]) if p.get("rendered_at") else ts

    label = await db.fetchrow(
        "SELECT product_id, status, battery_pct, device_id FROM shelf_labels WHERE label_id = $1",
        label_id,
    )
    if label is None:
        log.info("shelf report for unknown label %s; dropped", label_id)
        return
    if label["device_id"] is None:
        # A tag answering as a label the database gives no tag: a leftover
        # from before a reset. config_seen() is sending it back to "Not set
        # up"; until then it must not make this label look online.
        log.info("shelf report for %s, which has no tag linked; dropped", label_id)
        return

    status = ("unassigned" if label["product_id"] is None
              else "online" if in_sync else "syncing")
    await db.execute(
        """UPDATE shelf_labels
              SET last_seen_at = $2, status = $3,
                  rssi_dbm = COALESCE($4, rssi_dbm),
                  -- NULL battery means "on USB, can't tell": keep the last reading.
                  battery_pct = COALESCE($5, battery_pct),
                  firmware = COALESCE($6, firmware)
            WHERE label_id = $1""",
        label_id, ts, status, rssi, battery, firmware,
    )

    if label["status"] == "offline":
        await db.execute(
            """INSERT INTO label_events (label_id, event_type, detail)
               VALUES ($1, 'connected', $2)""",
            label_id, {"rssi_dbm": rssi},
        )
    if battery is not None and battery < LOW_BATTERY_PCT and (
        label["battery_pct"] is None or label["battery_pct"] >= LOW_BATTERY_PCT
    ):
        await db.execute(
            """INSERT INTO label_events (label_id, event_type, detail)
               VALUES ($1, 'battery_low', $2)""",
            label_id, {"battery_pct": battery},
        )

    # The label says its glass shows this price: settle the pushes of it as
    # confirmed. A push the 45-second timer already marked failed is settled
    # too, because a label that missed its doorbell confirms at its next
    # regular check-in, minutes later, and "failed" would then be false.
    if in_sync and price is not None:
        settled = await db.fetch(
            """UPDATE label_pushes
                  SET result = 'confirmed', settled_at = $3
                WHERE label_id = $1 AND price = $2
                  AND result IN ('pending', 'failed')
                  AND ($4::uuid IS NULL OR decision_id IS NULL OR decision_id = $4::uuid)
            RETURNING id""",
            label_id, price, rendered_at, decision_id,
        )
        if settled:
            log.info("%s confirmed %d push(es) at %s", label_id, len(settled), price)

    bus.emit("labels", changed=[label_id])


async def config_seen(label_id: str, raw: bytes) -> None:
    """The database is the truth about which tag is which label; the broker
    keeps a retained copy for the edge. They drift apart when the database is
    reset (`make fresh-demo`) but the broker isn't: the old assignment keeps
    replaying, and a tag goes on answering as a label the console no longer
    gives it, so the console's changes never reach it. Each retained config is
    checked here as it arrives, and corrected or deleted.
    """
    if not raw:
        return   # a deletion, ours or anyone's
    try:
        seen = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        seen = {}
    row = await db.fetchrow(
        """SELECT l.device_id, l.product_id, p.name
             FROM shelf_labels l
             LEFT JOIN products p ON p.product_id = l.product_id
            WHERE l.label_id = $1""",
        label_id,
    )
    if row is None or row["device_id"] is None:
        log.info("retained config for %s names tag %s, which the database doesn't link:"
                 " deleting it", label_id, seen.get("device_id"))
        await gate.clear_label_config(label_id)
        return
    if (seen.get("device_id"), seen.get("product_id"), seen.get("display_name")) != (
        row["device_id"], row["product_id"], row["name"]
    ):
        log.info("retained config for %s disagrees with the database; republishing", label_id)
        await gate.publish_label_config(label_id)


async def discovered(p: dict) -> None:
    device = str(p["device_id"]).upper()
    if not DEVICE_RE.match(device):
        raise ValueError(f"device_id {device!r}")
    button = bool(p.get("button"))

    linked = await db.fetchval(
        "SELECT label_id FROM shelf_labels WHERE device_id = $1", device
    )
    if linked:
        # The console has this tag as a label, but the tag says it isn't set
        # up: its assignment never reached it, or the gateway lost it. Send
        # the config again rather than listing it as new.
        log.info("device %s is %s but reports not set up; republishing", device, linked)
        await gate.publish_label_config(linked)
        return

    is_open = await db.fetchval(
        "SELECT label_pairing_until > now() FROM store_settings LIMIT 1"
    )
    if not is_open:
        return

    await db.execute(
        """INSERT INTO label_discoveries
               (device_id, rssi_dbm, battery_pct, firmware, button_at)
           VALUES ($1, $2, $3, $4, CASE WHEN $5 THEN now() END)
           ON CONFLICT (device_id) DO UPDATE
              SET rssi_dbm = EXCLUDED.rssi_dbm,
                  battery_pct = COALESCE(EXCLUDED.battery_pct, label_discoveries.battery_pct),
                  firmware = EXCLUDED.firmware,
                  last_seen_at = now(),
                  button_at = COALESCE(EXCLUDED.button_at, label_discoveries.button_at)""",
        device,
        _int_in(p.get("rssi_dbm"), -120, 0),
        _int_in(p.get("battery_pct"), 0, 100),
        p.get("firmware"),
        button,
    )
    bus.emit("pairing", device_id=device, button=button)
