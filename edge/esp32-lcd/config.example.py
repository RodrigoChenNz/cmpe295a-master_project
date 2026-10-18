# Copy this file to `config.py` on the ESP32 and fill in your own values.
# `config.py` is gitignored: never commit real Wi-Fi credentials.

WIFI_SSID = "your-wifi-name"          # must be a 2.4 GHz network
WIFI_PASSWORD = "your-wifi-password"

MQTT_BROKER = "192.168.1.10"          # the team broker's LAN IP (make up), or a test broker
MQTT_PORT = 1883

# The contract topic: retail/price/{product_id}. "+" subscribes to every SKU.
# When testing on a public broker, add a private-looking prefix so strangers
# cannot send prices to your board, e.g. "hb295a-test/retail/price/+".
TOPIC = "retail/price/+"
