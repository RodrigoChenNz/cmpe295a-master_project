"""Shelf-edge node: ESP32 + 16x2 I2C LCD.

Subscribes to retail/price/{product_id} (contracts/price.schema.json), shows
the live price on the LCD, and keeps the last known price in flash (NVS) so the
shelf is never blank after a power cut or a network outage.

LCD line 1: SKU + price, right aligned     e.g.  SKU-001   $12.99
LCD line 2: reason_tag, verbatim (<= 16)   e.g.  Competitor up
Line 2 reads [cached] until the first live message after a reboot.

One node can drive several LCDs. config.DISPLAYS lists them, one entry per LCD:
    DISPLAYS = [
        {"sku": "SKU-001", "sda": 21, "scl": 22},            # LCD 1 on GPIO 21/22
        {"sku": "SKU-003", "sda": 16, "scl": 17},            # LCD 2 on its own bus
    ]
or, with both on the same two wires (second backpack's A0 pad closed):
    DISPLAYS = [
        {"sku": "SKU-001", "addr": 0x27},
        {"sku": "SKU-003", "addr": 0x26},
    ]
Keys: sku (required), addr (optional, default: first address found on that
bus that is not already used), sda (default 21), scl (default 22),
name (optional, see below), label (optional, see the display map below).
If config has no DISPLAYS the node behaves as before: SKU-001 on the first
display it finds.

Shelf-label layout (optional). Add a "name" to a display and that LCD shows
the product name instead of the SKU:
    {"sku": "SKU-001", "sda": 21, "scl": 22, "name": "MILK ORGANIC 1L"}
    LCD line 1: the name, cut at 16 characters    e.g.  MILK ORGANIC 1L
    LCD line 2: the price only                    e.g.  $3.49
                (price + [cached] until the first live message after a reboot)
Set SHOW_REASON = True in config.py to add the reason after the price
(e.g. "$3.49 Manager s"), as the console preview does.
The price message does not carry the product name (contracts/price.schema.json),
so the name comes from config.py, or from the display map below.

Dynamic display map (optional). Set DYNAMIC_MAP = True in config.py and the
board also subscribes to retail/config/display_map. A retained message there
    {"displays": [{"sku": "SKU-001", "name": "MILK ORGANIC 1L"},
                  {"sku": "SKU-003", "name": "EGGS FREERANGE"}]}
decides which product each LCD shows and the name on its line 1, with no
re-flashing. An entry that has a "label" (the console's label id, e.g.
"SHLF-0142") goes to the display whose config.DISPLAYS entry has the same
"label"; the console publishes the map in this form whenever a label is
paired or reassigned. An entry without a "label" goes to the display at the
same position (entry 0 = first display in config.DISPLAYS). Entries for labels
this board does not have are ignored. An entry replaces that display's sku and
name (no name = SKU layout); displays with no entry keep what config.py says.
The pins stay in config.py, because they are wires. The latest valid map is
saved in flash and used after a power cut. A product's sku can be at most 15
characters (flash key limit). Without DYNAMIC_MAP the board ignores the map
and subscribes to prices only.
"""
import network, time, json, machine, ubinascii, esp32
from machine import I2C, Pin
from umqtt.simple import MQTTClient
from lcd import I2cLcd
import config

MAP_TOPIC = "retail/config/display_map"
DYNAMIC_MAP = getattr(config, "DYNAMIC_MAP", False)
SHOW_REASON = getattr(config, "SHOW_REASON", False)
SEEN_MAX = 100   # latest live price kept in RAM for this many products (DYNAMIC_MAP only)

# ---- LCDs ----
DISPLAYS = getattr(config, "DISPLAYS", None) or [{"sku": "SKU-001"}]

buses = {}    # (sda, scl) -> I2C object; the ESP32 has two hardware buses, 0 and 1
used = set()  # (sda, scl, addr) already given to a display
SLOTS = {}    # index in DISPLAYS -> {"lcd": display, "sku": product_id, "label": console label id or None, "name": line 1 text or None}
first_error = None

for i, d in enumerate(DISPLAYS):
    sku = d["sku"]
    try:
        pins = (d.get("sda", 21), d.get("scl", 22))
        if pins not in buses:
            buses[pins] = I2C(len(buses), scl=Pin(pins[1]), sda=Pin(pins[0]), freq=100000)
        bus = buses[pins]
        addr = d.get("addr")
        if addr is None:
            addr = [a for a in bus.scan() if pins + (a,) not in used][0]
        used.add(pins + (addr,))
        SLOTS[i] = {"lcd": I2cLcd(bus, addr), "sku": sku, "label": d.get("label"),
                    "name": str(d["name"])[:16] if d.get("name") else None}
        print("display for", sku, "at", hex(addr), "on SDA", pins[0], "SCL", pins[1])
    except Exception as e:
        print("display for", sku, "not started:", e)
        if first_error is None:
            first_error = e

if not SLOTS:
    raise first_error   # nothing to show prices on: stop, as before

# ---- last-known price, kept in flash (NVS) ----
nvs = esp32.NVS("shelf")
last = {}   # sku -> (price, reason) most recently shown
seen = {}   # sku -> (price, reason) latest live price of any product (DYNAMIC_MAP only, RAM)

def save_last(sku, price, reason):
    try:
        nvs.set_blob(sku, json.dumps({"p": price, "r": reason}).encode())
        nvs.commit()
    except Exception as e:
        print("save failed:", e)

def load_last(sku):
    try:
        buf = bytearray(96)
        n = nvs.get_blob(sku, buf)
        d = json.loads(bytes(buf[:n]))
        return float(d["p"]), str(d["r"])
    except Exception:
        return None

