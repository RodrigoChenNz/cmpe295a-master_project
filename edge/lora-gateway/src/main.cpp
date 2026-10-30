/*
 * LoRa gateway for the shelf labels: Heltec WiFi LoRa 32 V3 or V4 (ESP32-S3,
 * SX1262, 0.96" OLED).
 *
 * Listens for label check-ins (edge/common/shelf_link.h) and answers each with
 * that device's assignment (label ID, product, product name) and the current
 * price of its product. Labels listen for a doorbell between check-ins, so the
 * gateway rings one as soon as its price or assignment changes or a blink
 * arrives. "Add labels" in the console rings every label not yet set up at
 * once, with a broadcast doorbell.
 *
 * Prices and assignments are held in RAM. Both come over USB serial from
 * tools/lora_bridge.py, which takes them from the broker, where they are
 * retained: after a reboot the gateway's hello makes the bridge send them all
 * again. The gateway itself has no network connection.
 *
 * Serial protocol with the bridge, one JSON object per line, 115200 baud:
 *
 *   bridge -> gateway   {"t":"who"}                       answer with hello
 *                       {"t":"ping"}                      the bridge is alive
 *                       {"t":"price","product_id":"SKU-002","cents":349,"reason":"Competitor up"}
 *                       {"t":"assign","device":"80B54EDCA3D4","label_id":"SHLF-0108",
 *                        "product_id":"SKU-002","name":"Sourdough Loaf 800g"}
 *                       {"t":"forget","device":"80B54EDCA3D4"}
 *                       {"t":"join"}                      ring every label not set up
 *                       {"t":"blink","label_id":"SHLF-0108","seconds":3}
 *
 *   gateway -> bridge   {"t":"hello","role":"gateway",...} on boot and on "who"
 *                       {"t":"checkin","device":...}       every label check-in
 *
 * Lines starting with "# " are for people and the bridge ignores them.
 */
#include <Arduino.h>
#include <ArduinoJson.h>
#include <RadioLib.h>
#include <SPI.h>
#include <U8g2lib.h>
#include <Wire.h>

#include "shelf_link.h"

#define FW_VERSION "gw-0.2"

#ifndef GW_BOARD
#define GW_BOARD "v3"
#endif

// What reaches the antenna. On the V4 the RF amplifier adds about 11 dB on
// top of the radio chip's own output, so the chip is set lower to match.
static const int TX_POWER_DBM = 14;

// Board pins (Heltec V3/V4 schematics; Meshtastic's variants agree).
static const int PIN_VEXT = 36;       // LOW powers the OLED
static const int PIN_OLED_SDA = 17;
static const int PIN_OLED_SCL = 18;
static const int PIN_OLED_RST = 21;
static const int PIN_LED = 35;

// A label switches from transmit to receive straight after its check-in; this
// gives it time to be listening before the reply starts.
static const uint32_t TURNAROUND_MS = 20;
static const uint32_t BRIDGE_TIMEOUT_MS = 12000;  // the bridge pings every 5 s
static const uint32_t BLINK_EXPIRY_MS = 10UL * 60 * 1000;
// A doorbell is rung up to this many times, this far apart, until the label
// checks in. Missing all of them only means waiting for its regular check-in.
static const uint8_t RINGS = 3;
static const uint32_t RING_GAP_MS = 2000;
// "Add labels": the broadcast doorbell, rung this many times. Labels answer
// after a random delay of up to 3 s, so the gap leaves room for their check-ins.
static const uint8_t JOIN_RINGS = 2;
static const uint32_t JOIN_GAP_MS = 6000;
// Trust a label's "I'm listening" for this long after its last check-in:
// comfortably more than its 30-minute check-in interval (WAKE_SECS), so one
// missed check-in doesn't stop the gateway ringing it.
static const uint32_t LISTENING_TRUST_MS = 75UL * 60 * 1000;

SX1262 radio = new Module(LINK_PIN_NSS, LINK_PIN_DIO1, LINK_PIN_RST, LINK_PIN_BUSY);
volatile bool radioIrq = false;

U8G2_SSD1306_128X64_NONAME_F_HW_I2C oled(U8G2_R0, PIN_OLED_RST, PIN_OLED_SCL, PIN_OLED_SDA);

// ---- state ----

