# ShelfIQ software implementation

All changes are on a development branch. No release branch is automatically
updated. Contract additions require the team's normal review before merging.

## Run the complete software loop

Requirements: Docker Compose v2, Python 3.11 or 3.12 for local research commands,
and Node 20+ for local frontend development.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,forecast]'
make env
# Edit .env: choose the local account email and password.
make software
```

If port 8000 is occupied, set `API_PORT=18000` in `.env` and use that port for API tools.

Open http://localhost:5173. Sign in using the bootstrap account in your local
`.env`. The software environment has a separate seeded database volume; it
never inserts synthetic catalogue records into the real-store volume.

The simulator produces synthetic observations, the forecaster publishes demand
and inventory risk, and the pricing service publishes a recommendation plus
its exact price explanation. The API either delegates it under policy or puts
it in the review queue. Approved decisions and delivery jobs commit atomically.
The delivery worker publishes an ordered price message. The display emulator
persists its last rendered contents and returns a correlated acknowledgement.

Use **Software status** to inspect observations, model versions, durable delivery
counts and emulator status. Use **Shelf labels → Register label** for a new
identifier, then pair it with a product on its detail page. A green label needs
a matching render report, not just a successful broker publish.

The default policy is explicitly `rule-v1`. It is a real deterministic policy
used for integration, not a trained RL model. Configure a trained checkpoint to
run DQN or DDPG. Default forecasts are a seasonal baseline; Prophet and LSTM
are available in the experiment command, not silently substituted at runtime.

`make software-down` stops the software stack and preserves its data.
`make software-logs` follows its logs. Switching between real, old fake demo,
and complete software modes requires stopping the current stack first. The
old fake publisher must not run alongside the complete software workers.

## Train and evaluate

```sh
make train
make forecast
```

Training exports `artifacts/dqn.zip` and `artifacts/ddpg.zip`, manifest JSON with
model hashes, seeds and configuration, and episode monitor CSVs. Evaluation
uses seeds 101–105, separate from the training seed 42, and identical exogenous
random streams for each policy. It exports per-run `pricing.csv` and
`summary.json`. Forecasting uses a chronological 106-day training / 14-day
hold-out split and exports RMSE and WAPE for mean, seasonal, Prophet and LSTM.
All generated data are synthetic and must be identified as such in the report.

To use a trained model in the live software stack, set these in `.env` and
restart `make software`:

```dotenv
PRICING_POLICY=dqn
PRICING_MODEL=/srv/artifacts/dqn.zip
```

The policy path must exist. A missing or incompatible checkpoint stops the
pricing worker rather than impersonating a learned policy with a fallback.
Set `PRICING_POLICY=ddpg` and its corresponding checkpoint for continuous actions.
The worker image contains SB3 and PyTorch. The research-only Prophet dependency
is installed by the `forecast` extra for local experiments.

The environment has six normalized features: inventory, competitor price,
current price, forecast demand, weekday and time bucket. DQN selects one of five
price deltas; DDPG selects a continuous delta in [-1, 1]. Bounds apply to both.
The reward is profit minus 0.3 times absolute price movement and 0.5 times a
synthetic churn proxy. Penalties activate after 50 warm-up episodes. The proxy
is not observed customer churn and must not be reported as measured retention.
Short training runs validate the pipeline; they do not establish convergence.

Exact single-reference interventional Shapley values explain the actual bounded
price function. Six features require 64 policy evaluations. The reference is
recorded with the explanation, contributions are in USD, and their sum plus
the base reconstructs the recommendation. There is no tree-model surrogate
silently explaining an unrelated churn score. Overrides keep the original
recommendation explanation separate from the manager's applied price.

## Verify

```sh
make validate
make test
make integration
make smoke
```

`make integration` starts dedicated test containers on localhost ports 15432
and 11883. Tests refuse to reset a database whose name does not end in `_test`.
They cover duplicate delivery, ordering, expiry, stale references, concurrent
approval, rollback, outbox retries, authorization and matching render contents.
Real MQTT integration exercises rule, trained DQN and trained DDPG workers.

`make smoke` checks the running application using the account from `.env`.
It submits an explanation before its proposal, approves through the signed-in
HTTP API if needed, and waits for the device's exact acknowledgement. It
prints elapsed SOFTWARE-loop latency and whether the receiver is emulated.
It requires at least one product with a current price and a paired label.
The simulator may supersede recommendations during a long manual review;
submit a fresh proposal rather than approving an old reference price.

## Delivery and recovery

`message_outbox` is durable PostgreSQL state. Approved prices are queued in the
same transaction as the decision, history and label push. Delivery workers
serialize dispatch with a database advisory lock. A crash after publication
but before marking sent can replay a message. Devices persist and deduplicate
the monotonic `publication_id`; old messages cannot roll a display back.
Only the API publishes approved prices. Rejection, expiry, supersession and
overrides have separate outcome messages for the pricing service.

Explanation-before-proposal ordering uses a durable inbox. Non-additive or
cross-product explanations are rejected. Incoming payloads are validated
against JSON Schemas, including UUIDs, topic identities, and decimal money.
Queued messages do not time out as failed label pushes until actually sent.
Heartbeats establish liveness; only matching render content confirms delivery.

Additive, checksummed migrations run automatically at API startup and preserve
existing volumes. Do not edit applied migrations. Add the next numbered file.
Take backups before upgrading a real store; the supplied development stack is
not a managed production deployment or a point-of-sale integration.

## Authenticated MQTT with TLS 1.3

Local defaults use a plaintext broker bound to loopback. The secure overlay
uses TLS 1.3, individual client certificates, and broker-enforced topic ACLs:

```sh
make secure-identities
make software-down
make software-secure
```

The CA and role certificates are generated locally under gitignored `secrets/`.
Generation refuses to overwrite existing identities. API certificates alone
can write `retail/price/+`; pricing certificates can only recommend. The web
console still uses local HTTP; remote hosting needs HTTPS, secure cookies,
protected database access, backups and an operational certificate lifecycle.
Do not expose the development database or frontend server to public networks.

For the smoke check against the secure stack, use `make smoke-secure` (the pricing certificate):

```sh
MQTT_PORT=8883 MQTT_TLS=true MQTT_CA_FILE=secrets/certs/ca.pem \
MQTT_CERT_FILE=secrets/certs/pricing.pem MQTT_KEY_FILE=secrets/certs/pricing.key make smoke
```

Optional security integration tests:

```sh
docker compose -f infra/docker-compose.tls-test.yml up -d
TLS_TEST_PORT=18884 .venv/bin/python -m pytest -q tests/test_tls.py
```

## Input from a real store

An authenticated editor can POST a `state.schema.json` object with `source=store`
to `/api/system/observations`. It is recorded and forwarded on the state topic in both development and TLS modes. The API certificate can forward authenticated observations; the pricing certificate cannot publish state or approved prices.
Use UUID observation identities, timezone-aware timestamps, and chronological
sales history. Run this instead of the simulator for that store. Mapping a real
POS feed and selecting/calibrating models require actual store data and business
approval; no real customers or physical displays are implied by the emulator.

## Contract additions

Existing topic families remain. New state, shelf and decision schemas describe
observations, acknowledgements and decision feedback. Approved price messages
include an optional publication sequence and display name for upgraded devices.
Legacy devices that omit acknowledgements cannot be counted as confirmed.
The proposals/explanations now explicitly allow the metadata the console uses.
Review these changes as a group before integration into shared branches.
