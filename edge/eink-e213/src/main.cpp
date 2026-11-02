/*
 * Shelf-edge label: Heltec Vision Master E213 or Wireless Paper (ESP32-S3,
 * 2.13" e-ink, 250x122, SX1262 LoRa). One source, one build per board
 * (platformio.ini); what differs between them is in "Board" below.
 *
 * Every label runs this same firmware and is configured from the console, not
 * at build time. Out of the box it shows "Not set up" with its device ID (the
 * ESP32's factory MAC). In the console, Shelf labels > Add labels rings every
 * label not yet set up; pressing this label's button tells the console which
 * one you're holding; assigning it there sends its label ID, product and
 * product name down in the gateway's next reply. All of that is kept in flash,
 * so it survives deep sleep and power cuts.
 *
 * No Wi-Fi. The label checks in with the LoRa gateway (edge/lora-gateway),
 * which answers with its assignment and the current price of its product. The
 * packets are in edge/common/shelf_link.h.
 *
 * E-ink keeps its image with the power off, so the glass is redrawn only when
 * what it should show has changed. A fingerprint of the last screen drawn is
 * kept in flash, so a wake-up that changes nothing costs no refresh, and the
 * shelf is never blank.
 *
 * Between check-ins the label listens for a doorbell: the gateway rings it the
 * moment a new price, an assignment or a blink arrives, and it checks in at
 * once, so a change shows up in about 3 s. With DEEP_SLEEP 1 the ESP32 is in
 * deep sleep while the radio listens on a short duty cycle ("wake on radio"),
 * and a doorbell wakes it. With DEEP_SLEEP 0 it stays awake, which keeps the
 * USB serial console up. Either way the label's button (21 on the E213, PRG
 * on the Wireless Paper)
 * checks in now, and says "this is the one in my hand" while setting up.
 */
#include <sys/time.h>
#include <Arduino.h>
#include <Preferences.h>
#include <RadioLib.h>
#include <SPI.h>
#include <driver/rtc_io.h>
#include <esp_sleep.h>
#include <esp_system.h>
#include <heltec-eink-modules.h>
#include "Fonts/FreeSans9pt7b.h"
#include "Fonts/FreeSansBold9pt7b.h"
#include "Fonts/FreeSansBold18pt7b.h"
#include "Fonts/FreeSansBold24pt7b.h"

#include "config.h"
#include "shelf_link.h"

// ---- board ----
// Pins from the Heltec schematics; Meshtastic's variants for both boards agree.
#if defined(BOARD_WIRELESS_PAPER)
static const uint8_t BOARD_CODE = 1;          // "wpaper" in the console
static const char *BOARD_NAME = "Wireless Paper";
static const int PIN_BUTTON = 0;              // "PRG", also the boot button
static const char *BUTTON_NAME = "PRG";
static const int PIN_LED = 18;
static const int PIN_BAT_ADC = 20;
static const int PIN_BAT_ADC_CTRL = 19;
static const uint8_t BAT_ADC_ON = LOW;        // LOW connects the battery divider
static const adc_attenuation_t BAT_ADC_ATTEN = ADC_11db;
static const float BAT_DIVIDER = 2.0f;        // roughly 1:1
// GPIO45 powers the radio's front end as well as the display, so it has to
// stay on through deep sleep or the radio can't hear a doorbell.
static const bool VEXT_POWERS_RADIO = true;
#else  // Vision Master E213
static const uint8_t BOARD_CODE = 0;          // "e213" in the console
static const char *BOARD_NAME = "Vision Master E213";
static const int PIN_BUTTON = 21;             // the button beside the USB port
static const char *BUTTON_NAME = "21";
static const int PIN_LED = 45;                // not fitted on the first revision
static const int PIN_BAT_ADC = 7;
static const int PIN_BAT_ADC_CTRL = 46;
static const uint8_t BAT_ADC_ON = HIGH;       // HIGH connects the battery divider
static const adc_attenuation_t BAT_ADC_ATTEN = ADC_2_5db;
static const float BAT_DIVIDER = 4.9f * 1.03f;  // with Meshtastic's calibration
// GPIO18 powers only the display and the I2C connector.
static const bool VEXT_POWERS_RADIO = false;
#endif

