# Multi-Agent System for Dynamic Retail Pricing with Edge-IoT Integration

SJSU CMPE 295A/295B master's project. Advisor: Prof. Vidyacharan Bhaskar.

Autonomous agents set retail prices in real time. Every price decision carries a SHAP-based
explanation, so the system can always answer *why* a price moved — on the manager's dashboard
and on the LCD at the shelf edge.

**Team:** Het Bhalala · Rodrigo Chen · Parth Gala · Aruna Samhitha Mutyala

## Architecture

```
L5  Manager Dashboard    React + FastAPI + Postgres        Rodrigo
L4  AI Backend           RL pricing agent · SHAP · forecaster   Parth, Aruna
L3  Message Bus          Mosquitto (MQTT v5)               shared
L2  Edge Gateway         MSP432 LaunchPad + Wi-Fi link     Het
L1  Physical Display     16x2 character LCD                Het
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
cp .env.example .env
make up          # mosquitto + postgres
make tap         # watch the MQTT bus in another terminal
make help        # everything else
```

**One command, no manual setup step.** If bringing the system up ever needs a human to explain
something, fix the compose file rather than writing it down.

## Layout

```
contracts/     ★ MQTT payload schemas — changing these needs all four approvals
infra/           docker-compose, mosquitto config, postgres schema
services/
  dashboard-api/   FastAPI · MQTT→WS bridge · REST history      Rodrigo
  dashboard-web/   React · Vite · TypeScript                    Rodrigo
  pricing-agent/   stub rules → DDPG/DQN + SHAP                  Parth
  forecaster/      stub → Prophet/LSTM + churn risk              Aruna
  simulator/       fake store; becomes the gym environment       Parth
edge/
  msp432/          firmware · 16x2 LCD driver                    Het
  gateway/         MQTT ↔ serial link (design not settled)       Het
tools/           fake publisher, MQTT tap, validators, smoke test
notebooks/       EDA, training runs, model comparison
docs/            workbook, meeting notes, agent prompts
```

## How we work

**Walking skeleton first.** The 295A attempt built one layer deep and the layers never
exchanged a message. This time: the thinnest end-to-end path through all five layers, every
component a deliberate stub, passing in week 1. Then thicken each layer independently behind
the frozen contract.

Week 1 target — `make smoke` green:

```
simulator → stub agent → retail/price/SKU-001 ─┬→ dashboard-api → WebSocket → browser
                                               └→ gateway → MSP432 → LCD
```

No RL, no SHAP, no Prophet, no real hardware. Those swap in afterwards, one at a time, without
touching anyone else's code.

**Branches:** `feat/<service>-<topic>`, e.g. `feat/web-mqtt-bridge`. Small PRs, one service each,
reviewed by one teammate. A PR touching `contracts/` needs all four.

**Weekly integration:** everyone merged, `make up`, `make smoke`. If it's red, that's the agenda.

**Meeting notes** go in [`docs/meetings/`](docs/meetings/) — they're workbook material, so don't
let them lapse.

## Open decisions

| Question | Owner | Why it blocks |
|---|---|---|
| MSP432P401R or MSP430? "MSP32" is not a TI part number | Het | Determines the whole Layer 2 toolchain |
| How MQTT reaches the board — CC3120, ESP8266 modem, or Pi bridge | Het | Neither MSP part has on-die Wi-Fi; see [`edge/gateway/`](edge/gateway/) |
| Does `retail/guardrail/{id}` exist? | Parth | The agent has to clamp to it; schema is drafted but unapproved |
| Hardware order placed? | Het | Lead time; simulation only carries so far |

## Docs

- [Workbook (295A)](docs/workbook_assignment_1.md) — literature review, architecture, schedule
- [Meeting notes](docs/meetings/)
- [Agent prompts](docs/prompts/) — self-contained briefs for building each layer
