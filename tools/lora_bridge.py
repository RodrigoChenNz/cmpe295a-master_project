#!/usr/bin/env python3
"""Bridge the LoRa gateway (USB serial) to the MQTT bus.

    make lora
    uv run --with aiomqtt --with pyserial tools/lora_bridge.py [--port /dev/cu.usbserial-0001]

The shelf labels have no Wi-Fi. They talk LoRa to the gateway
(edge/lora-gateway), which is plugged into this machine over USB. This script is
the gateway's only link to the rest of the system:

    retail/price/+            -> gateway   the approved price for each product
    retail/label/+/config     -> gateway   which device is which label, and its product
    retail/label/pairing      -> gateway   "Add labels": ring every label not set up
    retail/label/+/blink      -> gateway   rung at once, shown at its next check-in
    check-in of a set-up label     -> retail/shelf/{label_id}    what its glass shows, signal, battery
    check-in of a label not set up -> retail/label/discovered    so the console can list it

Prices and configs are retained on the broker, so this script holds the full
picture as soon as it subscribes, and replays it to a gateway that has just
booted with empty tables.

It runs on the host rather than in Docker because Docker Desktop on macOS
cannot pass a USB serial device into a container.

**`retail/shelf/{label_id}` is a proposed contract topic** (contracts/
shelf.schema.json, draft) and needs all four signatures before it is merged.
The `retail/label/...` topics are console-owned, like blink.

Money is parsed as Decimal and sent to the gateway as whole cents, so a price
never passes through binary float (contracts/README.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

try:
    import aiomqtt
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit(
        "lora_bridge needs aiomqtt and pyserial.\n"
        "  uv run --with aiomqtt --with pyserial tools/lora_bridge.py\n"
        "or: make lora"
    )

PRICE_TOPIC = "retail/price/+"
BLINK_TOPIC = "retail/label/+/blink"
CONFIG_TOPIC = "retail/label/+/config"
PAIRING_TOPIC = "retail/label/pairing"
SUBSCRIPTIONS = (PRICE_TOPIC, BLINK_TOPIC, CONFIG_TOPIC, PAIRING_TOPIC)

ID_LEN = 11          # edge/common/shelf_link.h: 12-byte fields, NUL-padded
REASON_LEN = 16      # contracts/price.schema.json: reason_tag maxLength
NAME_LEN = 32        # shelf_link.h: the product name on the glass
DEVICE_RE = re.compile(r"^[0-9A-F]{12}$")
PING_EVERY = 5.0     # the gateway shows "not connected" after 12 s of silence
CONFIG_SETTLE_SECS = 3.0  # retained configs arrive within this of subscribing
PROBE_SECS = 3.0     # long enough for a gateway that resets when the port opens

# USB serial ports an ESP32 board shows up as: CP2102 (V3), native USB (V4, E213).
PORT_PATTERNS = re.compile(
    r"/dev/(cu\.(usbserial|usbmodem|SLAB_USBtoUART|wchusbserial)|tty(USB|ACM))"
)


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {msg}", flush=True)


def money(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def price_version(cents: int, reason: str) -> int:
    """linkPriceVersion() in edge/common/shelf_link.h, which must stay in step.

    It identifies a price as a label draws it, so the version a label reports
    can be matched to the price, and the decision, it is showing."""
    h = 2166136261
    for i in range(4):
        h ^= (cents >> (8 * i)) & 0xFF
        h = (h * 16777619) & 0xFFFFFFFF
    for b in reason.encode()[:REASON_LEN]:
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    return h or 1


# The label firmware's board, in the top two bits of its version byte.
BOARDS = {0: "e213", 1: "wpaper"}


def firmware_name(byte: int) -> str:
    return f"{BOARDS.get(byte >> 6, 'board' + str(byte >> 6))}-{byte & 0x3F}"


def glass_name(name: str) -> str:
    """The product name as the label's font can draw it: ASCII, 32 bytes."""
    return name.encode("ascii", "replace").decode()[:NAME_LEN]