// Reported in every check-in: the version in the low six bits, the board in
// the top two. The console shows it as "e213-4" or "wpaper-4".
static const uint8_t FIRMWARE = 4 | (BOARD_CODE << 6);

// Heltec has fitted this board with two panels that need different drivers:
// LCMEN2R13EFC1 on the first boards, E0213A367 from V1.1 (mid 2025) onwards.
// Neither driver touches the hardware until it first draws, so both are
// built and detectPanel() picks one at boot. PANEL in config.h can force it.
#ifndef PANEL
#define PANEL 0
#endif
LCMEN2R13EFC1 panelOriginal;
E0213A367 panelV1_1;
BaseDisplay *display = &panelV1_1;

// Bump when the layout changes, so a reflashed label redraws even though what
// it holds has not changed.
static const int LAYOUT_VERSION = 4;
static const int MARGIN = 6;

static const uint32_t REPLY_WAIT_MS = 600;
static const int ATTEMPTS = 3;
// Labels woken together by "Add labels" answer after a random delay up to
// this, so a box of them doesn't transmit at once.
static const uint32_t BROADCAST_SPREAD_MS = 3000;

// The display library takes HSPI; the radio gets FSPI to itself.
SPIClass loraSPI(FSPI);
SX1262 radio = new Module(LINK_PIN_NSS, LINK_PIN_DIO1, LINK_PIN_RST, LINK_PIN_BUSY, loraSPI);
bool radioReady = false;
volatile bool radioIrq = false;

Preferences prefs;

uint8_t myDevice[LINK_DEVICE_LEN];
char myDeviceHex[2 * LINK_DEVICE_LEN + 1];

// Survive deep sleep, unlike ordinary globals.
RTC_DATA_ATTR uint16_t seq = 0;
RTC_DATA_ATTR uint16_t missedInARow = 0;
RTC_DATA_ATTR uint8_t panelFound = 0;   // detected once per power-on, not per wake
RTC_DATA_ATTR int64_t nextCheckinUs = 0; // on the RTC clock, which runs through deep sleep
// Whether this label has a label ID, for the radio wake-up path, which runs
// before flash is read. Kept in step with `assignment` by saveAssignment().
RTC_DATA_ATTR bool setUpCache = false;

// Pressed since the last check-in that got through: the next one says so.
bool buttonPressed = false;

// ---- what the label holds, mirrored in flash ----
struct Assignment {
    char labelId[LINK_ID_LEN + 1] = "";   // "" = not set up
    char product[LINK_ID_LEN + 1] = "";   // "" = no product
    char name[LINK_NAME_LEN + 1] = "";
};
Assignment assignment;

struct Price {
    bool has = false;
    uint32_t cents = 0;
    char reason[LINK_REASON_LEN + 1] = "";
};
Price shown;

// ---- screen fingerprint ----

static uint32_t fnv1a(const char *s) {
    uint32_t h = 2166136261u;
    while (*s) {
        h ^= (uint8_t)*s++;
        h *= 16777619u;
    }
    return h;
}

static uint32_t currentKey() {
    char buf[200];
    snprintf(buf, sizeof buf, "%d|%s|%s|%s|%s|%d|%lu|%s", LAYOUT_VERSION, myDeviceHex,
             assignment.labelId, assignment.product, assignment.name, shown.has,
             (unsigned long)shown.cents, shown.reason);
    return fnv1a(buf);
}

// ---- drawing ----

// Copy `in` to `out`, cut with "..." if it is wider than maxW in the current font.
static void fitText(const char *in, char *out, size_t n, int maxW) {
    strlcpy(out, in, n);
    if (display->getTextWidth(out) <= maxW) return;
    for (int len = (int)strlen(in) - 1; len > 0; len--) {
        snprintf(out, n, "%.*s...", len, in);
        if (display->getTextWidth(out) <= maxW) return;
    }
    strlcpy(out, "...", n);
}

