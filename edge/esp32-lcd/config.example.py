# Copy this file to `config.py` on the ESP32 and fill in your own values.
# `config.py` is gitignored: never commit real Wi-Fi credentials.

WIFI_SSID = "your-wifi-name"          # must be a 2.4 GHz network
WIFI_PASSWORD = "your-wifi-password"

# The IP address of the computer that runs `make demo` (Windows: run `ipconfig`
# and read "IPv4 Address" under your Wi-Fi adapter). Not "localhost".
MQTT_BROKER = "192.168.1.10"
MQTT_PORT = 1883

# The contract topic: retail/price/{product_id}. "+" subscribes to every SKU.
# When testing on a public broker, add a private-looking prefix so strangers
# cannot send prices to your board, e.g. "hb295a-test/retail/price/+".
TOPIC = "retail/price/+"

# One entry per LCD on this board. The demo data has an LCD label on SKU-001
# (milk, label SHLF-0142) and on SKU-003 (eggs, label SHLF-0121).
#
# Wiring B (default, no soldering): LCD 1 on GPIO 21 (SDA) and GPIO 22 (SCL),
# LCD 2 on its own two wires, GPIO 16 (SDA) and GPIO 17 (SCL). On a 38-pin
# ESP32 these are the pins marked RX2 and TX2. Both backpacks keep address 0x27.
#
# "name" is optional. With it, the LCD shows the product name on line 1 and the
# price on line 2 (like a shelf label). Without it, the LCD shows the SKU and the
# price, then the reason tag. The name is cut at 16 characters.
DISPLAYS = [
    {"sku": "SKU-001", "sda": 21, "scl": 22, "name": "MILK ORGANIC 1L"},
    {"sku": "SKU-003", "sda": 16, "scl": 17, "name": "EGGS FREERANGE"},
]

# Wiring A: both LCDs on the same two wires (GPIO 21 and 22). They need different
# I2C addresses: close the solder pad marked A0 on the second backpack (0x27
# becomes 0x26). Backpacks with a PCF8574A chip start at 0x3F, so use 0x3F and 0x3E.
# DISPLAYS = [
#     {"sku": "SKU-001", "addr": 0x27, "name": "MILK ORGANIC 1L"},
#     {"sku": "SKU-003", "addr": 0x26, "name": "EGGS FREERANGE"},
# ]

# Optional: also show the reason tag after the price on line 2, e.g. "$3.49 Manager se".
# SHOW_REASON = True

# Optional: let the product and name of each LCD be changed over MQTT (topic
# retail/config/display_map) without re-flashing. The entries above are used until
# the first map arrives. The pins above never change this way.
# To match entries from the console by label id, add the label id of each LCD to its
# entry above, e.g. {"sku": "SKU-001", "sda": 21, "scl": 22, "label": "SHLF-0142"}.
# DYNAMIC_MAP = True