# Resting voltage of a single-cell LiPo against charge left, from typical
# discharge curves. Voltage is only a rough guide to charge (about +-10%),
# but it is what the board can measure, and shelf_labels.battery_pct holds
# a percentage.
LIPO_CURVE = [
    (4200, 100), (4150, 95), (4110, 90), (4080, 85), (4020, 80), (3980, 75),
    (3950, 70), (3910, 65), (3870, 60), (3850, 55), (3840, 50), (3820, 45),
    (3800, 40), (3790, 35), (3770, 30), (3750, 25), (3730, 20), (3710, 15),
    (3690, 10), (3610, 5), (3270, 0),
]


def battery_pct(mv: int) -> int:
    if mv >= LIPO_CURVE[0][0]:
        return 100
    for (hi_mv, hi_pct), (lo_mv, lo_pct) in zip(LIPO_CURVE, LIPO_CURVE[1:]):
        if mv >= lo_mv:
            return round(lo_pct + (hi_pct - lo_pct) * (mv - lo_mv) / (hi_mv - lo_mv))
    return 0


# ─── the gateway end ────────────────────────────────────────────────────────


class Gateway:
    """The serial link, or None while there isn't one."""

    def __init__(self) -> None:
        self.port: serial.Serial | None = None

    def send(self, obj: dict) -> bool:
        if self.port is None:
            return False
        try:
            self.port.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())
            return True
        except (serial.SerialException, OSError) as exc:
            log(f"gateway write failed: {exc}")
            self.close()
            return False

    def close(self) -> None:
        if self.port is not None:
            try:
                self.port.close()
            except Exception:  # noqa: BLE001
                pass
        self.port = None


def open_port(device: str) -> serial.Serial:
    # DTR/RTS low before opening: on boards with an auto-reset circuit, raising
    # them on open resets the ESP32. The probe tolerates that, but there's no
    # reason to cause it.
    port = serial.Serial()
    port.port = device
    port.baudrate = 115200
    port.timeout = 0.5
    port.dtr = False
    port.rts = False
    port.open()
    return port


class NotAGateway(Exception):
    """The port opened, but nothing on it answered as a gateway."""


def probe(device: str) -> serial.Serial | None:
    """Open `device` and keep it if a gateway answers. Labels never do.

    Returns None if the port couldn't be opened (busy, or gone), and raises
    NotAGateway if it opened and stayed silent.
    """
    try:
        port = open_port(device)
    except (serial.SerialException, OSError):
        return None
    try:
        port.reset_input_buffer()
        port.write(b'{"t":"who"}\n')
        deadline = time.monotonic() + PROBE_SECS
        while time.monotonic() < deadline:
            line = port.readline().decode(errors="replace").strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("t") == "hello" and msg.get("role") == "gateway":
                return port
    except (serial.SerialException, OSError):
        port.close()
        return None
    port.close()
    raise NotAGateway(device)


def find_gateway(explicit: str | None, skip: set[tuple[str, str | None]]) -> serial.Serial | None:
    """Look for the gateway, leaving alone every port already found not to be one.

    Each probe holds a port open for up to PROBE_SECS. Probing a label again
    every few seconds would keep colliding with `pio run -t upload` and
    `pio device monitor` on that label, so a port that answered as something
    else is skipped until it's unplugged. `skip` holds (device, serial number)
    and is pruned here of ports that have gone away.
    """
    if explicit:
        try:
            return probe(explicit)
        except NotAGateway:
            return None
    present = {(i.device, i.serial_number) for i in list_ports.comports()
               if PORT_PATTERNS.match(i.device)}
    skip &= present
    for key in sorted(present - skip):
        try:
            port = probe(key[0])
        except NotAGateway:
            skip.add(key)
            log(f"{key[0]} isn't the gateway; leaving it alone until it's replugged")
            continue
        if port is not None:
            return port
    return None


# ─── state shared by both halves ────────────────────────────────────────────


