// The LoRa link between shelf labels (edge/eink-e213) and the gateway
// (edge/lora-gateway). Both firmwares include this file, so the two sides
// cannot disagree about a radio setting or a packet layout.
//
// This is not a contract in the contracts/ sense: it never leaves the edge
// layer. The gateway's bridge (tools/lora_bridge.py) turns it back into MQTT
// topics, so nothing above Layer 2 knows LoRa exists.
//
// Every label is addressed by its device ID, the ESP32's factory MAC address.
// It knows nothing else when it leaves the box: its label ID, product and
// product name come from the console, inside the gateway's replies.
//
// The exchange is always started by the label, because a label spends almost
// all its time asleep:
//
//   label   --CHECKIN-->  gateway     "I'm device D, showing price version V"
//   label  <--REPLY-----  gateway     "you are SHLF-0108 for SKU-002, Sourdough
//                                      Loaf 800g, and its price is $3.49"
//
// The label saves any change to its assignment, compares the price with what
// its glass shows, and redraws only if something differs. Its next check-in
// reports the new version, which is how the gateway learns the price reached
// the shelf.
//
// Between check-ins a label listens for a doorbell. When a new price or an
// assignment arrives, the gateway rings it:
//
//   label  <--DOORBELL--  gateway     "device D, check in now"
//
// and it checks in at once. A doorbell to the broadcast address wakes every
// label that isn't set up yet, which is how "Add labels" in the console finds
// a box of new tags. The doorbell carries nothing else, so a missed one only
// costs time: the next regular check-in fetches everything anyway.
//
// A sleeping label hears doorbells by "wake on radio": its ESP32 is in deep
// sleep while the SX1262 listens for about 15 ms every quarter second. A
// doorbell starts with a long preamble so that one of those short listens
// always lands on it. Doorbells also use their own sync word, so the ordinary
// check-ins and replies of other labels never wake a sleeping one.
#pragma once

#include <stdint.h>
#include <stdio.h>
#include <string.h>

// ---- radio settings ----
// US915 ISM band. SF7 at 125 kHz keeps a reply under 200 ms of airtime, well
// inside the 400 ms per-transmission dwell limit for this band, and still
// carries across a store floor.
#define LINK_FREQ_MHZ   915.0
#define LINK_BW_KHZ     125.0
#define LINK_SF         7
#define LINK_CR         5          // coding rate 4/5
#define LINK_SYNC_WORD  0x12       // private network; LoRaWAN uses 0x34
#define LINK_PREAMBLE   8
#define LINK_TCXO_VOLTS 1.8        // the SX1262 TCXO on all three Heltec boards

// Doorbells only. 256 symbols at SF7 is 262 ms of preamble, about 0.3 s on air
// in all: under the band's 400 ms limit, and long enough for a receiver that
// listens every ~250 ms. A longer preamble lets labels sleep longer but
// breaks that limit.
#define LINK_DOORBELL_SYNC     0x2B
#define LINK_DOORBELL_PREAMBLE 256

// SX1262 SPI and control pins. The same on the Vision Master E213 and the
// WiFi LoRa 32 V3 and V4.
#define LINK_PIN_NSS    8
#define LINK_PIN_SCK    9
#define LINK_PIN_MOSI   10
#define LINK_PIN_MISO   11
#define LINK_PIN_RST    12
#define LINK_PIN_BUSY   13
#define LINK_PIN_DIO1   14

// ---- packets ----
#define LINK_MAGIC_0  'S'
#define LINK_MAGIC_1  'Q'
// 2: check-in flags and the doorbell; 3: wake-on-radio doorbell;
// 4: addressed by device ID, assignment in the reply, broadcast doorbell
#define LINK_VERSION  4

#define LINK_DEVICE_LEN 6          // ESP32 factory MAC
#define LINK_ID_LEN     12         // "SHLF-0108" / "SKU-002", NUL-padded
#define LINK_REASON_LEN 16         // reason_tag; NUL-terminated only if shorter
#define LINK_NAME_LEN   32         // product name on the glass; same rule

enum : uint8_t {
    LINK_CHECKIN = 1,
    LINK_REPLY = 2,
    LINK_DOORBELL = 3,
};

// Check-in flags
#define LINK_LISTENING 0x01        // listens for a doorbell until its next check-in
#define LINK_BUTTON    0x02        // its button was just pressed: "this is the one in my hand"

// Reply flags
#define LINK_HAS_PRICE 0x01        // cents and reason are the current price of its product
#define LINK_ASSIGNED  0x02        // label_id, product_id and name are its assignment
#define LINK_FORGET    0x04        // the console removed this device: back to "Not set up"