static void startFrame(bool inverted, uint16_t &fg) {
    display->clearMemory();
    display->landscape();
    fg = inverted ? WHITE : BLACK;
    if (inverted) display->fillScreen(BLACK);
    display->setTextColor(fg);
}

// Footer: a rule, then two short strings in the built-in 6x8 font, for
// whoever is restocking rather than the shopper.
static void footer(uint16_t fg, const char *left, const char *right) {
    const int W = display->width();
    display->drawLine(0, 101, W, 101, fg);
    display->setFont(NULL);
    display->setCursor(MARGIN, 108);
    display->print(left);
    display->setCursor(W - MARGIN - 6 * (int)strlen(right), 108);
    display->print(right);
}

static void line(int y, const char *text) {
    display->setCursor(MARGIN, y);
    display->print(text);
}

// Out of the box, or removed in the console.
static void drawNotSetUp(bool inverted) {
    uint16_t fg;
    startFrame(inverted, fg);
    char buf[48];
    display->setFont(&FreeSansBold9pt7b);
    line(22, "Not set up");
    display->setFont(NULL);
    snprintf(buf, sizeof buf, "Device  %s", myDeviceHex);
    line(42, buf);
    line(60, "In the console: Shelf labels >");
    line(72, "Add labels, then press this");
    snprintf(buf, sizeof buf, "label's button (%s).", BUTTON_NAME);
    line(84, buf);
    footer(fg, "ShelfIQ e-ink label", "");
    display->update();
}

// Set up, but with no product, or a product with no price yet.
static void drawReady(bool inverted) {
    uint16_t fg;
    startFrame(inverted, fg);
    const int W = display->width();
    char buf[64];
    display->setFont(&FreeSansBold18pt7b);
    line(40, assignment.labelId);
    display->setFont(&FreeSans9pt7b);
    if (*assignment.product) {
        fitText(*assignment.name ? assignment.name : assignment.product, buf, sizeof buf, W - 2 * MARGIN);
        line(66, buf);
        line(88, "Waiting for its price");
    } else {
        line(66, "Ready, not assigned");
        line(88, "Give it a product in the console");
    }
    footer(fg, *assignment.product ? assignment.product : "", assignment.labelId);
    display->update();
}

static void drawPrice(bool inverted) {
    uint16_t fg;
    startFrame(inverted, fg);
    const int W = display->width();
    char buf[64];

    display->setFont(&FreeSansBold9pt7b);
    fitText(*assignment.name ? assignment.name : assignment.product, buf, sizeof buf, W - 2 * MARGIN);
    line(18, buf);

    char money[16];
    snprintf(money, sizeof money, "$%lu.%02lu", (unsigned long)(shown.cents / 100),
             (unsigned long)(shown.cents % 100));
    display->setFont(&FreeSansBold24pt7b);
    if (display->getTextWidth(money) > W - 2 * MARGIN)
        display->setFont(&FreeSansBold18pt7b);
    // No reason tag (the manager gave none): the price moves down into the
    // space the reason line would have used, between the name and the footer.
    line(*shown.reason ? 66 : 78, money);

    if (*shown.reason) {
        display->setFont(&FreeSans9pt7b);
        fitText(shown.reason, buf, sizeof buf, W - 2 * MARGIN);
        line(92, buf);
    }

    footer(fg, assignment.product, assignment.labelId);
    display->update();
}

static void drawCurrent(bool inverted) {
    if (!*assignment.labelId) drawNotSetUp(inverted);
    else if (!*assignment.product || !shown.has) drawReady(inverted);
    else drawPrice(inverted);
}

// Redraw only if the glass does not already show this screen.
static void ensureDrawn() {
    uint32_t want = currentKey();
    if (prefs.getUInt("screen", 0) == want) return;
    Serial.println("redrawing");
    drawCurrent(false);
    prefs.putUInt("screen", want);
}

// ---- blink ----

