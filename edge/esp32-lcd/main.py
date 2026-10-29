"""Shelf-edge node: ESP32 + 16x2 I2C LCD.

Subscribes to retail/price/{product_id} (contracts/price.schema.json), shows
the live price on the LCD, and keeps the last known price in flash (NVS) so the
shelf is never blank after a power cut or a network outage.

LCD line 1: SKU + price, right aligned     e.g.  SKU-001   $12.99
LCD line 2: reason_tag, verbatim (<= 16)   e.g.  Competitor up
Line 2 reads [cached] until the first live message after a reboot.
"""
import network, time, json, machine, ubinascii, esp32
from machine import I2C, Pin
from umqtt.simple import MQTTClient
from lcd import I2cLcd
import config

# ---- LCD ----
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=100000)
lcd1 = I2cLcd(i2c, i2c.scan()[0])
LCDS = {"SKU-001": lcd1}   # product_id -> display (add the 2nd LCD here later)

# ---- last-known price, kept in flash (NVS) ----
nvs = esp32.NVS("shelf")
last = {}   # sku -> (price, reason) most recently shown

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

def line1(sku, price):
    p = "$%.2f" % price
    gap = max(1, 16 - len(sku) - len(p))
    return (sku + " " * gap + p)[:16]

def show(sku, price, reason):
    lcd = LCDS.get(sku)
    if lcd is None:
        print("no display for", sku)
        return False
    lcd.write_line(0, line1(sku, price))
    lcd.write_line(1, reason[:16])
    return True

# ---- on boot: show the last known price straight away ----
for sku in LCDS:
    saved = load_last(sku)
    if saved:
        show(sku, saved[0], "[cached]")
        last[sku] = saved
        print("restored", sku, saved)
    else:
        LCDS[sku].write_line(0, "Connecting...")
        LCDS[sku].write_line(1, "")

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
        sku = topic.decode().split("/")[-1]
        msg = json.loads(payload)
        if msg["product_id"] != sku:
            print("topic/payload mismatch, ignored")
            return
        price = float(msg["price"])
        reason = str(msg["reason_tag"])
        if show(sku, price, reason):
            if last.get(sku) != (price, reason):
                save_last(sku, price, reason)
                last[sku] = (price, reason)
            print("shown:", sku, price, reason)
    except Exception as e:
        print("bad message ignored:", e)

def mqtt_connect():
    cid = "hb-" + ubinascii.hexlify(machine.unique_id()).decode()
    c = MQTTClient(cid, config.MQTT_BROKER, port=config.MQTT_PORT, keepalive=30)
    c.set_callback(on_message)
    c.connect()
    c.subscribe(config.TOPIC, qos=1)
    return c

# ---- main ----
client = None
while True:
    try:
        if client is None:
            wifi_connect()
            client = mqtt_connect()
            print("MQTT connected, subscribed to", config.TOPIC)
            for sku, lcd in LCDS.items():
                if sku not in last:
                    lcd.write_line(0, "Waiting for")
                    lcd.write_line(1, "price...")
        client.check_msg()
        time.sleep_ms(100)
    except OSError as e:
        print("connection problem:", e)
        client = None
        time.sleep(3)
