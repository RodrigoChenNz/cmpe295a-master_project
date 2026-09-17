# gateway — Layer 2

**Owner:** Het Bhalala · **Status:** not started · **Design not settled**

The MSP432 has no on-die Wi-Fi (the ESP32 in the original 295A design did). Something must
carry MQTT to the board. Three options, pick one this week:

| Option | Trade-off |
|---|---|
| CC3120 BoosterPack | Native TI stack, MQTT on the board itself, no gateway process. Most work, best story. |
| ESP8266 as serial modem | Cheap, fast to build. Two MCUs to explain in the thesis. |
| Raspberry Pi / laptop bridge | Trivial (Python, 50 lines). Weakest "edge computing" claim for the paper. |

Whichever wins, the board must keep displaying the last price when the link drops — that
resilience demo is a stated success criterion.