class Bridge:
    def __init__(self, port_arg: str | None) -> None:
        self.port_arg = port_arg
        self.gateway = Gateway()
        self.not_gateways: set[tuple[str, str | None]] = set()
        self.mqtt: aiomqtt.Client | None = None
        # product_id -> (cents, reason): the retained prices, replayed to a
        # gateway that has just booted with an empty table.
        self.prices: dict[str, tuple[int, str]] = {}
        # product_id -> {version: (cents, decision_id)}: every price seen, so a
        # label reporting an older version can still be said to show it.
        self.history: dict[str, dict[int, tuple[int, str | None]]] = {}
        # label_id -> {"device", "product_id", "name"}: the retained configs.
        self.configs: dict[str, dict] = {}
        # True once the retained configs have had time to arrive after
        # connecting. Until then a missing config means "not here yet", not
        # "not a label any more".
        self.configs_ready = False
        # label_id -> (version, first seen): when the glass started showing it.
        self.rendered: dict[str, tuple[int, str]] = {}
        # label_id -> True once its glass was seen showing the current price,
        # so "confirmed" is announced once per change rather than every check-in.
        self.confirmed: dict[str, bool] = {}

    def publish(self, topic: str, payload: dict) -> None:
        if self.mqtt is None:
            return
        body = json.dumps(payload, separators=(",", ":"))
        asyncio.get_running_loop().create_task(self._publish(topic, body))

    async def _publish(self, topic: str, body: str) -> None:
        try:
            await self.mqtt.publish(topic, body, qos=1)
        except Exception as exc:  # noqa: BLE001
            log(f"publish to {topic} failed: {exc}")

    def send_price(self, product_id: str) -> None:
        cents, reason = self.prices[product_id]
        self.gateway.send(
            {"t": "price", "product_id": product_id, "cents": cents, "reason": reason}
        )

    def send_assign(self, label_id: str) -> None:
        c = self.configs[label_id]
        self.gateway.send({"t": "assign", "device": c["device"], "label_id": label_id,
                           "product_id": c["product_id"], "name": c["name"]})

    # ── MQTT -> gateway ──

    def on_price(self, topic: str, payload: bytes) -> None:
        topic_id = topic.rsplit("/", 1)[-1]
        if not payload:
            # A cleared retained message. The gateway keeps the last price it
            # was given until it reboots; the shelf keeps its price either way.
            self.prices.pop(topic_id, None)
            log(f"{topic_id}: retained price cleared")
            return
        try:
            msg = json.loads(payload, parse_float=Decimal)
            product_id = msg["product_id"]
            price = Decimal(msg["price"])
            reason = msg["reason_tag"]
            currency = msg["currency"]
        except (json.JSONDecodeError, KeyError, TypeError, InvalidOperation) as exc:
            log(f"{topic}: malformed price ignored ({exc})")
            return
        problem = None
        if product_id != topic_id:
            problem = f"topic says {topic_id}, payload says {product_id}"
        elif currency != "USD":
            problem = f"currency {currency!r}"
        elif not isinstance(reason, str) or len(reason) > REASON_LEN:
            problem = f"reason_tag {reason!r}"
        elif price <= 0 or price != price.quantize(Decimal("0.01")):
            problem = f"price {price}"
        elif len(product_id) > ID_LEN:
            problem = f"product_id longer than the radio's {ID_LEN} characters"
        if problem:
            log(f"{topic}: price ignored, {problem}")
            return

        cents = int(price * 100)
        self.prices[product_id] = (cents, reason)
        self.history.setdefault(product_id, {})[price_version(cents, reason)] = (
            cents, msg.get("decision_id"))
        self.send_price(product_id)
        said = f'"{reason}"' if reason else "(no reason)"
        log(f"{product_id}: price {money(cents)} {said}"
            f"{'' if self.gateway.port else ' (held until the gateway connects)'}")

    def on_config(self, topic: str, payload: bytes) -> None:
        label_id = topic.split("/")[2]
        if not payload:
            # Removed in the console: the device goes back to "Not set up".
            old = self.configs.pop(label_id, None)
            if old:
                self.gateway.send({"t": "forget", "device": old["device"]})
                log(f"{label_id}: device {old['device']} removed")
            return
        try:
            msg = json.loads(payload)
            device = str(msg["device_id"]).upper()
            product_id = msg.get("product_id") or ""
            name = glass_name(msg.get("display_name") or "")
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
            log(f"{topic}: malformed config ignored ({exc})")
            return
        if not DEVICE_RE.match(device) or len(label_id) > ID_LEN or len(product_id) > ID_LEN:
            log(f"{topic}: config ignored, bad device or id")
            return
        old = self.configs.get(label_id)
        if old and old["device"] != device:
            # A tag replaced: the old one stops being this label.
            self.gateway.send({"t": "forget", "device": old["device"]})
        self.configs[label_id] = {"device": device, "product_id": product_id, "name": name}
        self.send_assign(label_id)
        log(f"{label_id}: device {device}, {product_id or 'no product'}"
            f"{f' ({name})' if name else ''}")

    def on_pairing(self, payload: bytes) -> None:
        try:
            until = datetime.fromisoformat(
                str(json.loads(payload)["open_until"]).replace("Z", "+00:00"))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return
        if until > datetime.now(timezone.utc):
            self.gateway.send({"t": "join"})
            log(f"Add labels: ringing every label not set up (open until "
                f"{until.astimezone().strftime('%H:%M')})")

    def on_blink(self, topic: str, payload: bytes) -> None:
        label_id = topic.split("/")[2]
        try:
            seconds = int(json.loads(payload).get("seconds", 3))
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            seconds = 3
        if len(label_id) > ID_LEN:
            log(f"{label_id}: blink ignored, label_id too long for the radio")
            return
        if self.gateway.send({"t": "blink", "label_id": label_id, "seconds": seconds}):
            log(f"{label_id}: blink {seconds} s, ringing it")
        else:
            log(f"{label_id}: blink dropped, no gateway connected")

    # ── gateway -> MQTT and stdout ──

    def on_gateway_line(self, line: str) -> None:
        if not line.startswith("{"):
            return   # "# " lines are the gateway talking to a person at a monitor
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        kind = msg.get("t")
        if kind == "hello":
            log(f"gateway {msg.get('fw')} ({msg.get('board')}) on {self.gateway.port.port},"
                f" radio {'ok' if msg.get('radio') else 'FAILED'},"
                f" {msg.get('freq_mhz')} MHz SF{msg.get('sf')};"
                f" sending {len(self.prices)} price(s) and {len(self.configs)} label(s)")
            for product_id in self.prices:
                self.send_price(product_id)
            for label_id in self.configs:
                self.send_assign(label_id)
        elif kind == "checkin":
            self.on_checkin(msg)

    def on_checkin(self, m: dict) -> None:
        device, label, product = m["device"], m["label_id"], m["product_id"]
        mv = m.get("battery_mv") or 0
        # Above a full cell means the reading is the USB charger, not the battery.
        on_usb = mv > 4250
        pct = battery_pct(mv) if mv and not on_usb else None
        battery = ("" if not mv else f", on USB ({mv / 1000:.2f} V)" if on_usb
                   else f", battery {pct}% ({mv / 1000:.2f} V)")
        link = f"{m['rssi']} dBm, SNR {m['snr']:.1f}{battery}"
        firmware = firmware_name(int(m.get("firmware", 0)))
        # SF7 at 125 kHz decodes down to about -124 dBm and SNR -7.5 dB. Near
        # that edge the gateway still hears the label but its reply is lost.
        # A few metres apart with working antennas reads -30 to -70 dBm.
        weak = ("  ** very weak signal: check both LoRa antennas are on the right connector **"
                if m["rssi"] <= -110 or m["snr"] < -5 else "")

        if not label:
            self.publish("retail/label/discovered", {
                "device_id": device, "button": bool(m.get("button")),
                "rssi_dbm": max(-120, min(0, int(m["rssi"]))), "battery_pct": pct,
                "firmware": firmware, "ts": now_iso(),
            })
            state = "not set up" + (", BUTTON PRESSED" if m.get("button") else "")
            log(f"device {device}  {link}  {state}{weak}")
            return

        if self.configs_ready and label not in self.configs:
            # The console is the truth about which tag is which label. This tag
            # claims a label the console has no config for: it was removed, or
            # the database was reset. Send it back to "Not set up".
            self.gateway.send({"t": "forget", "device": device})
            log(f"device {device} says it is {label}, which the console doesn't have:"
                " sending it back to \"Not set up\"")
            return

        if not product:
            state = "set up, no product"
        elif not m.get("current"):
            state = f"no price for {product} on the bus yet"
            self.confirmed[label] = False
        elif m["shows"] == m["current"]:
            state = f"showing {money(m['cents'])}"
            if not self.confirmed.get(label):
                state += ", confirmed on the glass"
            self.confirmed[label] = True
        else:
            state = f"sent {money(m['cents'])}, the glass shows an older price"
            self.confirmed[label] = False
        if m.get("button"):
            state += ", button pressed"
        if m.get("blink"):
            state += f", blink {m['blink']} s"
        if not m.get("replied"):
            state += " (reply failed to transmit)"
        log(f"{label} ({product or '-'})  {link}  {state}{weak}")
        self.publish(f"retail/shelf/{label}", self.shelf_report(m, pct, firmware))

    def shelf_report(self, m: dict, pct: int | None, firmware: str) -> dict:
        """A ShelfReport (contracts/shelf.schema.json, draft)."""
        label, product, shows = m["label_id"], m["product_id"] or None, m["shows"]
        shown = self.history.get(product or "", {}).get(shows) if shows else None
        if shows and self.rendered.get(label, (None,))[0] != shows:
            self.rendered[label] = (shows, now_iso())
        return {
            "label_id": label,
            "product_id": product,
            # Two decimals by construction: whole cents over 100.
            "price": float(Decimal(shown[0]) / 100) if shown else None,
            "decision_id": shown[1] if shown else None,
            "in_sync": bool(shows) and shows == m.get("current"),
            "rendered_at": self.rendered[label][1] if shows else None,
            "rssi_dbm": max(-120, min(0, int(m["rssi"]))),
            "battery_pct": pct,
            "firmware": firmware,
            "ts": now_iso(),
        }


