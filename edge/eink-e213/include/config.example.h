// Copy this file to `config.h` (same folder). `config.h` is gitignored so you
// can change these locally, but nothing here is per label any more: every
// label runs the same build and gets its label ID, product and name from the
// console (Shelf labels > Add labels).
#pragma once

// 1: deep sleep between check-ins, with the radio listening for doorbells
// ("wake on radio"). New prices still arrive in about 3 s. Use this on
// battery. The USB serial console drops each time the board sleeps.
// 0: stay awake. Same speed, far more power, but the serial console stays
// up. Use this while setting up or debugging.
#define DEEP_SLEEP     1

// The regular check-in, which reports battery and signal and catches any
// doorbell the label missed. Price changes don't wait for it. Each one costs
// about a second awake, so longer is better for the battery. Keep it well under
// the console's "offline" window (Settings > Shelf labels, 75 minutes by
// default) and the gateway's LISTENING_TRUST_MS (75 minutes).
#define WAKE_SECS      1800

// Each check-in lands up to this far either side of WAKE_SECS, so labels
// switched on together drift apart instead of all reporting at once.
#define WAKE_JITTER_SECS 60

// Radio output. 14 dBm reaches across a store; the SX1262 allows up to 22.
#define LORA_POWER_DBM 14

// Which e-ink panel. 0 detects it at boot, which is right for every board so
// far. 1 forces the older panel (LCMEN2R13EFC1: E213 without a version,
// Wireless Paper V1.1); 2 forces the newer one (E0213A367: E213 V1.1 and
// later, Wireless Paper V1.1.1 and V1.2). Only needed if detection picks
// wrong: the screen stays blank or shows noise.
#define PANEL          0
