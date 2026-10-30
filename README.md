# Multi-Agent System for Dynamic Retail Pricing with Edge-IoT Integration

SJSU CMPE 295A/295B master's project. Advisor: Prof. Vidyacharan Bhaskar.

An RL agent **recommends** retail prices in real time, each recommendation carrying a
SHAP-based explanation. A store manager approves, overrides or rejects it — or delegates the
small ones to a standing policy. The approved price lands in two places: the manager's
dashboard, and the 16x2 LCD at the shelf edge.

**The agent never sets a price.** Nothing reaches a shelf that a human did not approve or
explicitly delegate, and every one of those decisions is logged with a name against it. That
is what makes the explanation load-bearing rather than decorative: the manager reads it
*before* deciding, not after the price has already moved.

**Team:** Het Bhalala · Rodrigo Chen · Parth Gala · Aruna Samhitha Mutyala

## Complete software implementation

Run `make software` for the simulator → forecast → pricing and exact Shapley explanation → approval → durable MQTT delivery → confirmed display emulator loop. Setup, training, evaluation, TLS, recovery, and limitations are documented in [the software guide](docs/SOFTWARE.md).

The implementation lives in `retail/`; the existing dashboard and API integrate it through shared schemas. DQN and DDPG train against a seeded Gymnasium environment. Forecast experiments compare mean, seasonal, Prophet and LSTM on chronological held-out data. The default live policy and forecast are explicitly rule-based and seasonal; trained checkpoints can be selected through configuration.

Software tests use a separate PostgreSQL database and real Mosquitto broker. Physical firmware and real-store validation remain separate work. Synthetic churn risk is a proxy, not a model fitted to customer records.

## Architecture

```
L5  Manager Dashboard    React + FastAPI + Postgres        Rodrigo
L4  AI Backend           RL pricing agent · SHAP · forecaster   Parth, Aruna
L3  Message Bus          Mosquitto (MQTT v5)               shared
L2  Edge Gateway         MSP432 LaunchPad · ESP32          Het
L1  Physical Display     16x2 LCD · Heltec E213 e-ink      Het
```

The layers never call each other's functions — they exchange JSON over MQTT. Those payload
shapes live in [`contracts/`](contracts/) and are the real interface between the four of us.
**Read that directory before writing any code.**

## Quick start

Host prerequisites: Docker Desktop, Node 20+, and `uv` (which supplies Python 3.11 itself):

```bash
brew install uv && uv python install 3.11
```

Then, from a clean clone:

```bash
make up          # the real system, on an empty database — nothing fake
make demo        # sample catalogue + fake publisher standing in for Layers 1-4
make fresh       # wipe the real database and start over from first-run setup
make help        # everything else
```

Then open <http://localhost:5173>.

**`make up` has no sample or emulated data.** On the very first run the
database is empty and the console asks you to **create the first account**.
That account is the **Super admin**: it can do everything a manager can, and it
is the only role that can grant or remove the manager role. Everyone else joins
through an invite link from the Users screen.

**Then set up the store, in this order:**
1. Name the layout's levels on Locations (Zone › Aisle › Bay by default, or add Floor on top).
2. Add locations.
3. Add products. This isn't possible until step 1 is done.
4. Link the products to locations.

The Dashboard keeps a checklist until every step is done. [COMMANDS.md](COMMANDS.md) has the details.

**What you add is kept.** `make down`, `make up` and `make restart` never touch
the database volume. Only `make fresh` (just the real database) and `make nuke`
(everything) delete data.

**`make demo`** (same as `make up DEMO=1`) loads the design prototype's sample
catalogue and runs `tools/fake_publisher.py` as a container. The publisher sends
proposals and explanations on the real contract topics and reports label
sightings, which keeps the console *live*. The demo database has its own volume,
so you can switch between the two modes without `make nuke`, and sample rows
never get into the real database. `make fake` follows the publisher's output.
Sign in to the demo with the account named in `.env` (`make up` creates `.env`
from `.env.example`). `make fresh-demo` resets the demo to its seed.

