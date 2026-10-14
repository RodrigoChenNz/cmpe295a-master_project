# Commands

Run every command from the repo root. The only thing you need installed is Docker Desktop. `make help` prints this list in short.

## Running the system

| Command | What it does |
|---|---|
| `make up` | Starts the real system: mosquitto, postgres, dashboard-api and dashboard-web. It uses the real database and has no sample or emulated data. |
| `make demo` | Starts the same system with sample data and the fake publisher standing in for Layers 1–4. It uses its own separate database. `make up DEMO=1` does the same thing. |
| `make down` | Stops everything and keeps your data. |
| `make restart` | Runs `down` then `up`. Use `make restart DEMO=1` for the demo. |

Then open **http://localhost:5173**.

- **Real system, first run:** the console asks you to create the first account. That account is the **Super admin**.
- **Demo:** sign in as **admin@admin.com** / **admin**. That account is the demo's Super admin. To change it, set `SHELFIQ_BOOTSTRAP_EMAIL` / `SHELFIQ_BOOTSTRAP_PASSWORD` in `.env`.

## Starting over

Your data persists across `down`, `up` and `restart`. Only these commands delete it:

| Command | Deletes | Then |
|---|---|---|
| `make fresh` | The real database only | Starts the system again, back at first-run setup |
| `make fresh-demo` | The demo database only | Starts the demo again with the original sample data |
| `make nuke` | Everything: both databases and the broker's data | Leaves the system stopped |

### If the setup or sign-in screen never loads

Run `make ps`. If `rp-api` shows **Restarting**, run `make logs`. A message saying the database predates the schema means the database volume was created by older code; the schema is only applied when a volume is first created. Run `make fresh` (or `make fresh-demo` if you were running the demo). This deletes that database.

## Looking inside

| Command | What it does |
|---|---|
| `make logs` | Follows the logs from every container |
| `make ps` | Shows container status |
| `make psql` | Opens a SQL shell on whichever database is running |
| `make tap` | Prints every MQTT message on the `retail/` topics |
| `make fake` | Follows the fake publisher's output (demo only) |

## Development

| Command | What it does |
|---|---|
| `make env` | Creates `.env` from `.env.example` if it doesn't exist. `make up` does this for you. |
| `make seed` | Regenerates `infra/postgres/03-seed.sql` from `tools/gen_seed.py`. Run `make fresh-demo` afterwards to apply it. |
| `make validate` | Checks that `contracts/*.schema.json` are well-formed JSON |
| `make smoke` | End-to-end check. **This does not work yet:** `tools/smoke.py` hasn't been written. |

## Ports

| Port | Service |
|---|---|
| 5173 | The console (web) |
| 8000 | dashboard-api. API docs are at `/docs` and the health check is at `/health`. |
| 5432 | postgres |
| 1883 | mosquitto (MQTT). MQTT-over-WebSocket is on 9101. |

## Setting up a new store (in the console)

Do these in order. The Dashboard shows a **Getting started** checklist until every step is done.

1. **Create the Super admin.** This is the first screen on an empty database.
2. **Name the layout's levels** (Locations). The default is **Zone › Aisle › Bay**. Rename them, or add a level, e.g. **Floor** on top for a store with several floors (1–6 levels).
   - **Renaming:** allowed any time.
   - **Adding or removing a level:** only while there are no locations.
   - **Who:** a manager or Super admin.
3. **Add locations** (Locations). Build the tree from the top level down. Shelf positions, where a label hangs, go on the lowest level.
4. **Add products** (Products). This isn't possible until step 2 is done. A product's location is optional when you add it.
5. **Link products to locations.** Any of these works:
   - Locations → select a spot → **Add products here**.
   - Products → select several → **Location**.
   - A product's page → **Location: Link / Change**.

   **Products → Location → Not linked yet** lists the products still waiting.

## Users and invites (in the console)

- The **first account** created on an empty database is the Super admin.
- **To add someone:** go to Users → Invite user and send them the link. They open `/join?token=…`, choose their own password, and are signed in. A link works once and expires after 7 days.
- **If a link is lost:** on that person's row, click **New invite link**. This cancels the old link.
- **Who can manage whom:**
  - A Super admin manages everyone.
  - A manager can invite and manage staff and read-only users only.
  - The last Super admin can't be demoted or deactivated.