struct ProductPrice {
    char id[LINK_ID_LEN + 1];
    uint32_t cents;
    char reason[LINK_REASON_LEN + 1];
    uint32_t version;
};
static const int MAX_PRICES = 128;
ProductPrice prices[MAX_PRICES];
int nPrices = 0;

struct Device {
    uint8_t addr[LINK_DEVICE_LEN];
    char hex[2 * LINK_DEVICE_LEN + 1];
    // The console's assignment, from the bridge. Unassigned: none yet.
    bool assigned;
    bool forget;           // removed in the console: say so once, then forget it
    char labelId[LINK_ID_LEN + 1];
    char product[LINK_ID_LEN + 1];
    char name[LINK_NAME_LEN + 1];
    // What the label last told us.
    uint32_t lastMs;       // 0 = not heard since this gateway booted
    uint32_t shows;        // price version on its glass
    int16_t rssi;
    bool listening;
    // 'k' showing the current price, 'n' sent a newer one, '-' no price known,
    // '?' not set up
    char state;
    uint8_t ringsLeft;
    uint32_t ringAtMs;
    uint8_t blinkSecs;
    uint32_t blinkAtMs;
};
static const int MAX_DEVICES = 64;
Device devices[MAX_DEVICES];
int nDevices = 0;

uint8_t joinRingsLeft = 0;
uint32_t joinAtMs = 0;

uint32_t bridgeSeenMs = 0;
bool bridgeEverSeen = false;
bool radioOk = false;

// ---- V4 RF front end ----
// The V4 puts an amplifier chip between the SX1262 and the antenna, and it
// must be powered and switched between transmit and receive. Two versions
// exist (V4.2: GC1109, V4.3: KCT8103L); which one is fitted is read from the
// shared enable pin, as Meshtastic does.
#ifdef GW_V4
enum { FEM_GC1109, FEM_KCT8103L } fem;
static const int PIN_FEM_POWER = 7;
static const int PIN_FEM_ENABLE = 2;     // CSD on both
static const int PIN_GC1109_PA = 46;     // CPS: HIGH = full PA
static const int PIN_KCT8103L_CTX = 5;   // HIGH while transmitting

static void femInit() {
    pinMode(PIN_FEM_POWER, OUTPUT);
    digitalWrite(PIN_FEM_POWER, HIGH);
    delay(1);
    pinMode(PIN_FEM_ENABLE, INPUT);
    fem = digitalRead(PIN_FEM_ENABLE) == HIGH ? FEM_KCT8103L : FEM_GC1109;
    pinMode(PIN_FEM_ENABLE, OUTPUT);
    digitalWrite(PIN_FEM_ENABLE, HIGH);
    if (fem == FEM_KCT8103L) {
        pinMode(PIN_KCT8103L_CTX, OUTPUT);
        digitalWrite(PIN_KCT8103L_CTX, LOW);
    } else {
        pinMode(PIN_GC1109_PA, OUTPUT);
        digitalWrite(PIN_GC1109_PA, LOW);
    }
    Serial.printf("# RF front end: %s\n", fem == FEM_KCT8103L ? "KCT8103L (V4.3)" : "GC1109 (V4.2)");
}

static void femTransmit(bool tx) {
    if (fem == FEM_KCT8103L) digitalWrite(PIN_KCT8103L_CTX, tx ? HIGH : LOW);
    else digitalWrite(PIN_GC1109_PA, tx ? HIGH : LOW);
}
static const int CHIP_POWER_DBM = TX_POWER_DBM - 11;
#else
static void femInit() {}
static void femTransmit(bool) {}
static const int CHIP_POWER_DBM = TX_POWER_DBM;
#endif

// ---- lookups ----

static ProductPrice *findPrice(const char *id) {
    if (!*id) return nullptr;
    for (int i = 0; i < nPrices; i++)
        if (strcmp(prices[i].id, id) == 0) return &prices[i];
    return nullptr;
}

static Device *findDevice(const uint8_t *addr) {
    for (int i = 0; i < nDevices; i++)
        if (linkSameDevice(devices[i].addr, addr)) return &devices[i];
    return nullptr;
}

static Device *findByLabel(const char *labelId) {
    for (int i = 0; i < nDevices; i++)
        if (devices[i].assigned && strcmp(devices[i].labelId, labelId) == 0) return &devices[i];
    return nullptr;
}

