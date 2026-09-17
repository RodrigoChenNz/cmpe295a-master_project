# tools

Developer scripts. Nothing here ships, and no service imports from here.

| Script | Purpose |
|---|---|
| `fake_publisher.py` | Emit contract-valid traffic for ~8 SKUs. Lets Layer 5 be built and demoed before Layer 4 exists. |
| `mqtt_tap.py` | Subscribe `retail/#` and pretty-print. First thing to reach for when integration breaks. |
| `validate.py` | Check a payload against `contracts/*.schema.json`. |
| `seed_db.py` | Load the product catalog into Postgres. |
| `smoke.py` | End-to-end: publish a price, assert it lands in the DB and on the socket. |

`fake_publisher.py` is also demo insurance — if the RL agent is mid-retrain or the Wi-Fi
module is flaking during a presentation, it drives a fully live dashboard.