static void blink(int seconds) {
    Serial.printf("blink %d s\n", seconds);
    // If the power goes while inverted, the next boot must redraw.
    prefs.putUInt("screen", 0);

    uint32_t until = millis() + (uint32_t)seconds * 1000;
    drawCurrent(true);   // a full refresh flashes on its own, which helps
    bool on = true;
    while ((int32_t)(until - millis()) > 0) {
        digitalWrite(PIN_LED, on ? HIGH : LOW);
        on = !on;
        delay(250);
    }
    digitalWrite(PIN_LED, LOW);
    drawCurrent(false);
    prefs.putUInt("screen", currentKey());
}

// ---- panel detection ----

// Holding the panel in reset makes it report busy, and the two controllers
// signal busy opposite ways: the Fitipower one (LCMEN2R13EFC1) pulls BUSY low,
// the Solomon Systech one (E0213A367) drives it high. The same test
// Meshtastic uses on this board.
static uint8_t detectPanel() {
    Platform::VExtOn();   // the panel is unpowered until this
    pinMode(PIN_DISPLAY_RST, OUTPUT);
    digitalWrite(PIN_DISPLAY_RST, LOW);
    delay(10);
    pinMode(PIN_DISPLAY_BUSY, INPUT);
    bool busyHigh = digitalRead(PIN_DISPLAY_BUSY) == HIGH;
    pinMode(PIN_DISPLAY_RST, INPUT);
    return busyHigh ? 2 : 1;
}

static void choosePanel() {
    if (PANEL) panelFound = PANEL;
    else if (!panelFound) panelFound = detectPanel();
    display = panelFound == 1 ? (BaseDisplay *)&panelOriginal : (BaseDisplay *)&panelV1_1;
    Serial.printf("panel: %s%s\n",
                  panelFound == 1 ? "LCMEN2R13EFC1 (original E213)" : "E0213A367 (E213 V1.1 and later)",
                  PANEL ? ", forced by PANEL in config.h" : "");
}

// ---- persistence ----

static void loadState() {
    prefs.getString("label", assignment.labelId, sizeof assignment.labelId);
    prefs.getString("product", assignment.product, sizeof assignment.product);
    prefs.getString("name", assignment.name, sizeof assignment.name);
    setUpCache = *assignment.labelId;
    if (prefs.isKey("cents")) {
        shown.has = true;
        shown.cents = prefs.getULong("cents", 0);
        prefs.getString("reason", shown.reason, sizeof shown.reason);
    }
}

static void saveAssignment() {
    prefs.putString("label", assignment.labelId);
    prefs.putString("product", assignment.product);
    prefs.putString("name", assignment.name);
    setUpCache = *assignment.labelId;
}

static void saveShown() {
    if (shown.has) {
        prefs.putULong("cents", shown.cents);
        prefs.putString("reason", shown.reason);
    } else {
        prefs.remove("cents");
        prefs.remove("reason");
    }
}

// ---- battery ----

static uint16_t batteryMv() {
    pinMode(PIN_BAT_ADC_CTRL, OUTPUT);
    digitalWrite(PIN_BAT_ADC_CTRL, BAT_ADC_ON);
    delay(5);
    analogSetPinAttenuation(PIN_BAT_ADC, BAT_ADC_ATTEN);
    uint32_t sum = 0;
    for (int i = 0; i < 8; i++) sum += analogReadMilliVolts(PIN_BAT_ADC);
    digitalWrite(PIN_BAT_ADC_CTRL, !BAT_ADC_ON);
    uint32_t mv = (uint32_t)(sum / 8 * BAT_DIVIDER);
    return mv < 2500 ? 0 : (uint16_t)mv;   // no LiPo reads this low: none fitted
}

// ---- radio ----

static void IRAM_ATTR onRadioIrq() { radioIrq = true; }