static Device &device(const uint8_t *addr) {
    if (Device *d = findDevice(addr)) return *d;
    int slot;
    if (nDevices < MAX_DEVICES) {
        slot = nDevices++;
    } else {
        // Full: reuse an unassigned one heard from longest ago, else the oldest.
        slot = 0;
        for (int i = 1; i < MAX_DEVICES; i++) {
            const Device &a = devices[i], &b = devices[slot];
            if ((!a.assigned && b.assigned) ||
                (a.assigned == b.assigned && a.lastMs < b.lastMs)) slot = i;
        }
    }
    Device &d = devices[slot];
    memset(&d, 0, sizeof d);
    memcpy(d.addr, addr, LINK_DEVICE_LEN);
    linkDeviceHex(d.hex, addr);
    d.listening = true;   // every v4 label listens; corrected at its first check-in
    d.state = '?';
    return d;
}

// The label ID shown on the OLED and sent to the bridge.
static const char *shortName(const Device &d) {
    static char buf[LINK_ID_LEN + 1];
    if (d.assigned) return d.labelId;
    snprintf(buf, sizeof buf, "new %s", d.hex + 8);   // last 4 hex digits
    return buf;
}

// ---- doorbells ----

static bool canRing(const Device &d) {
    // Not heard since boot: an assignment replayed by the bridge. Ring it
    // anyway; at worst the doorbell goes unanswered.
    if (d.lastMs == 0) return true;
    return d.listening && millis() - d.lastMs < LISTENING_TRUST_MS;
}

static void ring(Device &d) {
    if (!canRing(d)) return;
    d.ringsLeft = RINGS;
    d.ringAtMs = millis();
}

// A price just changed: ring every label for that product whose glass shows
// something else.
static void ringFor(const ProductPrice &p) {
    for (int i = 0; i < nDevices; i++) {
        Device &d = devices[i];
        if (d.assigned && strcmp(d.product, p.id) == 0 && d.shows != p.version) {
            d.state = 'n';
            ring(d);
        }
    }
}

static int sendDoorbell(const uint8_t *addr) {
    LinkDoorbell b = {};
    linkHeader(b.h, LINK_DOORBELL);
    memcpy(b.device, addr, LINK_DEVICE_LEN);
    // The long preamble and the doorbell's own sync word are what a sleeping
    // label's radio listens for; then back to normal, to hear the check-in
    // that answers it.
    radio.setSyncWord(LINK_DOORBELL_SYNC);
    radio.setPreambleLength(LINK_DOORBELL_PREAMBLE);
    femTransmit(true);
    int st = radio.transmit((uint8_t *)&b, sizeof b);
    femTransmit(false);
    radio.setSyncWord(LINK_SYNC_WORD);
    radio.setPreambleLength(LINK_PREAMBLE);
    radioIrq = false;
    radio.startReceive();
    return st;
}

static void serviceDoorbells() {
    if (joinRingsLeft && (int32_t)(millis() - joinAtMs) >= 0) {
        uint8_t everyone[LINK_DEVICE_LEN];
        memset(everyone, 0xFF, sizeof everyone);
        int st = sendDoorbell(everyone);
        joinRingsLeft--;
        joinAtMs = millis() + JOIN_GAP_MS;
        Serial.printf("# doorbell to every label not set up (%s)\n",
                      st == RADIOLIB_ERR_NONE ? "sent" : "transmit failed");
        return;
    }
    for (int i = 0; i < nDevices; i++) {
        Device &d = devices[i];
        if (!d.ringsLeft || (int32_t)(millis() - d.ringAtMs) < 0) continue;
        int st = sendDoorbell(d.addr);
        d.ringsLeft--;
        d.ringAtMs = millis() + RING_GAP_MS + random(0, 500);
        Serial.printf("# doorbell %s (%s)\n", shortName(d), st == RADIOLIB_ERR_NONE ? "sent" : "transmit failed");
        return;   // one per pass, so check-ins in between get heard
    }
}

// ---- serial: bridge -> gateway ----

static void sendHello() {
    JsonDocument doc;
    doc["t"] = "hello";
    doc["role"] = "gateway";
    doc["fw"] = FW_VERSION;
    doc["board"] = GW_BOARD;
    doc["radio"] = radioOk;
    doc["freq_mhz"] = LINK_FREQ_MHZ;
    doc["sf"] = LINK_SF;
    doc["prices"] = nPrices;
    serializeJson(doc, Serial);
    Serial.println();
}