# ─── the two loops ──────────────────────────────────────────────────────────


async def serial_loop(bridge: Bridge) -> None:
    announced_missing = False
    while True:
        if bridge.gateway.port is None:
            port = await asyncio.to_thread(find_gateway, bridge.port_arg, bridge.not_gateways)
            if port is None:
                if not announced_missing:
                    where = bridge.port_arg or "any USB serial port"
                    log(f"no gateway found on {where}; is it plugged in? (retrying)")
                    announced_missing = True
                await asyncio.sleep(3)
                continue
            announced_missing = False
            bridge.gateway.port = port
            # The probe consumed the hello; ask again so on_gateway_line
            # announces the gateway and replays the prices.
            bridge.gateway.send({"t": "who"})

        last_ping = 0.0
        try:
            while bridge.gateway.port is not None:
                if time.monotonic() - last_ping > PING_EVERY:
                    bridge.gateway.send({"t": "ping"})
                    last_ping = time.monotonic()
                port = bridge.gateway.port
                if port is None:
                    break
                raw = await asyncio.to_thread(port.readline)
                if raw:
                    bridge.on_gateway_line(raw.decode(errors="replace").strip())
        except (serial.SerialException, OSError) as exc:
            log(f"gateway disconnected: {exc}")
            bridge.gateway.close()