static bool radioBegin() {
    if (radioReady) return true;
    gpio_hold_dis((gpio_num_t)LINK_PIN_NSS);   // held HIGH through deep sleep
    loraSPI.begin(LINK_PIN_SCK, LINK_PIN_MISO, LINK_PIN_MOSI, LINK_PIN_NSS);
    int st = radio.begin(LINK_FREQ_MHZ, LINK_BW_KHZ, LINK_SF, LINK_CR,
                         LINK_SYNC_WORD, LORA_POWER_DBM, LINK_PREAMBLE,
                         LINK_TCXO_VOLTS);
    if (st != RADIOLIB_ERR_NONE) {
        Serial.printf("radio init failed: %d\n", st);
        return false;
    }
    radio.setDio2AsRfSwitch(true);
    radio.setPacketReceivedAction(onRadioIrq);
    radioReady = true;
    return true;
}

// One check-in and the wait for its reply. True if a reply for this label came back.
static bool exchange(LinkReply &reply) {
    LinkCheckin c = {};
    linkHeader(c.h, LINK_CHECKIN);
    memcpy(c.device, myDevice, LINK_DEVICE_LEN);
    linkPut(c.label_id, LINK_ID_LEN, assignment.labelId);
    linkPut(c.product_id, LINK_ID_LEN, assignment.product);
    c.shows = shown.has ? linkPriceVersion(shown.cents, shown.reason) : 0;
    c.battery_mv = batteryMv();
    c.seq = ++seq;
    c.flags = LINK_LISTENING | (buttonPressed ? LINK_BUTTON : 0);
    c.firmware = FIRMWARE;

    int st = radio.transmit((uint8_t *)&c, sizeof c);
    if (st != RADIOLIB_ERR_NONE) {
        Serial.printf("transmit failed: %d\n", st);
        return false;
    }
    radioIrq = false;   // the transmit-done interrupt is not a packet
    radio.startReceive();

    uint32_t start = millis();
    while (millis() - start < REPLY_WAIT_MS) {
        if (!radioIrq) {
            delay(2);
            continue;
        }
        radioIrq = false;
        uint8_t buf[128];
        size_t len = radio.getPacketLength();
        if (len > sizeof buf) len = sizeof buf;
        st = radio.readData(buf, len);
        // Another label's check-in, or a reply meant for someone else: keep listening.
        if (st == RADIOLIB_ERR_NONE && linkValid(buf, len, LINK_REPLY, sizeof(LinkReply))) {
            memcpy(&reply, buf, sizeof reply);
            if (linkSameDevice(reply.device, myDevice) && reply.seq == c.seq) {
                radio.standby();
                return true;
            }
        }
        radio.startReceive();
    }
    radio.standby();
    return false;
}

// Check in, retrying with a random back-off in case another label talked over us.
static bool checkIn(LinkReply &reply) {
    for (int i = 0; i < ATTEMPTS; i++) {
        if (i > 0) delay(random(150, 600));
        if (exchange(reply)) return true;
    }
    return false;
}

// Take in a reply. True if anything on the glass has to change.
static bool apply(const LinkReply &r) {
    bool changed = false;

    if (r.flags & LINK_FORGET) {
        Serial.println("removed in the console: back to \"Not set up\"");
        assignment = Assignment();
        shown = Price();
        saveAssignment();
        saveShown();
        return true;
    }

    if (r.flags & LINK_ASSIGNED) {
        Assignment a;
        linkGet(a.labelId, r.label_id, LINK_ID_LEN);
        linkGet(a.product, r.product_id, LINK_ID_LEN);
        linkGet(a.name, r.name, LINK_NAME_LEN);
        if (strcmp(a.labelId, assignment.labelId) || strcmp(a.product, assignment.product) ||
                strcmp(a.name, assignment.name)) {
            Serial.printf("assigned: %s for %s \"%s\"\n", a.labelId,
                          *a.product ? a.product : "no product", a.name);
            // A different product: its old price must not stay under the new name.
            if (strcmp(a.product, assignment.product)) {
                shown = Price();
                saveShown();
            }
            assignment = a;
            saveAssignment();
            changed = true;
        }
    }

    if ((r.flags & LINK_HAS_PRICE) && *assignment.product) {
        Price p;
        p.has = true;
        p.cents = r.cents;
        linkGet(p.reason, r.reason, LINK_REASON_LEN);
        Serial.printf("gateway: $%lu.%02lu \"%s\" (heard us at %d dBm, SNR %d)\n",
                      (unsigned long)(p.cents / 100), (unsigned long)(p.cents % 100),
                      p.reason, r.rssi_dbm, r.snr_db);
        if (p.cents > 0 && (!shown.has ||
                linkPriceVersion(p.cents, p.reason) != linkPriceVersion(shown.cents, shown.reason))) {
            shown = p;
            saveShown();
            changed = true;
        }
    } else if (*assignment.product) {
        Serial.printf("gateway has no price for %s yet\n", assignment.product);
    }
    return changed;
}