static void onPrice(JsonDocument &doc, const char *line) {
    const char *id = doc["product_id"] | "";
    const char *reason = doc["reason"] | "";
    uint32_t cents = doc["cents"] | 0;
    if (!*id || strlen(id) > LINK_ID_LEN || strlen(reason) > LINK_REASON_LEN || !cents) {
        Serial.printf("# bad price command ignored: %s\n", line);
        return;
    }
    ProductPrice *p = findPrice(id);
    if (!p) {
        if (nPrices == MAX_PRICES) {
            Serial.println("# price table full, ignored");
            return;
        }
        p = &prices[nPrices++];
        strlcpy(p->id, id, sizeof p->id);
    }
    p->cents = cents;
    strlcpy(p->reason, reason, sizeof p->reason);
    p->version = linkPriceVersion(cents, p->reason);
    Serial.printf("# price %s %lu \"%s\"\n", id, (unsigned long)cents, p->reason);
    ringFor(*p);
}

static void onAssign(JsonDocument &doc, const char *line) {
    uint8_t addr[LINK_DEVICE_LEN];
    const char *labelId = doc["label_id"] | "";
    const char *product = doc["product_id"] | "";
    const char *name = doc["name"] | "";
    if (!linkParseDevice(addr, doc["device"] | "") || !*labelId ||
            strlen(labelId) > LINK_ID_LEN || strlen(product) > LINK_ID_LEN) {
        Serial.printf("# bad assign command ignored: %s\n", line);
        return;
    }
    // A label ID moves to a new device when a tag is replaced: the old device
    // stops being that label.
    if (Device *old = findByLabel(labelId)) {
        if (!linkSameDevice(old->addr, addr)) {
            old->assigned = false;
            old->forget = true;
            ring(*old);
        }
    }
    Device &d = device(addr);
    bool changed = !d.assigned || strcmp(d.labelId, labelId) || strcmp(d.product, product) ||
                   strncmp(d.name, name, LINK_NAME_LEN);
    d.assigned = true;
    d.forget = false;
    strlcpy(d.labelId, labelId, sizeof d.labelId);
    strlcpy(d.product, product, sizeof d.product);
    strlcpy(d.name, name, sizeof d.name);
    Serial.printf("# %s is %s for %s\n", d.hex, labelId, *product ? product : "no product");
    if (changed) ring(d);
}

static void onForget(JsonDocument &doc) {
    uint8_t addr[LINK_DEVICE_LEN];
    if (!linkParseDevice(addr, doc["device"] | "")) return;
    Device *d = findDevice(addr);
    if (!d) return;
    d->assigned = false;
    d->forget = true;
    Serial.printf("# %s removed in the console\n", d->hex);
    ring(*d);
}

static void onBlink(JsonDocument &doc) {
    const char *labelId = doc["label_id"] | "";
    int secs = constrain((int)(doc["seconds"] | 3), 1, 30);
    Device *d = findByLabel(labelId);
    if (!d) {
        Serial.printf("# blink for %s, which no device here is\n", labelId);
        return;
    }
    d->blinkSecs = secs;
    d->blinkAtMs = millis();
    ring(*d);
    Serial.printf("# blink %s %d s\n", labelId, secs);
}

static void onBridgeLine(const char *line) {
    JsonDocument doc;
    if (deserializeJson(doc, line)) return;   // not JSON: ignore
    const char *t = doc["t"] | "";
    bridgeSeenMs = millis();
    bridgeEverSeen = true;

    if (strcmp(t, "who") == 0) sendHello();
    else if (strcmp(t, "price") == 0) onPrice(doc, line);
    else if (strcmp(t, "assign") == 0) onAssign(doc, line);
    else if (strcmp(t, "forget") == 0) onForget(doc);
    else if (strcmp(t, "blink") == 0) onBlink(doc);
    else if (strcmp(t, "join") == 0) {
        joinRingsLeft = JOIN_RINGS;
        joinAtMs = millis();
        Serial.println("# Add labels: ringing every label not set up");
    }
    // "ping" needs nothing beyond bridgeSeenMs.
}

static void pollSerial() {
    static char line[512];
    static size_t n = 0;
    while (Serial.available()) {
        char ch = (char)Serial.read();
        if (ch == '\n' || ch == '\r') {
            if (n) {
                line[n] = '\0';
                onBridgeLine(line);
                n = 0;
            }
        } else if (n < sizeof line - 1) {
            line[n++] = ch;
        } else {
            n = 0;   // overlong line: drop it
        }
    }
}

