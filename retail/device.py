"""Durable display emulator: ordered publications, exact rendering, explicit emulation."""

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def render_lines(display_name, price, reason_tag):
    line1 = display_name.upper()[:16]
    money = f"${Decimal(str(price)):.2f}"
    room = 16 - len(money) - 1
    line2 = f"{money} {reason_tag[:room]}" if room >= 4 and reason_tag else money
    return line1, line2[:16]


class DisplayStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS displays(label_id TEXT PRIMARY KEY, publication_id INTEGER NOT NULL, payload TEXT NOT NULL)"
        )

    def receive(self, label_id, p):
        previous = self.conn.execute(
            "SELECT publication_id,payload FROM displays WHERE label_id=?", (label_id,)
        ).fetchone()
        if previous and previous[0] > p["publication_id"]:
            return json.loads(previous[1])
        if previous and previous[0] == p["publication_id"]:
            return json.loads(previous[1])
        line1, line2 = render_lines(
            p.get("display_name", p["product_id"]), p["price"], p["reason_tag"]
        )
        now = datetime.now(timezone.utc).isoformat()
        state = dict(
            label_id=label_id,
            product_id=p["product_id"],
            event="rendered",
            publication_id=p["publication_id"],
            price=float(p["price"]),
            line1=line1,
            line2=line2,
            rendered_at=now,
            ts=now,
            firmware="emulator-1.0",
            emulated=True,
            rssi=-45,
            battery=100,
        )
        with self.conn:
            self.conn.execute(
                "INSERT INTO displays VALUES(?,?,?) ON CONFLICT(label_id) DO UPDATE SET publication_id=excluded.publication_id,payload=excluded.payload",
                (label_id, p["publication_id"], json.dumps(state)),
            )
        return state

    def all(self):
        return [
            json.loads(r[0])
            for r in self.conn.execute("SELECT payload FROM displays ORDER BY label_id")
        ]