async def mqtt_loop(bridge: Bridge, host: str, port: int) -> None:
    while True:
        try:
            async with aiomqtt.Client(host, port, identifier="lora-bridge") as client:
                for topic in SUBSCRIPTIONS:
                    await client.subscribe(topic, qos=1)
                bridge.mqtt = client
                asyncio.get_running_loop().call_later(
                    CONFIG_SETTLE_SECS, setattr, bridge, "configs_ready", True)
                log(f"MQTT connected to {host}:{port}")
                async for message in client.messages:
                    topic = message.topic.value
                    payload = message.payload if isinstance(message.payload, bytes) else b""
                    if topic.startswith("retail/price/"):
                        bridge.on_price(topic, payload)
                    elif topic == PAIRING_TOPIC:
                        # Retained: only an open window still in the future rings.
                        bridge.on_pairing(payload)
                    elif topic.endswith("/config"):
                        bridge.on_config(topic, payload)
                    elif topic.endswith("/blink"):
                        bridge.on_blink(topic, payload)
        except aiomqtt.MqttError as exc:
            bridge.mqtt = None
            bridge.configs_ready = False
            log(f"MQTT {host}:{port} unavailable ({exc}); is the stack up? retrying in 3 s")
            await asyncio.sleep(3)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", help="gateway serial port (default: find it)")
    ap.add_argument("--mqtt-host", default=os.environ.get("MQTT_HOST", "localhost"))
    ap.add_argument("--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    args = ap.parse_args()

    bridge = Bridge(args.port)
    await asyncio.gather(
        serial_loop(bridge),
        mqtt_loop(bridge, args.mqtt_host, args.mqtt_port),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