// ---- one wake-up ----

static void cycle() {
    if (!radioBegin()) return;

    // Up to twice: the second check-in, after a redraw, reports the new version
    // so the gateway learns at once what reached the glass.
    for (int round = 0; round < 2; round++) {
        LinkReply r;
        if (!checkIn(r)) {
            missedInARow++;
            Serial.printf("no reply from the gateway (%u in a row), holding what's on the glass\n",
                          missedInARow);
            return;
        }
        missedInARow = 0;
        buttonPressed = false;   // the console has heard it

        bool changed = apply(r);
        ensureDrawn();
        if (r.blink_secs) blink(constrain((int)r.blink_secs, 1, 30));
        if (!changed) return;
    }
}

// ---- sleep ----

static int64_t nowUs() {
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    return (int64_t)tv.tv_sec * 1000000 + tv.tv_usec;
}

static void scheduleNextCheckin() {
    // After a check-in nobody answered (gateway unplugged, rebooting, out of
    // range), try again sooner: 30 s, 60, 120, 240, 480, 960, then the usual
    // interval. Otherwise a label powered on before its gateway sits unheard
    // for half an hour.
    int64_t secs = WAKE_SECS;
    if (missedInARow > 0) {
        int64_t retry = 30LL << (missedInARow > 7 ? 6 : missedInARow - 1);
        if (retry < secs) secs = retry;
    }
    // Jitter, so labels powered on together drift apart instead of checking
    // in over each other forever: WAKE_JITTER_SECS either side, but never more
    // than a tenth of the wait, which keeps a 30 s retry a retry.
    int64_t jitterMs = WAKE_JITTER_SECS * 1000LL;
    if (jitterMs > secs * 100) jitterMs = secs * 100;
    nextCheckinUs = nowUs() + secs * 1000000 + (int64_t)random(-(long)jitterMs, (long)jitterMs + 1) * 1000;
}

// Doorbells have their own sync word and a long preamble (shelf_link.h). The
// radio is switched to them only while listening, and back before a check-in.
static void doorbellMode(bool on) {
    radio.setSyncWord(on ? LINK_DOORBELL_SYNC : LINK_SYNC_WORD);
    radio.setPreambleLength(on ? LINK_DOORBELL_PREAMBLE : LINK_PREAMBLE);
}

enum Ring { RING_NONE, RING_MINE, RING_EVERYONE };

// A doorbell for this device, or one for every label not set up while this
// one isn't.
static Ring doorbellFor(const uint8_t *buf, size_t len) {
    if (!linkValid(buf, len, LINK_DOORBELL, sizeof(LinkDoorbell))) return RING_NONE;
    const LinkDoorbell *b = (const LinkDoorbell *)buf;
    if (linkIsBroadcast(b->device)) return setUpCache ? RING_NONE : RING_EVERYONE;
    return linkSameDevice(b->device, myDevice) ? RING_MINE : RING_NONE;
}

static void answerRing(Ring ring) {
    if (ring == RING_EVERYONE) {
        uint32_t wait = random(0, BROADCAST_SPREAD_MS);
        Serial.printf("Add labels: answering in %lu ms\n", (unsigned long)wait);
        delay(wait);
    } else {
        Serial.println("doorbell: checking in now");
        delay(20);   // let the gateway get back to listening first
    }
}