// ---- radio ----

static void IRAM_ATTR onRadioIrq() { radioIrq = true; }

static bool radioBegin() {
    SPI.begin(LINK_PIN_SCK, LINK_PIN_MISO, LINK_PIN_MOSI, LINK_PIN_NSS);
    int st = radio.begin(LINK_FREQ_MHZ, LINK_BW_KHZ, LINK_SF, LINK_CR,
                         LINK_SYNC_WORD, CHIP_POWER_DBM, LINK_PREAMBLE,
                         LINK_TCXO_VOLTS);
    if (st != RADIOLIB_ERR_NONE) {
        Serial.printf("# radio init failed: %d\n", st);
        return false;
    }
    radio.setDio2AsRfSwitch(true);
    radio.setPacketReceivedAction(onRadioIrq);
    radio.startReceive();
    return true;
}

static void onCheckin(const LinkCheckin &c, float rssi, float snr) {
    char reportedLabel[LINK_ID_LEN + 1], reportedProduct[LINK_ID_LEN + 1];
    linkGet(reportedLabel, c.label_id, LINK_ID_LEN);
    linkGet(reportedProduct, c.product_id, LINK_ID_LEN);
    Device &d = device(c.device);

    LinkReply r = {};
    linkHeader(r.h, LINK_REPLY);
    memcpy(r.device, c.device, LINK_DEVICE_LEN);
    r.seq = c.seq;
    r.rssi_dbm = (int8_t)constrain((int)rssi, -128, 127);
    r.snr_db = (int8_t)constrain((int)snr, -128, 127);
    if (d.blinkSecs && millis() - d.blinkAtMs < BLINK_EXPIRY_MS) r.blink_secs = d.blinkSecs;
    d.blinkSecs = 0;

    // Which product's price to send. The console's assignment wins. A label
    // the console hasn't told us about yet (this gateway just rebooted and the
    // bridge hasn't replayed) keeps working on what it says it is, and is
    // told nothing about its assignment, so it keeps it.
    const char *product = reportedProduct;
    if (d.forget) {
        r.flags |= LINK_FORGET;
        product = "";
    } else if (d.assigned) {
        r.flags |= LINK_ASSIGNED;
        linkPut(r.label_id, LINK_ID_LEN, d.labelId);
        linkPut(r.product_id, LINK_ID_LEN, d.product);
        memset(r.name, 0, LINK_NAME_LEN);
        memcpy(r.name, d.name, strlen(d.name));
        product = d.product;
    }
    ProductPrice *p = findPrice(product);
    if (p) {
        r.flags |= LINK_HAS_PRICE;
        r.cents = p->cents;
        // Exactly 16 bytes, NUL-padded; a 16-character reason has no NUL.
        memset(r.reason, 0, LINK_REASON_LEN);
        memcpy(r.reason, p->reason, strlen(p->reason));
    }

    // Reply first, report after: the label is listening for only 600 ms.
    delay(TURNAROUND_MS);
    femTransmit(true);
    int st = radio.transmit((uint8_t *)&r, sizeof r);
    femTransmit(false);
    radioIrq = false;   // the transmit-done interrupt is not a packet
    radio.startReceive();

    bool wasForget = d.forget;
    d.forget = false;   // told once; it goes back to "Not set up"
    d.lastMs = millis();
    d.shows = c.shows;
    d.rssi = (int16_t)rssi;
    d.listening = c.flags & LINK_LISTENING;
    d.ringsLeft = 0;    // it has checked in: the reply carried everything
    d.state = !d.assigned && !*reportedLabel ? '?'
            : !p ? '-' : (c.shows == p->version ? 'k' : 'n');

    JsonDocument doc;
    doc["t"] = "checkin";
    doc["device"] = d.hex;
    // The console's label for it if we know one, else what the label says.
    doc["label_id"] = wasForget ? "" : d.assigned ? d.labelId : reportedLabel;
    doc["product_id"] = product;
    doc["button"] = (c.flags & LINK_BUTTON) != 0;
    doc["shows"] = c.shows;
    doc["current"] = p ? p->version : 0;
    if (p) doc["cents"] = p->cents;
    doc["battery_mv"] = c.battery_mv;
    doc["firmware"] = c.firmware;
    doc["rssi"] = (int)rssi;
    doc["snr"] = snr;
    doc["seq"] = c.seq;
    doc["blink"] = r.blink_secs;
    doc["replied"] = st == RADIOLIB_ERR_NONE;
    doc["listening"] = d.listening;
    serializeJson(doc, Serial);
    Serial.println();
}

