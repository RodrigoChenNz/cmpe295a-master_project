# edge/lora-gateway: Heltec WiFi LoRa 32 as the label gateway

Layer 2 for the LoRa labels. One Heltec WiFi LoRa 32 (ESP32-S3, SX1262, 0.96"
OLED) listens for check-ins from the e-ink labels
([`edge/eink-e213`](../eink-e213/)) and answers each with that tag's
assignment (label ID, product, product name) and the current price of its
product.

**The gateway has no network connection.** It's plugged into the computer
running the stack over USB, and [`tools/lora_bridge.py`](../../tools/lora_bridge.py)
links that serial port to the broker:

```
E213 label ──LoRa 915 MHz──▶ this gateway ──USB──▶ lora_bridge.py ──▶ Mosquitto ◀── dashboard-api
                                                    in:  retail/price/+
                                                         retail/label/+/config    which tag is which label
                                                         retail/label/pairing     Add labels
                                                         retail/label/+/blink
                                                    out: retail/shelf/+           what each glass shows (draft contract)
                                                         retail/label/discovered  tags not set up
```

Nothing above the bridge knows LoRa exists. The price path is the same
`retail/price/+` a Wi-Fi label would read.

## Which board do I have?

| Board | Build environment |
|---|---|
| **V3**: USB-C, "V3" on the board, a CP2102 USB chip near the connector | `v3` |
| **V4**: USB-C, "V4" on the board, a GPS connector, no CP2102 | `v4` |
| V2 or older: micro-USB | not supported (different radio chip) |

The V4 has an RF amplifier between the radio and the antenna. The `v4` build
powers it and switches it for transmit and receive. A `v3` build leaves it
off, so on a V4 board the radio won't reach the labels.

## Setup

**Screw the 915 MHz antenna onto the LoRa connector before powering the board.**

1. Flash the gateway:
   ```bash
   cd edge/lora-gateway
   pio run -e v3 -t upload    # or -e v4
   ```
   The OLED shows `ShelfIQ LoRa gateway` and `Bridge: not connected`.
2. Start the stack: `make demo` (or `make up`) at the repo root.
3. Start the bridge, in its own terminal at the repo root:
   ```bash
   make lora
   ```
   It finds the gateway among the USB serial ports by asking each one, so the
   labels can stay plugged in too. If you need to choose the port yourself, run
   `uv run --with aiomqtt --with pyserial tools/lora_bridge.py --port /dev/cu.usbserial-0001`
   (`pio device list` shows the ports). It needs `uv` (`brew install uv`).
4. The OLED switches to `Bridge: connected` and counts the prices and labels
   the bridge sent. Power a new label: it shows "Not set up", and the bridge
   prints `device 80B54EDCA3D4 … not set up`.
5. Set it up from the console: Shelf labels → Add labels (see
   [`edge/eink-e213`](../eink-e213/README.md#setting-up-new-labels)). The bridge
   then prints its check-ins:
   ```
   15:44:33  SHLF-0318 (SKU-012)  -47 dBm, SNR 9.5  sent $3.40, the glass shows an older price
   15:44:36  SHLF-0318 (SKU-012)  -48 dBm, SNR 9.0  showing $3.40, confirmed on the glass
   ```
6. Approve or set a price in the console. The label shows it in about 3 s.

**Only one program can have the gateway's port open.** Close `pio device
monitor` on the gateway before starting the bridge.

## The OLED

```
ShelfIQ LoRa gateway
Bridge: connected
915.0MHz SF7 v3
Prices 12 Labels 2/3       labels set up / tags known
SHLF-0108 -47  4s ok      last two tags heard: id, RSSI, seconds ago,
new A3D4  -61 22s set     ok = showing the current price, new = sent a newer one,
                          -- = no price for its product, set = not set up yet
```

While "Add labels" is ringing every tag, line 2 reads `Finding new labels...`.

## Behaviour

- Prices and assignments are held in RAM. Both are retained on the broker, so
  after a reboot the gateway's `hello` makes the bridge send them all again.
- **Assignments.** Tags are known by device ID. The console's config for a label
  says which device it is; the gateway puts that label ID, product and product
  name in every reply to that device, and rings it when they change. A tag the
  console hasn't told it about (just after a gateway reboot) keeps working on
  what it says it is, and its assignment is left alone. "Remove tag" makes the
  next reply tell the tag to go back to "Not set up".
- **Doorbells.** Every label says in its check-in that it listens for
  doorbells. When its product's price changes, or a blink arrives for it, the
  gateway rings it: a 10-byte "check in now" packet, up to 3 times 2 s apart.
  "Add labels" rings the broadcast address twice, which every tag not set up
  answers.
  The label checks in at once and gets the price in the reply, so it shows in
  about 3 s. A doorbell uses its own sync word and a 256-symbol preamble (about
  0.3 s on air), so that a label in deep sleep, whose radio listens briefly every
  quarter second, still catches it.
- A blink for a label that can't be rung is queued until it next checks in, and
  dropped after 10 minutes.
- Every check-in gets a reply, including ones that change nothing. That's how
  a label tells "nothing new" apart from "no gateway".
- The serial protocol with the bridge is documented at the top of
  `src/main.cpp`. Lines starting with `# ` are for a person at
  `pio device monitor`.

## Radio settings

In [`edge/common/shelf_link.h`](../common/shelf_link.h): 915.0 MHz, 125 kHz,
SF7, coding rate 4/5, private sync word 0x12, 14 dBm. A reply is 92 bytes,
about 160 ms on air. US915 is unlicensed; keeping each transmission under
400 ms stays within its dwell-time limit. Changing any of these, or the
packet version in that file (now 4), means reflashing the gateway **and**
every label: they ignore packets from another version.

## Known gaps

- **`retail/shelf/{label_id}` is a draft contract topic** and needs all four
  signatures before this merges (`contracts/shelf.schema.json`).
- **The bridge runs on the host, not in Docker.** Docker Desktop on macOS can't
  pass a USB serial device into a container, so it isn't part of `make up`.
- **One gateway, one channel.** Two gateways in range would both answer.
- **Setting up from the console is untested on hardware.** The console, API and
  bridge are tested end to end against a simulated gateway; this firmware
  builds but the version 4 radio protocol hasn't run on a board yet.