// After a radio wake-up, read the packet that woke us straight over SPI.
// RadioLib's begin() resets the chip, which would throw the packet away, and
// a doorbell for another label should cost no check-in.
static void sx1262(uint8_t *b, size_t n) {
    uint32_t t = millis();
    while (digitalRead(LINK_PIN_BUSY) == HIGH && millis() - t < 20) {}
    loraSPI.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(LINK_PIN_NSS, LOW);
    for (size_t i = 0; i < n; i++) b[i] = loraSPI.transfer(b[i]);
    digitalWrite(LINK_PIN_NSS, HIGH);
    loraSPI.endTransaction();
}

static Ring ringThatWokeUs() {
    pinMode(LINK_PIN_NSS, OUTPUT);
    digitalWrite(LINK_PIN_NSS, HIGH);
    pinMode(LINK_PIN_BUSY, INPUT);
    loraSPI.begin(LINK_PIN_SCK, LINK_PIN_MISO, LINK_PIN_MOSI, LINK_PIN_NSS);

    uint8_t irq[4] = {0x12, 0, 0, 0};   // GetIrqStatus
    sx1262(irq, sizeof irq);
    uint16_t flags = (irq[2] << 8) | irq[3];
    // RxDone, and neither a header error (bit 5) nor a CRC error (bit 6)
    if (!(flags & 0x0002) || (flags & 0x0060)) return RING_NONE;

    uint8_t st[4] = {0x13, 0, 0, 0};    // GetRxBufferStatus: length, start
    sx1262(st, sizeof st);
    if (st[2] != sizeof(LinkDoorbell)) return RING_NONE;

    uint8_t rd[3 + sizeof(LinkDoorbell)] = {0x1E, st[3], 0};   // ReadBuffer
    sx1262(rd, sizeof rd);
    return doorbellFor(rd + 3, sizeof(LinkDoorbell));
}

// Undo what deepSleep() held, so the display, LED and battery divider can be
// driven again.
static void releaseSleepHolds() {
    gpio_deep_sleep_hold_dis();
    for (int pin : {LINK_PIN_NSS, PIN_PCB_VEXT, PIN_LED, PIN_BAT_ADC_CTRL})
        gpio_hold_dis((gpio_num_t)pin);
}

#if DEEP_SLEEP
// Sleep until the next check-in is due, the button is pressed, or (if the
// radio is up) a doorbell arrives. Does not return: waking runs setup().
static void deepSleep() {
    int64_t waitUs = nextCheckinUs - nowUs();
    if (waitUs < 1000) waitUs = 1000;

    bool listening = false;
    if (radioReady) {
        doorbellMode(true);
        // About 15 ms of listening every ~250 ms, sized so a doorbell's
        // preamble always overlaps one. DIO1 rises only on a received packet.
        listening = radio.startReceiveDutyCycleAuto(LINK_DOORBELL_PREAMBLE, 0) == RADIOLIB_ERR_NONE;
        if (!listening) radio.sleep();
    }
    // Only with a USB host attached: on battery, flushing to nobody would
    // keep the label awake for nothing on every sleep.
    if (Serial) {
        Serial.printf("deep sleep %lld s%s\n", (long long)(waitUs / 1000000),
                      listening ? ", listening for the doorbell" : "");
        Serial.flush();
    }

    // Hold every output where it is: radio deselected, LED and battery
    // divider off, and the display unpowered unless its switch also feeds the
    // radio. A floating pin here costs battery all night.
    digitalWrite(LINK_PIN_NSS, HIGH);
    if (VEXT_POWERS_RADIO && listening) Platform::VExtOn();
    else Platform::VExtOff();
    digitalWrite(PIN_LED, LOW);
    pinMode(PIN_BAT_ADC_CTRL, OUTPUT);
    digitalWrite(PIN_BAT_ADC_CTRL, !BAT_ADC_ON);
    for (int pin : {LINK_PIN_NSS, PIN_PCB_VEXT, PIN_LED, PIN_BAT_ADC_CTRL})
        gpio_hold_en((gpio_num_t)pin);
    gpio_deep_sleep_hold_en();

    esp_sleep_enable_timer_wakeup((uint64_t)waitUs);
    if (listening) esp_sleep_enable_ext0_wakeup((gpio_num_t)LINK_PIN_DIO1, 1);
    rtc_gpio_pullup_en((gpio_num_t)PIN_BUTTON);
    rtc_gpio_pulldown_dis((gpio_num_t)PIN_BUTTON);
    esp_sleep_enable_ext1_wakeup(1ULL << PIN_BUTTON, ESP_EXT1_WAKEUP_ANY_LOW);
    esp_deep_sleep_start();
}
#endif

