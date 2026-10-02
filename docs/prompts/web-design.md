# Agent Prompt — Manager Dashboard: the ShelfIQ console

> Owns `services/dashboard-web/`. This file is the authority on everything
> visual. Its sibling [`web-layer.md`](web-layer.md) owns the FastAPI service
> and the wire protocol.

> **This replaced an earlier brief.** That one specified a different design —
> phosphor amber on near-black, a character-cell grid, four routes. It was
> written before the ShelfIQ design existed and is superseded in full. If you
> find a reference to "Shelf-Edge sympathy", amber chrome ramps, or
> `/review` · `/` · `/audit` as the route map, it is from the old brief and does
> not apply.

---

## The design

**`design_handoff_shelfiq/`** is the source of truth: a set of high-fidelity
HTML prototypes plus a README that specifies every screen, token and state.
Colours, type, spacing, radii and interactions are **final** — recreate them,
do not reinterpret them.

The files in that bundle are design references, **not production code**.
`support.js` and `image-slot.js` are the prototype's own runtime and must not be
ported. Open `ShelfIQ Instrument.dc.html` in a browser to click through it; all
styling is inline, so inspecting an element gives exact values.

The implemented system is written down in
[`services/dashboard-web/DESIGN.md`](../../services/dashboard-web/DESIGN.md) —
tokens, the two typographic registers, component anatomy, and the rules that are
easy to break. **Read that before changing anything visual.** Where the handoff
and the implementation differ, DESIGN.md §7 lists each deviation with its
reason.

## What the system is

SJSU CMPE 295B: *Multi-Agent System for Dynamic Retail Pricing with Edge-IoT
Integration*. An RL agent **recommends** shelf prices, each recommendation
carrying a SHAP explanation. A store manager approves, overrides or rejects
them — or delegates the small ones to a standing policy they set.

```
L5  Manager Dashboard    React + FastAPI + Postgres      ← YOU
L4  AI Backend           RL pricing agent · SHAP · forecaster
L3  Message Bus          Mosquitto (MQTT v5)
L2  Edge Nodes           MSP432 LaunchPad · ESP32
L1  Physical Displays    16x2 character LCD · Heltec Vision Master E213 e-ink
```

**The agent never sets a price.** It proposes. Nothing reaches a shelf that a
human did not approve or explicitly delegate. That single fact is what this
interface exists to serve, and it is why the explanation is decision support
rather than post-hoc justification: the manager reads it **before** deciding.

The literature target: *manager acceptance of AI-recommended prices rose from
54% to 89% when SHAP explanations were surfaced on dashboards.* The approval
rate per `agent_version` is therefore an evaluation result, not just telemetry.

## Stack

React 19 · Vite · TypeScript · plain CSS (custom properties + CSS Modules).

No component library, no Tailwind, no icon package, no chart library — the few
icons are inline SVG in `components/ui.tsx`, and the three charts (sparkline,
price history, SHAP ladder) are hand-built SVG because they carry the design's
distinctiveness and need total control.

> The workbook specifies Chart.js and the 295A build actually ran Recharts.
> Neither is used. The charts here are a step function, a sparkline with gaps,
> and a diverging bar ladder; no library's defaults survive contact with this
> design, and three small components cost less than a dependency.

Talks only to `dashboard-api` over REST and one WebSocket. It never connects to
MQTT directly.

## Routes

| Route | Screen | State |
|---|---|---|
| `/` | Dashboard | built |
| `/suggestions` | Price suggestions | built |
| `/suggestions/:decisionId` | Suggestion detail | built |
| `/products` | Products | built |
| `/products/:productId` | Product detail | built |
| `/activity` | Activity log | built |
| `/labels`, `/labels/:id` | Shelf labels, Label detail | stub |
| `/rules` | Pricing rules | stub |
| `/settings` | Settings | stub |
| `/users` | Users and roles | stub |
| `/locations` | Locations | stub |

Each stub names the endpoints that already serve it. All five have a working API
half, so they are screen-only work. Sign-in is not a route: `App.tsx` gates on
`GET /api/auth/me`.

## What you must not make

A specific, recognisable style has become the default output of coding agents.
Every item below is banned outright.

- `rounded-2xl` cards with `shadow-lg` floating on a grey page
- indigo / violet / purple-to-pink gradients, anywhere, for anything
- glassmorphism, `backdrop-blur`, frosted translucent panels
- Inter or Poppins as the interface face
- emoji as icons
- a three-equal-column card grid as a page's primary structure
- a centred hero with a pill-shaped call to action
- `<h1>Dashboard</h1>` with a subtitle under every panel title
- the sparkle motif for anything "AI"
- gradient area fill fading to transparent under a line chart
- untouched chart-library defaults
- `toFixed()` on money, and three decimals of false precision
- a hardcoded pulsing green "Live" badge not wired to real socket state
- skeleton shimmer on refetch
- an icon beside every label from whichever set was installed first

**And the one that matters most here:** hardcoded fake metrics. The 295A build
shipped `$124,690`, `34.2%`, `68.4%` — invented numbers that made the whole
thing read as a mockup. Every number on screen must trace to a row in the
database or be explicitly labelled as sample. While the seed is present,
`/api/meta` reports `sample_data` and the shell shows a banner.

The deeper tell is that generated interfaces are **arbitrary** — nothing about
them could only belong to this project. The fix is not more decoration. It is
that every visual decision traces to a hardware constraint or a data field:
sixteen cells because the LCD is sixteen characters, two rows because it is two
lines, a diverging palette because SHAP has a sign.

## The rules that are load-bearing

These are not style preferences. Each one prevents a specific way this interface
could mislead the person using it.

1. **Money is a string, end to end.** `"3.49"`, never `3.49`. A JSON number is a
   double by the time a browser has parsed it, and this system's output lands on
   a price label.
2. **Null renders as an em dash** — never 0, never "N/A".
3. **"Approved" and "on the shelf" are different claims**, and every message
   distinguishes them. `published: false` means recorded and queued; say that.
4. **The shelf preview says "as published"**, never "what the shelf is showing".
   There is no acknowledgement topic in `contracts/`, so the console cannot know
   what the glass shows.
5. **The explanation explains the proposal, not the override.** Say so before
   the manager clicks, not after.
6. **A proposal with no explanation is a normal state**, not an error — SHAP can
   fail or lag. Surface it as what it is: you can still decide, but you would be
   deciding without the agent's reasoning.
7. **`approved_by` comes from the session and nowhere else.** No header, query
   parameter or body field anywhere in the API can set it. The project's central
   claim is that every price change has a name against it.
8. **The 16-character cap is hardware**, enforced in the schema, in the API, and
   visibly in the UI with a counter. Past sixteen the firmware drops the rest —
   a longer name does not wrap, it disappears.

## Running it

```bash
make up      # postgres, mosquitto, dashboard-api, dashboard-web
make fake    # stand in for Layers 1-4 so the console has a live system
```

Then <http://localhost:5173>, signing in as the account named in `.env`.

Without `make fake` nothing publishes proposals and nothing reports label
sightings, so the queue stops filling and the network ages to offline within
fifteen minutes. That is the honest consequence of Layers 1–4 not existing yet,
not a bug — and the Dashboard says so in those words rather than pointing a
manager at the shelves.
