# dashboard-web — Layer 5

**Owner:** Rodrigo Chen · **Status:** software integration implemented

The ShelfIQ console. React 19 · Vite · TypeScript · plain CSS (custom
properties + CSS Modules). No component library, no Tailwind, no icon package,
no chart library.

Talks only to `dashboard-api` over REST and one WebSocket. It never connects to
MQTT directly.

```bash
make up      # from the repo root — brings this up with everything else, empty database
make demo    # the same, with sample data and a fake publisher, so it has a system
```

<http://localhost:5173>. On an empty database you get the first-run screen, which
creates the Super admin. In the demo, sign in as the account named in your `.env`.
Invite links open `/join?token=…`.

For local work outside Docker: `npm install && npm run dev`, with
`VITE_API_URL` pointing at the API.

## What is built

| Route | Screen |
|---|---|
| `/` | Dashboard — snapshot, what needs your attention, performance signals |
| `/suggestions` | The review queue. **This is the product.** |
| `/suggestions/:id` | Suggestion detail — the SHAP explanation leads |
| `/products` | Products — filter panel, table and cards, selection island |
| `/products/:id` | Product detail — the 16-character shelf name and its policy |
| `/activity` | Activity log — time-first, with a CSV export |

Additional implemented routes: Shelf labels, Label detail and pairing, Pricing rules, Settings, Users and roles, Locations, and `/system` software status. Label registration and correlated emulator render details are integrated.

For the full software stack use `make software`; see [software guide](../../docs/SOFTWARE.md).

## Where things are

```
src/
  styles/tokens.css     every design token, both the neutrals and the status families
  styles/base.css       the page plane, form controls, shared utilities
  lib/api.ts            the only place this app talks to dashboard-api
  lib/types.ts          the shapes the API returns (money is a string — see the header)
  lib/format.ts         money, percentages, relative time, status thresholds
  lib/useStream.ts      the WebSocket, useResource, useDebounced, useDismiss
  components/AppShell   nav, header, broker banner, the single socket, toasts
  components/ShelfDisplay  the 16x2 LCD and the Heltec E213 e-ink previews
  components/ui.tsx     status tags, pills, pagination, charts, the SHAP ladder
  routes/               one file and one stylesheet per screen
```

## Read this before changing anything visual

**[`DESIGN.md`](DESIGN.md)** — the written system. Tokens, the mono/sans rule,
component anatomy, and §6 "Rules that are easy to break", which is where the
non-obvious constraints live:

- money is a string end to end, because a JSON number is a double
- null renders as an em dash, never 0 or "N/A"
- **"approved" and "on the shelf" are different claims**, and every message
  distinguishes them
- the shelf preview says *"as published"*, never *"what the shelf is showing"* —
  the contract has no acknowledgement topic, so the console cannot know
- a proposal with no explanation is a normal state, not an error

§7 lists each place the implementation deliberately departs from the handoff,
with the reason.

The design bundle itself (`design_handoff_shelfiq/`) is the visual source of
truth; [`docs/prompts/web-design.md`](../../docs/prompts/web-design.md) is the
brief.

## Design language in one line

The dashboard and the shelf label speak the same language — sixteen character
cells because the LCD has sixteen, two rows because it has two, and the
explanation as the hero.