static void sleepUntilNext() {
#if DEEP_SLEEP
    deepSleep();
#else
    uint32_t ms = (uint32_t)((nextCheckinUs - nowUs()) / 1000);
    Serial.printf("next check-in in %lu s, or sooner if the gateway rings\n", (unsigned long)(ms / 1000));
    // Listen for a doorbell until the next check-in is due.
    bool listening = false;
    if (radioReady) {
        doorbellMode(true);
        listening = radio.startReceive() == RADIOLIB_ERR_NONE;
    }
    radioIrq = false;
    Ring ring = RING_NONE;
    while (nowUs() < nextCheckinUs) {
        if (digitalRead(PIN_BUTTON) == LOW) {
            delay(30);   // debounce
            while (digitalRead(PIN_BUTTON) == LOW) delay(10);
            Serial.printf("button %s: checking in now\n", BUTTON_NAME);
            buttonPressed = true;
            break;
        }
        if (listening && radioIrq) {
            radioIrq = false;
            uint8_t buf[64];
            size_t len = radio.getPacketLength();
            if (len > sizeof buf) len = sizeof buf;
            if (radio.readData(buf, len) == RADIOLIB_ERR_NONE &&
                    (ring = doorbellFor(buf, len)) != RING_NONE) break;
            radio.startReceive();   // someone else's packet: keep listening
        }
        delay(5);
    }
    if (radioReady) {
        radio.standby();
        doorbellMode(false);
    }
    if (ring != RING_NONE) answerRing(ring);
#endif
}

// ---- main ----

void setup() {
    esp_sleep_wakeup_cause_t why = esp_sleep_get_wakeup_cause();
    releaseSleepHolds();
    randomSeed(esp_random());
    esp_efuse_mac_get_default(myDevice);
    linkDeviceHex(myDeviceHex, myDevice);

#if DEEP_SLEEP
    // Woken by the radio. If it was a doorbell for another label, go straight
    // back to sleep: no check-in, no display, a few hundred ms awake in all.
    Ring ring = RING_NONE;
    if (why == ESP_SLEEP_WAKEUP_EXT0) {
        ring = ringThatWokeUs();
        if (ring == RING_NONE) {
            radioBegin();
            deepSleep();
        }
    }
#endif

    Serial.begin(115200);
#if !DEEP_SLEEP
    delay(500);   // let the USB serial console attach
#endif
    pinMode(PIN_LED, OUTPUT);
    digitalWrite(PIN_LED, LOW);
    pinMode(PIN_BUTTON, INPUT_PULLUP);
    if (why == ESP_SLEEP_WAKEUP_EXT1) buttonPressed = true;

    prefs.begin("shelf", false);
    loadState();
    Serial.printf("\n%s shelf label, device %s, %s: %s\n", BOARD_NAME, myDeviceHex,
                  *assignment.labelId ? assignment.labelId : "not set up",
                  why == ESP_SLEEP_WAKEUP_TIMER ? "timer wake"
                  : why == ESP_SLEEP_WAKEUP_EXT0 ? "doorbell wake"
                  : why == ESP_SLEEP_WAKEUP_EXT1 ? "button wake"
                  : "power on");

    choosePanel();
    ensureDrawn();

#if DEEP_SLEEP
    if (ring != RING_NONE) answerRing(ring);
#endif
    cycle();
    scheduleNextCheckin();
}

void loop() {
    sleepUntilNext();
    cycle();
    scheduleNextCheckin();
}