static void pollRadio() {
    if (!radioIrq) return;
    radioIrq = false;
    uint8_t buf[128];
    size_t len = radio.getPacketLength();
    if (len > sizeof buf) len = sizeof buf;
    int st = radio.readData(buf, len);
    float rssi = radio.getRSSI();
    float snr = radio.getSNR();
    digitalWrite(PIN_LED, HIGH);

    if (st == RADIOLIB_ERR_NONE && linkValid(buf, len, LINK_CHECKIN, sizeof(LinkCheckin))) {
        LinkCheckin c;
        memcpy(&c, buf, sizeof c);
        onCheckin(c, rssi, snr);   // restarts receive itself
    } else {
        // Our own reply heard back, a corrupt packet, or someone else's LoRa.
        radio.startReceive();
    }
    digitalWrite(PIN_LED, LOW);
}

// ---- OLED ----

static void drawOled() {
    static uint32_t last = 0;
    if (millis() - last < 500) return;
    last = millis();

    char buf[32];
    oled.clearBuffer();
    oled.setFont(u8g2_font_6x10_tf);
    oled.drawStr(0, 9, "ShelfIQ LoRa gateway");

    bool bridgeUp = bridgeEverSeen && millis() - bridgeSeenMs < BRIDGE_TIMEOUT_MS;
    oled.drawStr(0, 20, !radioOk ? "RADIO FAILED, see USB"
                        : joinRingsLeft ? "Finding new labels..."
                        : bridgeUp ? "Bridge: connected"
                        : "Bridge: not connected");
    snprintf(buf, sizeof buf, "%.1fMHz SF%d %s", LINK_FREQ_MHZ, LINK_SF, GW_BOARD);
    oled.drawStr(0, 31, buf);
    int setUp = 0;
    for (int i = 0; i < nDevices; i++) setUp += devices[i].assigned;
    snprintf(buf, sizeof buf, "Prices %d Labels %d/%d", nPrices, setUp, nDevices);
    oled.drawStr(0, 42, buf);

    // The two devices heard from most recently.
    int a = -1, b = -1;
    for (int i = 0; i < nDevices; i++) {
        if (!devices[i].lastMs) continue;
        if (a < 0 || devices[i].lastMs > devices[a].lastMs) { b = a; a = i; }
        else if (b < 0 || devices[i].lastMs > devices[b].lastMs) b = i;
    }
    int y = 53;
    for (int i : {a, b}) {
        if (i < 0) break;
        const Device &d = devices[i];
        uint32_t ago = (millis() - d.lastMs) / 1000;
        const char *st = d.state == 'k' ? "ok" : d.state == 'n' ? "new"
                       : d.state == '?' ? "set" : "--";
        // 21 characters, the width of the screen: "SHLF-0108 -47  4s new"
        snprintf(buf, sizeof buf, "%-9.9s%4d%4lus %s", shortName(d), d.rssi,
                 (unsigned long)(ago > 999 ? 999 : ago), st);
        oled.drawStr(0, y, buf);
        y += 11;
    }
    oled.sendBuffer();
}

// ---- main ----

void setup() {
    Serial.begin(115200);
    delay(300);
    pinMode(PIN_LED, OUTPUT);
    digitalWrite(PIN_LED, LOW);

    pinMode(PIN_VEXT, OUTPUT);
    digitalWrite(PIN_VEXT, LOW);   // OLED power on
    delay(50);
    oled.setBusClock(400000);   // a full frame in ~25 ms, so drawing never delays a reply
    oled.begin();

    femInit();
    radioOk = radioBegin();
    Serial.printf("# ShelfIQ LoRa gateway %s (%s), %.1f MHz SF%d, radio %s\n",
                  FW_VERSION, GW_BOARD, LINK_FREQ_MHZ, LINK_SF, radioOk ? "ok" : "FAILED");
    sendHello();
}

void loop() {
    pollSerial();
    if (radioOk) {
        pollRadio();
        serviceDoorbells();
    }
    drawOled();
}