def save_map(entries):
    try:
        nvs.set_blob("map", json.dumps(entries).encode())
        nvs.commit()
    except Exception as e:
        print("save failed:", e)

def load_map():
    try:
        buf = bytearray(512)
        n = nvs.get_blob("map", buf)
        return json.loads(bytes(buf[:n]))
    except Exception:
        return None

# ---- drawing ----
def line1(sku, price):
    p = "$%.2f" % price
    gap = max(1, 16 - len(sku) - len(p))
    return (sku + " " * gap + p)[:16]

def price_line(price, reason, cached):
    p = "$%.2f" % price
    if cached:
        return (p + " [cached]")[:16]
    room = 16 - len(p) - 1
    if SHOW_REASON and reason and room >= 4:
        return (p + " " + reason[:room])[:16]
    return p

def draw(s, price, reason, cached=False):
    if s["name"]:
        s["lcd"].write_line(0, s["name"])
        s["lcd"].write_line(1, price_line(price, reason, cached))
    else:
        s["lcd"].write_line(0, line1(s["sku"], price))
        s["lcd"].write_line(1, reason[:16])

def show(sku, price, reason, cached=False):
    n = 0
    for s in SLOTS.values():
        if s["sku"] == sku:
            draw(s, price, reason, cached)
            n += 1
    if n == 0:
        print("no display for", sku)
    return n > 0

def remember(sku, price, reason):
    if last.get(sku) != (price, reason):
        save_last(sku, price, reason)
        last[sku] = (price, reason)

# ---- dynamic display map ----
def apply_map(entries):
    """Returns the displays that changed.

    An entry with a "label" goes to the display with that label in config.DISPLAYS;
    an entry without one goes to the display at the same position.
    """
    changed = []
    for pos, e in enumerate(entries):
        if not isinstance(e, dict):
            continue
        sku, name = e.get("sku"), e.get("name")
        if not isinstance(sku, str) or not sku or len(sku) > 15:
            continue
        name = name[:16] if isinstance(name, str) and name else None
        label = e.get("label")
        if label is None:
            targets = [pos] if pos in SLOTS else []
        else:
            targets = [i for i, s in SLOTS.items() if s["label"] == label]
        for i in targets:
            s = SLOTS[i]
            if (s["sku"], s["name"]) != (sku, name):
                s["sku"], s["name"] = sku, name
                if i not in changed:
                    changed.append(i)
    return changed

def current_map():
    out = []
    for i in range(len(DISPLAYS)):
        s = SLOTS.get(i)
        out.append({"sku": s["sku"], "name": s["name"]} if s else {})
    return out

def refresh(s):
    """A display was pointed at another product: draw what we know about it."""
    sku = s["sku"]
    if sku in seen:
        price, reason = seen[sku]
        draw(s, price, reason)
        remember(sku, price, reason)
        return
    saved = last.get(sku) or load_last(sku)
    if saved:
        draw(s, saved[0], "[cached]", True)
        last[sku] = saved
    else:
        s["lcd"].write_line(0, "Waiting for")
        s["lcd"].write_line(1, "price...")

def on_map(payload):
    changed = apply_map(json.loads(payload.strip())["displays"])
    for i in changed:
        print("display", i, "now", SLOTS[i]["sku"], SLOTS[i]["name"])
        refresh(SLOTS[i])
    if changed:
        save_map(current_map())

if DYNAMIC_MAP:
    m = load_map()
    if m:
        apply_map(m)
        print("display map restored")

# ---- on boot: show the last known price straight away ----
for s in SLOTS.values():
    saved = load_last(s["sku"])
    if saved:
        draw(s, saved[0], "[cached]", True)
        last[s["sku"]] = saved
        print("restored", s["sku"], saved)
    else:
        s["lcd"].write_line(0, "Connecting...")
        s["lcd"].write_line(1, "")

# ---- Wi-Fi ----
def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        t = 20
        while not wlan.isconnected() and t > 0:
            time.sleep(1)
            t -= 1
    return wlan.isconnected()

# ---- MQTT ----
def on_message(topic, payload):
    try:
        t = topic.decode()
        if t == MAP_TOPIC:
            if DYNAMIC_MAP:
                on_map(payload)
            return
        sku = t.split("/")[-1]
        msg = json.loads(payload)
        if msg["product_id"] != sku:
            print("topic/payload mismatch, ignored")
            return
        price = float(msg["price"])
        reason = str(msg["reason_tag"])
        if DYNAMIC_MAP and (sku in seen or len(seen) < SEEN_MAX):
            seen[sku] = (price, reason)
        if show(sku, price, reason):
            remember(sku, price, reason)
            print("shown:", sku, price, reason)
    except Exception as e:
        print("bad message ignored:", e)

def mqtt_connect():
    cid = "hb-" + ubinascii.hexlify(machine.unique_id()).decode()
    c = MQTTClient(cid, config.MQTT_BROKER, port=config.MQTT_PORT, keepalive=30)
    c.set_callback(on_message)
    c.connect()
    c.subscribe(config.TOPIC, qos=1)
    if DYNAMIC_MAP:
        c.subscribe(MAP_TOPIC, qos=1)
    return c

# ---- main ----
client = None
while True:
    try:
        if client is None:
            wifi_connect()
            client = mqtt_connect()
            print("MQTT connected, subscribed to", config.TOPIC)
            for s in SLOTS.values():
                if s["sku"] not in last:
                    s["lcd"].write_line(0, "Waiting for")
                    s["lcd"].write_line(1, "price...")
        client.check_msg()
        time.sleep_ms(100)
    except OSError as e:
        print("connection problem:", e)
        client = None
        time.sleep(3)
