## Complete software tools

`make software` runs the integrated workers. `make integration` runs isolated PostgreSQL/MQTT tests. `make smoke` signs in using the private `.env` account, publishes a correlated recommendation and explanation, approves it, and waits for its exact emulator render. `make validate` validates all schemas and examples. See [software guide](../docs/SOFTWARE.md) for prerequisites and TLS setup.

# tools

Developer scripts. Nothing here ships, and no service imports from here.

---

## Running the system

Everything goes through the Makefile at the repo root. You need Docker Desktop
running; nothing else has to be installed on the host.

```bash
make up      # postgres, mosquitto, dashboard-api, dashboard-web — empty database, nothing fake
make demo    # the same, plus the sample catalogue and the fake publisher
```

Then open **<http://localhost:5173>**.

- **`make up`, first run:** the console shows a setup screen. The account you
  create there is the **Super admin**. It invites everyone else from the Users
  screen; each invite is a `/join?token=…` link that works once and expires in
  a week.
- **`make demo`:** sign in with the account named in `.env`. `make up` creates
  `.env` from `.env.example` if it does not exist yet, and the default is
  `admin@admin.com` with password `admin`. That account is the
  demo's Super admin.

Data persists across `make down` / `make up` / `make restart`. To start over:

```bash
make fresh       # wipe the real database only → first-run setup again
make fresh-demo  # wipe the demo database only → re-applies the seed
```

**`make up` contains no sample or emulated data.** Until Layers 1–4 exist,
nothing publishes proposals on it, so the console stays empty. That is
expected.

**`make demo`** (or `make up DEMO=1`) layers `infra/docker-compose.demo.yml` on
top: Postgres applies `03-seed.sql` to a separate `postgres-demo-data` volume,
and `fake_publisher.py` runs as the `fake-publisher` container. Switching modes
needs no `make nuke`, because the two databases never share a volume.
`make fake` follows the publisher's output.

| Port | Service |
|---|---|
| 5173 | the console |
| 8000 | dashboard-api — browsable at `/docs`, liveness at `/health` |
| 5432 | postgres |
| 1883 | mosquitto (MQTT), 9101 for MQTT-over-WebSocket |

### The rest of the Makefile

```bash
make help     # list every target
make logs     # follow all containers
make ps       # container status
make tap      # print every message on the retail/ topic tree
make psql     # psql shell on the dev database
make down     # stop, keep the data
make fresh   # DELETE the real database only and start again (fresh-demo: the demo one)
make nuke     # stop and DELETE the volumes — wipes both the real and the demo database
make seed     # regenerate 03-seed.sql (see gen_seed.py below)
make validate # check contracts/*.schema.json are well-formed JSON
```

`make fresh-demo` re-seeds the demo from scratch. Because the seed's timestamps are
relative to `now()`, that always gives you a fresh store with a full queue
rather than one that expired on the day the seed file was written.

### Running a service outside Docker

Occasionally useful for a debugger. The console:

```bash
cd services/dashboard-web && npm install && npm run dev
```

The API needs Python 3.11, which `uv` supplies. `uv run` reads the dependencies
out of `pyproject.toml` itself, so there is nothing to install first:

```bash
cd services/dashboard-api
uv run uvicorn app.main:app --reload
```

Both read their configuration from the root `.env`. Point `DATABASE_URL` and
`MQTT_HOST` at `localhost` rather than the compose service names.

---

## The scripts

| Script | What it does | How to run it |
|---|---|---|
| `fake_publisher.py` | Stands in for Layers 1–4: publishes proposals, SHAP explanations and inventory risk on the real contract topics, and reports label sightings. | runs under `make demo` |
| `gen_seed.py` | Generates `infra/postgres/03-seed.sql` from the design prototype's sample catalogue. | `make seed` |

### `fake_publisher.py`

Publishes on the real topics with the real payload shapes, so when Parth's agent
and Aruna's forecaster come online this tool is deleted and nothing in
`dashboard-api` changes:

```
retail/proposal/{product_id}    a price recommendation
retail/explain/{product_id}     its SHAP vector, a beat later
retail/inventory/{product_id}   dead-stock risk
```

It deliberately produces a mix: most moves small enough to be auto-approved, some
large enough to need a human, a few outside the product's band, and about one in
eight proposals with **no** explanation at all — because SHAP failing is a normal
state the console has to handle, not an error.

**One thing in it is not a contract message.** It writes `shelf_labels.last_seen_at`
straight to Postgres, because there is no topic in `contracts/` on which a label
says it is alive. That gap is worth closing properly — see the module docstring
and the open-decisions table in the root README.

It is also demo insurance: if the RL agent is mid-retrain or the Wi-Fi module is
flaking during a presentation, this drives a fully live dashboard.

`make demo` runs it inside the `dashboard-api` image, which already has `aiomqtt`
and `asyncpg`, so it works on a machine with no local Python environment. It
refuses to start against a database without the seed's `sample_data_marker`
table, so it cannot write fake rows into the real one. To run it directly
against the demo stack instead:

```bash
uv run --with aiomqtt --with asyncpg tools/fake_publisher.py
```

### `gen_seed.py`

Transcribes the catalogue, labels, suggestions and decisions from
`design_handoff_shelfiq/ShelfIQ Instrument.dc.html` into SQL, and synthesises
ninety days of price history around each product's real current price.

Every timestamp it emits is an expression **relative to `now()`**, not an
absolute value. That is the whole point: an absolute `expires_at` means the
review queue is empty on every day after the seed was generated, because the
housekeeping timer lapses all twelve proposals within thirty seconds of the
first `make up`.

Edit `gen_seed.py`, never `03-seed.sql` — the generated file says so in its
header. The seed only runs on an empty volume, so:

```bash
make seed       # regenerate the SQL
make fresh-demo # apply it
```

---

## Not written yet

`make smoke` points at `tools/smoke.py`, which **does not exist** — the target
fails. The week-1 walking-skeleton check described in the root README still
needs writing: publish a proposal, assert it reaches the gate, approve it, assert
the price lands in `price_history` and on `retail/price/`.

Everything it needs is in place (`fake_publisher.py` shows how to publish, and
the API's behaviour is verifiable over REST), so it is a short script rather than
a project. Worth doing before the next integration meeting, since "if it's red,
that's the agenda" only works if there is something to run.
