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

### Shelf-label layout (optional)

Add a `name` to a display in `config.py` and that LCD shows the product name
and the price, like a shelf label, instead of the SKU and the reason:

```
MILK ORGANIC 1L       line 1: the name from config.py, cut at 16 characters
$3.49                 line 2: the price only ($3.49 [cached] after a reboot)
```

The price message does not carry the product name (`contracts/price.schema.json`),
so the name is set per display in `config.py`. `SHOW_REASON = True` adds the
reason after the price (`$3.49 Manager se`), the same rule the console preview
uses. A display without a `name` keeps the layout above. The layout has not yet
been checked on the real board.

### Changing products without re-flashing (optional)

With `DYNAMIC_MAP = True` in `config.py` the board also subscribes to
`retail/config/display_map`. A retained message there sets the product and the
line-1 name of each LCD:

```json
{"displays": [{"sku": "SKU-001", "name": "MILK ORGANIC 1L"},
              {"sku": "SKU-003", "name": "EGGS FREERANGE"}]}
```

- An entry with a `"label"` (the console's label id, for example `"SHLF-0142"`) goes
  to the display whose `DISPLAYS` entry has the same `"label"`. An entry without a
  `"label"` goes to the display at the same position (entry 0 is the first display).
  Entries for labels this board does not have are ignored. An entry replaces that
  display's `sku` and `name`; no `name` means the SKU layout. Displays with no entry
  keep the values from `config.py`. The pins stay in `config.py` because they are
  wires.
- The board keeps the latest live price of up to 100 products in RAM, so a display
  pointed at another product shows that product's price at once. If the board has
  never seen a price for it, the LCD says `Waiting for price...`.
- The latest valid map is saved in flash and used after a power cut. A malformed
  map is ignored and the screens do not change. A `sku` can be at most 15
  characters (flash key limit).
- From a computer with `mosquitto_pub`:
  `mosquitto_pub -h BROKER -r -q 1 -t retail/config/display_map -m '{"displays":[{"sku":"SKU-001","name":"MILK ORGANIC 1L"}]}'`
- The console does not publish this topic yet. It is not one of the contract topics,
  so the team has to agree the message, and where the console sends it, before it
  does. Until then it is sent by hand.

## Hardware

One LCD:

| LCD backpack (PCF8574) | ESP32 DevKit |
|---|---|
| GND | GND |
| VCC | VIN (5 V) |
| SDA | GPIO21 |
| SCL | GPIO22 |

Two LCDs, no soldering (the default in `config.example.py`). The second backpack
gets its own pair of wires, so both keep the address 0x27 (the ESP32 has two I2C
controllers):

| Backpack pin | LCD 1 | LCD 2 |
|---|---|---|
| GND | GND | GND |
| VCC | VIN (5 V) | VIN (5 V) |
| SDA | GPIO21 | GPIO16 (marked RX2) |
| SCL | GPIO22 | GPIO17 (marked TX2) |

Instead, both LCDs can share GPIO21 and GPIO22 if the second backpack has a
different I2C address (close its A0 solder pad: 0x27 becomes 0x26). Then give each
entry in `DISPLAYS` an `addr`.

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
2. Copy `config.example.py` to `config.py`, fill it in (including `DISPLAYS`, one entry
   per LCD), and save it on the board.
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

With two LCDs, press EN and the serial console prints one `display for SKU-...` line
per LCD. In the console, open **Labels**, then a label, then **Resend current
price**: only the LCD with that product changes. A price for a product with no LCD
prints `no display for SKU-...` and changes nothing.

Checked on hardware with one LCD (ESP32 DevKit, MicroPython 1.29, public broker):

1. Live price shows on the LCD and updates when a new price is published.
2. After a reboot the saved price shows as `[cached]`, then the live reason.
3. With a wrong Wi-Fi name the saved price still shows and stays.

Two LCDs, the shelf-label layout and the display map have not been checked on
hardware and written down here yet.

## Known gaps

- **The wiring is read once at boot.** The pins and addresses are in `DISPLAYS` in
  `config.py`. A display that is not found at boot stays unused until the next
  reset, and the other display keeps working. If none is found the board stops.
  Which product each display shows can change at run time (display map above), but
  the wiring cannot.
- **The display map is not authenticated.** Anyone who can reach the broker can
  publish it, as with prices (the broker allows anonymous clients). It is off unless
  `DYNAMIC_MAP = True`.
- **No acknowledgement yet.** Publishing to `retail/shelf/{label_id}` after a
  price is shown is an open decision (README, Open decisions). It needs all four
  approvals because it changes `contracts/`, so it is not implemented here.
- **No TLS and no broker login.** Fine for a local broker, not for a public one.
- The Heltec E213 e-ink label is a separate part with its own toolchain and does
  not belong in this folder.
