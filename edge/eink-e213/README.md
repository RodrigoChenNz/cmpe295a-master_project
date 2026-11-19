# edge/eink-e213: e-ink shelf label (LoRa)

Layer 1 for the e-ink label, on either of two Heltec boards with the same
parts (ESP32-S3, 2.13" black and white e-ink at 250×122, SX1262 LoRa radio):

| Board | Build | Its button | USB port |
|---|---|---|---|
| Vision Master E213 | `e213` | **21**, beside the USB port | `/dev/cu.usbmodem…`, gone while it sleeps |
| Wireless Paper V1.1, V1.1.1, V1.2 | `wireless-paper` | **PRG** | `/dev/cu.usbserial-…`, stays up while it sleeps |

Wherever this page says "button 21", read "PRG" on a Wireless Paper. **It
doesn't use Wi-Fi.**
It talks LoRa at 915 MHz to the gateway in [`edge/lora-gateway`](../lora-gateway/),
which gets prices and assignments from the broker through
[`tools/lora_bridge.py`](../../tools/lora_bridge.py).

```
E213 label ──LoRa 915 MHz──▶ WiFi LoRa 32 gateway ──USB──▶ lora_bridge.py ──▶ Mosquitto ◀── dashboard-api
```

The packets are in [`edge/common/shelf_link.h`](../common/shelf_link.h), which
both firmwares include.

**Every label runs the same build.** Nothing about a particular label is
compiled in: its label ID, product and product name come from the console, and
it keeps them in flash.

## Setting up new labels

1. Flash every tag with this firmware (below). Each shows **Not set up** and
   its device ID, the radio's factory address.
2. In the console: **Shelf labels → Add labels → Start searching**. The gateway
   rings every tag that isn't set up, and they appear in the list within a few
   seconds.
3. **Press the button marked 21** on the tag in your hand. It moves to the top
   of the list, marked "In your hand".
4. Choose what it becomes: **a new label** (the console gives it the next
   `SHLF-` number) or **an existing label with no tag**, such as one whose tag
   broke, which keeps that label's product, position and history. Pick a
   product and, optionally, a shelf position.
5. The tag redraws within a few seconds with its product's name and price.

Moving a label to another product is an edit in the console (Shelf labels →
the label → Reassign); the tag redraws at once. **Remove tag** on the label's
page sends the tag back to "Not set up".

## How it works

The label is asleep almost all the time. It **checks in** with the gateway:
"I'm device D, showing price version V." The gateway answers with its
assignment and the current price of its product. The label saves any change to
its assignment, redraws only if something on the glass has to change, then
checks in once more so the gateway learns at once what the glass shows. Every
check-in becomes a report to the console (`retail/shelf/{label_id}`): online
status, signal, battery, and whether the glass shows the current price.

- **Changes show in about 3 s, even while the label sleeps.** When its price or
  assignment changes, or a blink arrives, the gateway rings the label (a
  "doorbell") and it checks in at once. If it misses the doorbell, the gateway
  rings twice more, and the regular check-in every `WAKE_SECS` (30 minutes,
  give or take `WAKE_JITTER_SECS`, 1 minute) is the fallback. Press button 21 to check in now.
- **Wake on radio (`DEEP_SLEEP 1`, the default).** Between check-ins the ESP32
  is in deep sleep, the display is unpowered and the radio listens for about
  15 ms every quarter second. A doorbell starts with a 0.26 s preamble, so one
  of those listens always catches it, and the radio wakes the ESP32. Doorbells
  have their own sync word, so other labels' traffic never wakes a sleeping
  label. A doorbell for another label wakes it for a few hundred milliseconds,
  and it goes back to sleep without checking in.
- **Add labels** sends a doorbell to every tag not set up. Each answers after a
  random delay of up to 3 s, so a box of them doesn't transmit at once.
- **Awake (`DEEP_SLEEP 0`).** Same behaviour and speed, but the ESP32 never
  sleeps, so the USB serial console stays up. For setting up and debugging.
- **E-ink keeps its image with no power.** The label keeps a fingerprint of
  the last screen in flash, so a wake-up, a reboot or a power cut costs no
  refresh, and the shelf is never blank.
- **No gateway in range:** the label keeps what's on the glass and retries after
  30 s, then 1, 2, 4, 8 and 16 minutes, then every `WAKE_SECS`.
- **The console calls a label offline** after 75 minutes without a check-in
  (Settings → Shelf labels), which forgives one missed check-in. The gateway
  keeps ringing a label for the same 75 minutes after it last heard from it.
- **Blink** (the console's "find this label" button) inverts the screen for that
  many seconds and flashes the board's LED (GPIO45 on the E213, absent on its
  first revision; GPIO18 on the Wireless Paper).
- **Battery:** each check-in carries the battery voltage; the console shows it
  as a percentage, and logs "Battery low" under 20%. On USB it reads as unknown.

## What the label shows

```
┌──────────────────────────────────────┐
│ Sourdough Loaf 800g                  │  product name, from the console
│ $3.49                                │  price, large
│ Competitor up                        │  reason_tag verbatim
│──────────────────────────────────────│
│ SKU-002                    SHLF-0108 │  for staff: product and label id
└──────────────────────────────────────┘
```

Before that it shows **Not set up** (with its device ID and how to set it up),
then **SHLF-0318 · Ready, not assigned** until it has a product, and the product
name with **Waiting for its price** until a price arrives.

## Which panel do I have?

You don't need to know. Heltec has fitted this board with two panels that need
different drivers, and the firmware tells them apart at boot: it holds the
panel in reset and reads which way its BUSY pin goes, the same test Meshtastic
uses. The serial console prints the result:

| Board revision (back of the board) | Panel | Console says |
|---|---|---|
| No version printed | LCMEN2R13EFC1 | `panel: LCMEN2R13EFC1 (original E213)` |
| V1.1, V1.1.1 | E0213A367 | `panel: E0213A367 (E213 V1.1 and later)` |

If the screen stays blank or shows noise, force the other one with `PANEL` in
`config.h` (1 = original, 2 = V1.1 and later).

## Flashing

**Screw the 915 MHz antenna onto the LoRa connector (on the HT-RA62 module, not
"WIFI ANT") before powering the board.** Transmitting without an antenna can
damage the radio.

1. Install PlatformIO: `brew install platformio`, or the PlatformIO extension
   for VS Code.
2. Copy `include/config.example.h` to `include/config.h`. The defaults suit
   every label; `config.h` exists only so you can change sleep mode or radio
   power locally.
3. Plug in the board over USB-C (use a **data** cable) and flash it with the
   build for that board, naming its port so PlatformIO can't pick the gateway
   or a Bluetooth device:
   ```bash
   cd edge/eink-e213
   pio device list
   pio run -e e213 -t upload --upload-port /dev/cu.usbmodem101              # Vision Master E213
   pio run -e wireless-paper -t upload --upload-port /dev/cu.usbserial-XXXX  # Wireless Paper
   ```
   **A Wireless Paper and the V3 gateway both show up as `/dev/cu.usbserial-…`.**
   Plug one in at a time and note which name is which before flashing.

   On the E213, if the upload can't find the port, hold **BOOT**, tap **RST**,
   release **BOOT**, and upload again: a sleeping E213 isn't listening on USB.
   On the Wireless Paper the same trick is hold **PRG**, tap **RST**. Press
   **RST** after the upload finishes.

### On battery

`DEEP_SLEEP 1` is the default. The label is awake only to check in (about a
second every 30 minutes) and to redraw after a change. The rest of the time the
radio's short listens dominate, at an estimated 0.3 mA. Battery life depends on
the board's own sleep current, which hasn't been measured. With a 1000 mAh cell,
expect months rather than days, but measure it: a USB power meter, or a
multimeter in series with the battery, over a few hours.

On the E213 the USB serial console drops at every sleep; set `DEEP_SLEEP 0`
while setting up or debugging. The Wireless Paper's console stays up.

On the Wireless Paper, one switch (GPIO45) powers both the display and the
radio's front end, so it stays on through deep sleep to keep the radio
listening. Its sleep current will be higher than the E213's, where the display
is switched off; measure both.

## Known gaps

- **`retail/shelf/{label_id}` is a draft contract topic.** It needs all four
  signatures before this merges (see `contracts/shelf.schema.json`).
- **No authentication on the radio.** Anyone with a 915 MHz LoRa radio and this
  packet format could send a label a price, and the button claim only proves
  someone is near the tag. A per-tag code shown as a QR code, and signed
  replies, are the next step.
- **The layout hasn't been checked on a panel yet.** It's drawn from font
  metrics. Positions are in `drawPrice()` and `drawReady()`. Bump
  `LAYOUT_VERSION` after changing them so labels already on the shelf redraw.
- **Deep-sleep current hasn't been measured**, so battery life is an estimate.
- **The Wireless Paper build hasn't run on a board yet.** Its pins come from
  Heltec's schematic as used by the display library and Meshtastic.
- **Setting up from the console hasn't run on a board yet.** The console, API
  and bridge are tested end to end against a simulated gateway, and both
  firmwares build; the radio side needs the real boards.