#pragma pack(push, 1)
struct LinkHeader {
    uint8_t magic[2];
    uint8_t version;
    uint8_t type;
};

struct LinkCheckin {
    LinkHeader h;
    uint8_t device[LINK_DEVICE_LEN];
    char label_id[LINK_ID_LEN];    // "" until assigned
    char product_id[LINK_ID_LEN];  // "" if it has no product
    uint32_t shows;                // linkPriceVersion() of the glass; 0 = no price
    uint16_t battery_mv;           // 0 = unknown
    uint16_t seq;                  // per label; the reply echoes it
    uint8_t flags;
    uint8_t firmware;              // the label firmware's version number
};

struct LinkReply {
    LinkHeader h;
    uint8_t device[LINK_DEVICE_LEN];  // every label hears every reply
    uint16_t seq;
    uint8_t flags;
    uint8_t blink_secs;            // 0 = no blink requested
    uint32_t cents;
    char reason[LINK_REASON_LEN];
    int8_t rssi_dbm;               // how the gateway heard this check-in
    int8_t snr_db;
    char label_id[LINK_ID_LEN];
    char product_id[LINK_ID_LEN];
    char name[LINK_NAME_LEN];
};

struct LinkDoorbell {
    LinkHeader h;
    uint8_t device[LINK_DEVICE_LEN];  // all 0xFF: every label not yet set up
};
#pragma pack(pop)

static_assert(sizeof(LinkCheckin) == 44, "check-in layout changed");
static_assert(sizeof(LinkReply) == 92, "reply layout changed");
static_assert(sizeof(LinkDoorbell) == 10, "doorbell layout changed");

static inline void linkHeader(LinkHeader &h, uint8_t type) {
    h.magic[0] = LINK_MAGIC_0;
    h.magic[1] = LINK_MAGIC_1;
    h.version = LINK_VERSION;
    h.type = type;
}

static inline bool linkValid(const uint8_t *buf, size_t len, uint8_t type, size_t size) {
    if (len != size) return false;
    const LinkHeader *h = (const LinkHeader *)buf;
    return h->magic[0] == LINK_MAGIC_0 && h->magic[1] == LINK_MAGIC_1 &&
           h->version == LINK_VERSION && h->type == type;
}

static inline bool linkIsBroadcast(const uint8_t *device) {
    for (int i = 0; i < LINK_DEVICE_LEN; i++)
        if (device[i] != 0xFF) return false;
    return true;
}

static inline bool linkSameDevice(const uint8_t *a, const uint8_t *b) {
    return memcmp(a, b, LINK_DEVICE_LEN) == 0;
}

// "80B54EDCA3D4": how a device ID is written everywhere above the radio.
static inline void linkDeviceHex(char out[2 * LINK_DEVICE_LEN + 1], const uint8_t *device) {
    for (int i = 0; i < LINK_DEVICE_LEN; i++) snprintf(out + 2 * i, 3, "%02X", device[i]);
}

static inline bool linkParseDevice(uint8_t *device, const char *hex) {
    if (strlen(hex) != 2 * LINK_DEVICE_LEN) return false;
    for (int i = 0; i < LINK_DEVICE_LEN; i++) {
        unsigned v;
        if (sscanf(hex + 2 * i, "%2X", &v) != 1) return false;
        device[i] = (uint8_t)v;
    }
    return true;
}

// Copy a C string into a fixed packet field, NUL-padded.
static inline void linkPut(char *field, size_t n, const char *s) {
    memset(field, 0, n);
    strncpy(field, s, n);
}

// Copy a fixed packet field out as a C string. `out` holds at least n + 1.
static inline void linkGet(char *out, const char *field, size_t n) {
    memcpy(out, field, n);
    out[n] = '\0';
}

// Identifies a price as the label would draw it. Both sides compute it the
// same way, so "the label shows version V" can be checked against "the current
// price is version V" without sending the price back. Never 0, which means
// "nothing shown". tools/lora_bridge.py has a copy (price_version) that must
// stay in step.
static inline uint32_t linkPriceVersion(uint32_t cents, const char *reason) {
    uint32_t h = 2166136261u;
    for (int i = 0; i < 4; i++) {
        h ^= (uint8_t)(cents >> (8 * i));
        h *= 16777619u;
    }
    for (size_t i = 0; i < LINK_REASON_LEN && reason[i]; i++) {
        h ^= (uint8_t)reason[i];
        h *= 16777619u;
    }
    return h ? h : 1;
}