`make tap` watches the MQTT bus; `make psql` opens a shell on the database.

**One command, no manual setup step.** If bringing the system up ever needs a human to explain
something, fix the compose file rather than writing it down.

## Layout

```
contracts/     ★ MQTT payload schemas — changing these needs all four approvals
infra/           docker-compose, mosquitto config, postgres schema
services/
  dashboard-api/ ✔ FastAPI · the decision gate · MQTT↔WS         Rodrigo
  dashboard-web/ ✔ React 19 · Vite · TypeScript · the console    Rodrigo
  pricing-agent/   DQN / DDPG + exact price Shapley                  Parth
  forecaster/      seasonal runtime; Prophet/LSTM evaluation              Aruna
  simulator/       seeded store + Gymnasium environment       Parth
edge/
  msp432/          firmware · 16x2 LCD driver                    Het
  gateway/         MQTT ↔ serial link (design not settled)       Het
                   note: the e-ink label is a Heltec Vision Master
                   E213 on ESP32, a different part and toolchain
tools/           fake publisher, MQTT tap, validators, smoke test
notebooks/       EDA, training runs, model comparison
docs/            workbook, meeting notes, agent prompts
design_handoff_shelfiq/  ★ the console's visual source of truth
```

The console includes review and explanation detail, catalogue, activity, labels and pairing, pricing rules, settings, users, locations, and software status. Approved prices and their durable delivery records commit together. A label becomes confirmed only after a matching render acknowledgement.

## How we work

Use `make integration` to exercise the full software path with isolated infrastructure, and `make smoke` against a running software stack. Keep contract changes under group review before merging.

**Branches and PRs:**
- Work branches are named `<type>/<initials>/<short-name>`, e.g. `feature/rc/layout-levels`.
  They are cut from `dev` and merged back into it through small PRs, reviewed by one teammate.
- Each week `dev` is promoted to `stage` for integration testing, and a verified `stage` is
  promoted to `main`, which is what we demo and submit.
- A PR touching `contracts/` needs all four of us.

The full workflow is in [CONTRIBUTING.md](CONTRIBUTING.md): naming, commits, reviews,
promotions and hotfixes.

**Weekly integration:** everyone merged, `make up`, `make smoke`. If it's red, that's the agenda.

**Meeting notes** go in [`docs/meetings/`](docs/meetings/) — they're workbook material, so don't
let them lapse.

## Open decisions

| Question | Owner | Why it blocks |
|---|---|---|
| **Sign off the approval gate** — the agent now proposes rather than prices | all four | Breaking change across `proposal`, `price` and `guardrail`. See [`contracts/README.md`](contracts/README.md#status). Firmware is unaffected. |
| **Does approval gating weaken the "agentic" claim in Ch.2?** | all four + advisor | It strengthens the XAI contribution and matches the cited 54%→89% acceptance figure, but it is Chapter 2 surgery. Raise it before building. |
| MSP432P401R or MSP430? "MSP32" is not a TI part number | Het | Determines the LCD node's toolchain |
| **Two label families now, not one** — 16x2 LCD and a Heltec Vision Master E213 (ESP32) e-ink | Het | Two toolchains and two Wi-Fi stories. The ESP32 has on-die Wi-Fi; neither MSP part does. The console already models both (`shelf_labels.kind`). |
| Review optional proposal fields | all four | Software branch adds schema support for confidence and rationale. |
| Review correlated shelf acknowledgement | all four + Het | Software branch implements and tests the contract with an explicit emulator; physical firmware must implement it. |
| How MQTT reaches the board — CC3120, ESP8266 modem, or Pi bridge | Het | Neither MSP part has on-die Wi-Fi; see [`edge/gateway/`](edge/gateway/) |
| Hardware order placed? | Het | Lead time; simulation only carries so far |

## Docs

- [Workbook (295A)](docs/workbook_assignment_1.md) — literature review, architecture, schedule
- [Meeting notes](docs/meetings/)
- [Agent prompts](docs/prompts/) — self-contained briefs for building each layer
