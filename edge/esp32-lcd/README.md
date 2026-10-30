# edge/esp32-lcd: ESP32 + 16x2 LCD shelf label

Layer 1 and 2 for the LCD label, on an ESP32 (MicroPython). The ESP32 has on-die
Wi-Fi, so it talks to the broker directly and needs no separate gateway.

It subscribes to `retail/price/+` (see [`contracts/price.schema.json`](../../contracts/price.schema.json)),
shows the live price, and keeps the last known price in flash (NVS) so the shelf
is never blank after a power cut or a network outage.

## What the LCD shows

```
SKU-001   $12.99      line 1: product_id, price right-aligned, 2 decimals
Competitor up         line 2: reason_tag verbatim (the schema caps it at 16)
```

- After a reboot the saved price appears at once, with `[cached]` on line 2 until
  the first live message arrives. The price is retained on the broker, so that
  is normally under a second.
- If the broker is unreachable the display holds the last price. It retries
  every few seconds.
- Fields other than `product_id`, `price` and `reason_tag` are ignored.
- A malformed message, or one whose `product_id` does not match its topic, is
  logged to the serial console and ignored. The display does not change.
- Flash is written only when the price or reason actually changes.

## Hardware

| LCD backpack (PCF8574) | ESP32 DevKit |
|---|---|
| GND | GND |
| VCC | VIN (5 V) |
| SDA | GPIO21 |
| SCL | GPIO22 |

A second LCD can share the same SDA and SCL pins if its backpack has a different
I2C address (bridge the A0/A1/A2 solder pads). Then add it to `LCDS` in
`main.py`.

## Files

| File | Purpose |
|---|---|
| `main.py` | The firmware. MicroPython runs it on every boot. |
| `lcd.py` | Minimal HD44780 driver for the PCF8574 I2C backpack |
| `config.example.py` | Template for `config.py`, which is **gitignored** (it holds the Wi-Fi password) |

## Setup

1. Flash MicroPython (ESP32 generic build) with Thonny: Tools, Options, Interpreter,
   "Install or update MicroPython". If Windows shows no COM port, install the
   Silicon Labs CP210x driver and use a **data** USB cable.
2. Copy `config.example.py` to `config.py`, fill it in, and save it on the board.
3. Save `lcd.py` and `main.py` on the board too. The board then needs all three files.
4. Press EN on the ESP32. The serial console should print
   `MQTT connected, subscribed to retail/price/+`.

The ESP32 only joins **2.4 GHz** Wi-Fi.

## Testing without the rest of the stack

Publish a retained message to a broker the ESP32 can reach:

```json
{"product_id":"SKU-001","price":12.99,"currency":"USD","reason_tag":"Competitor up",
 "approval":"manager","approved_by":"hb","ts":"2026-10-18T18:00:00Z"}
```

on `retail/price/SKU-001`. With the full stack running, `make demo` followed by an
approval in the console does the same thing.

Checked on hardware (ESP32 DevKit, MicroPython 1.29, public broker):

1. Live price shows on the LCD and updates when a new price is published.
2. After a reboot the saved price shows as `[cached]`, then the live reason.
3. With a wrong Wi-Fi name the saved price still shows and stays.

## Known gaps

- **One display.** The product-to-LCD mapping is hardcoded in `LCDS`. Support for
  two LCDs on the bus is the next step.
- **No acknowledgement yet.** Publishing to `retail/shelf/{label_id}` after a
  price is shown is an open decision (README, Open decisions). It needs all four
  approvals because it changes `contracts/`, so it is not implemented here.
- **No TLS and no broker login.** Fine for a local broker, not for a public one.
- The Heltec E213 e-ink label is a separate part with its own toolchain and does
  not belong in this folder.
