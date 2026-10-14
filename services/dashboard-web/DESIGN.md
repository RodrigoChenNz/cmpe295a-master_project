# ShelfIQ — design system

The written system behind `services/dashboard-web`. Source of truth for tokens,
the two typographic registers, component anatomy and the rules that are easy to
break by accident.

**It is a transcription, not an invention.** Every value here comes from the
design handoff (`design_handoff_shelfiq/README.md`). If a component needs a
colour that is not in `src/styles/tokens.css`, the answer is to add it there
with the handoff section it came from — never to inline a hex.

---

## 1. The idea in one line

The dashboard and the shelf label speak the same language. What a shopper sees
on a 16×2 LCD, the manager sees expanded, annotated and explained — and every
visual decision traces to a hardware constraint or a data field rather than to
taste.

The two places this is literal:

- **`ShelfDisplay`** renders a character-accurate 16×2 LCD, cell by cell, with
  a visible unlit ground. It is not an illustration of a label; it is a
  rendering spec the firmware author can check his output against.
- **The 16-character cap** on `display_name` and `reason_tag` is enforced in the
  schema, in the API, and visibly in the UI with a live counter that turns red.
  Past sixteen characters the firmware drops the rest, so a longer name does not
  wrap — it disappears.

---

## 2. Tokens

All in `src/styles/tokens.css`. Named by role, not by appearance, so that
`--divider` can change without a search for `#dfdacb`.

### Radii

| Token | Value | Used by |
|---|---|---|
| `--r` | 6px | buttons, inputs, pills, filter rows, nav items |
| `--rl` | 10px | cards, panels, menus |

### Accent

`--ac`, default `#17150e` — the ink itself, which is why primary buttons read as
printed rather than branded. The handoff's alternates were `#2f4a3a`, `#6b3fa0`,
`#7a2e1f`. Swapping `--ac` is the whole theme mechanism.

### Warm neutrals, dark to light

| Token | Value | Role |
|---|---|---|
| `--ink` | `#17150e` | primary text, nav background, primary button |
| `--ink-strong` | `#2a281f` | strong text, field labels |
| `--ink-body` | `#3a372c` | body-strong, table body |
| `--ink-2` | `#5c584a` | secondary text |
| `--ink-muted` | `#6f6a57` | muted text, table headings |
| `--nav-muted` | `#9a9484` | nav labels at rest, group headings |
| `--disabled` | `#aaa390` | disabled text, em dashes |
| `--border-input` | `#b9b197` | input borders, disabled chevrons |
| `--border-card` | `#cbc4ae` | card and panel borders |
| `--divider` | `#dfdacb` | dividers, row borders |
| `--track` | `#e2ddcf` | bar track |
| `--page` | `#e8e4d8` | page background, secondary-button hover |
| `--hover` | `#eeeade` | hover, active filter row |
| `--row-selected` | `#f1ede0` | selected row |
| `--row-hover` | `#f3efe3` | row hover |
| `--subtle` | `#f7f4ea` | subtle panel, pagination bar, text on dark |
| `--surface` | `#fbf9f2` | card surface |
| `--photo-slot` | `#f2eee2` | product photo placeholder |

Nav: `--nav-bg #17150e`, `--nav-hover #252318`, `--nav-divider #33302a`.

### Status

Three families, each a triple of line / text-on-tint / tint:

| Family | Line | Text | Tint |
|---|---|---|---|
| green | `#1c7a46` | `#1c5e36` | `#e0ecdf` |
| amber | `#c28a12` | `#7a4f12` · `#8c6008` | `#f3e6d0` |
| red | `#a5321f` | `#a5321f` (bar `#c9503c`) | `#f5dfda` |

**A value never wears its status colour as text on the page background.** Status
lives inside its own pill, where the tint/text pair is contrast-checked as a
unit. Identity comes from a coloured mark *beside* the text, not from the text.

### Category pill tints

Dairy `#e3e9f2`/`#2f4d74` · Bakery `#f3e6d0`/`#7a4f12` · Produce
`#e0ecdf`/`#2d6233` · Pantry `#ebe3f0`/`#5b3d72` · Seafood `#dcecea`/`#22605b` ·
Electronics `#e4e4f2`/`#3d3f7a` · Clothing `#f2e1e1`/`#7f3434` · fallback
`#eeeade`/`#3a372c`.

An unknown category gets the neutral fallback rather than being hashed into an
arbitrary colour. **A colour that means nothing is worse than no colour.**

### Shadows

Menus `0 8px 22px rgba(23,21,14,0.16)` · account menu `…0.22` · island
`0 8px 24px rgba(23,21,14,0.22)` · modal backdrop `rgba(23,21,14,0.42)`.

### Layout

`--nav-w` 232px, `--nav-w-collapsed` 64px, `--header-h` 76px, `--page-pad` 24px,
`--page-max` 1488px, `--content-max` 1440px.

`--header-h` is a token because **three** things depend on it being the same
number: the header, the Products toolbar's `top:` offset, and the filter panel's
sticky offset. Hardcoding 76px in any of them is how they drift apart.

---

## 3. The two typographic registers

**Space Grotesk** for the interface. **IBM Plex Mono** for machine values. Both
from Google Fonts, preconnected in `index.html`.

The split is a rule, not a preference:

> **Mono if and only if the string could appear on a shelf label or in a log
> line.**

Mono: `product_id`, SKU, UPC, price, `reason_tag`, timestamps, `decision_id`,
`agent_version`, label IDs, slot codes, firmware versions, counts in a column,
and every uppercase button label.

Sans: the product **name**, every heading, all prose, every empty state, every
guidance note, and the SHAP feature labels.

**Never set the explanation in mono.** Long monospace paragraphs are measurably
slower to read, and the explanation is the one thing in this product that must
not be hard to read. Hard cap: any string over five words is sans.

### Scale

| Token | Size | Used by |
|---|---|---|
| `--fs-title` | 26px/600 | page H1 |
| `--fs-panel-lg` | 20px/600 | "What needs your attention" |
| `--fs-panel` | 15px/600 | panel titles |
| `--fs-card` | 14px | card titles, table cells, nav items |
| `--fs-body` | 13px | body |
| `--fs-small` | 12.5px | secondary |
| `--fs-xs` | 12px | notes, field labels |
| `--fs-mono-label` | 10px | uppercase mono labels, letter-spacing 0.12em |

`tabular-nums` on anything in a column or stacked above another number.

---

## 4. Motion

| Thing | Duration | Easing |
|---|---|---|
| nav width | `--t-nav` 200ms, 60ms delay | `--ease` |
| nav labels | `--t-label` 130ms | ease |
| island in | `--t-island` 400ms | `--ease-in-island` |
| island out | `--t-island` 400ms | `--ease-out-island` |

The 60ms delay on the nav width lets labels start fading before the width moves,
so the text does not appear to squeeze.

**Reduced motion** zeroes the duration *tokens* rather than removing each
transition, so every component keeps its final state without having to know
about the preference. `@media (prefers-reduced-motion: reduce)` in
`tokens.css` is the only place this is handled.

---

## 5. Components

Primitives live in `src/components/ui.tsx`. Each is here because the handoff
repeats it on more than one screen.

### `StatusTag` / `StatusChip`

Thirteen status words collapse onto **four tones**, and the mapping is the
interesting part:

| Tone | Words | Means |
|---|---|---|
| good | confirmed, online, synced, connected, approved | it happened |
| wait | pending, syncing, stale, **queued** | we have not heard back |
| bad | failed, rejected, disconnected | it did not happen |
| none | offline, unassigned, excluded, expired, superseded | there is nothing to report |

`queued` is ours, not the design's: a price that is approved and audited but has
not left the building. Amber, because nothing has reached a shelf.

The tag carries a 5px colour bar *and* the word, so colour is never the only
channel.

### `ShelfDisplay`

Two genuinely different devices, not two skins:

- **`lcd`** — 16×2 character LCD. Sixteen cells per line, always: a short string
  pads with unlit cells rather than collapsing, because the physical display
  does not shrink to fit. Cells are `aria-hidden` with one readable string
  below, so a screen reader hears the name rather than fifteen letters.
- **`eink`** — Heltec Vision Master E213, 250×122 on an ESP32. A pixel display,
  so it is **not** bound by sixteen characters and gets the full product name,
  unit price and slot code. One scale factor for the whole panel, because that
  is what the hardware does.

**The caption is load-bearing.** It says *"Shelf render — as published"*, never
*"what the shelf is showing"*. The dashboard and the edge node are independent
MQTT subscribers and the contract has no acknowledgement topic, so a label can
be displaying a stale price out of flash while this preview shows the current
one. At a booth, a mirror that disagrees with the physical LCD beside it is the
worst failure available.

`was` and the inverted `REDUCED` tag appear **only on a markdown**. An increase
dressed as a reduction misleads a shopper standing in front of the shelf.

### `ShapLadder`

The hero of `/suggestions/:id`. Diverging, with a neutral axis:

- `--shap-down #2f5d8c` (cool) — pushed the price down
- `--shap-up #9c4a2c` (warm) — pushed it up

Two hues that read as **opposite**, because the chart's entire job is polarity.
The zero line is neutral grey, never the accent.

It checks additivity (`base_value + Σshap ≈ proposed_price`) and **says so when
it fails** rather than drawing bars that imply a decomposition they are not.

### `Confidence`

Renders a bar only when the agent actually reported a confidence. Otherwise it
says *"Not reported"* in words. **An empty bar reads as zero confidence, which
is a claim the agent did not make.**

### `Modal`

`{ title, subtitle?, width, onClose, children, footer }`. Handles the backdrop,
`role="dialog"`, Escape and outside-click (via `useDismiss`), and moving focus
into the dialog — preferring `[data-autofocus]`, because the close button is
earlier in the DOM than the body and would otherwise take focus on a form.

**`onClose` is where a caller intervenes.** Escape, the ×, and an outside click
all route through it, so a modal with unsaved work makes that one function
decide — see `AddProductModal`'s `requestClose`.

**Stacking two modals needs a guard.** `useDismiss` listens on `document`, so
`stopPropagation` does nothing between two dialogs' listeners, and the upper
modal's backdrop lies entirely outside the lower one's ref — meaning a click on
*Keep editing* would otherwise close the form underneath it. The lower modal
returns early from its close handler while the upper one is open.

### `Pagination`

Shared by Products, Shelf labels and Activity log. Rows-per-page 10/25/50;
Products and Shelf labels default to 25, Activity log to 10. Changing rows per
page resets to page 1. The page window is five wide and slides. When filters
shrink the list, **the API clamps to the last page that exists** so every client
behaves the same.

### `Sparkline` / `PriceHistoryChart`

Hand-built SVG; no chart library. Two rules:

- **Nulls are gaps, not zeroes.** A day with nothing to report is not a day with
  a value of nothing, so the line breaks.
- **Price history is a step chart.** A price was $3.49 until the instant it
  became $2.99; interpolating between them draws prices the shelf never showed.
  The proposal arm is dashed because it is a price that has not happened.

### The selection island (Products)

Fixed, bottom 24px, centred over the main area with the nav width subtracted —
so it is centred on the *content*, not the window, and the offset follows the
nav when it collapses. `islandIn` / `islandOut` keyframes. Escape clears the
selection, and so does a click outside both the table and the island.

---

## 6. Rules that are easy to break

1. **Money is a string, end to end.** The API sends `"3.49"`; `money()` formats
   the string it was given. Nothing parses a price into a float and re-renders
   it. The one exception is an MQTT payload, where the contract specifies a
   number — those go through `db.money_num`, which quantises first.
2. **Null renders as an em dash.** Never 0, never "N/A", never a guess. A margin
   of null means there is no price yet; `0%` would be a claim about the business
   that nobody made.
3. **Every number traces to a row.** The 295A build shipped `$124,690` and
   `34.2%` — invented figures that made the whole thing read as a mockup. While
   the seed is present, `/api/meta` reports `sample_data` and the shell shows a
   banner saying so.
4. **"Approved" and "on the shelf" are different claims.** Every success message
   distinguishes them. When `published: false`, the price is recorded and queued
   and the UI says exactly that.
5. **The explanation explains the proposal, not the override.** When a manager
   types a different price, the screen says so *before* they click.
6. **Panel counts are computed against the search-filtered catalogue**, never
   the panel-filtered one. A count that changes when you tick the box beside it
   makes the panel unreadable. `/api/products/facets` takes only `q`.
7. **Refetch keeps the old rows.** A skeleton appears on first load only;
   replacing correct data with a shimmer makes a fast connection look broken.

---

## 7. Deliberate deviations from the handoff

| What | Why |
|---|---|
| **Products bulk selection is a floating island.** | The handoff README specifies an island; the prototype's markup has an inline bar inside the table card. The README is explicit ("a floating island, not an inline bar") and the prototype ships the `islandIn`/`islandOut` keyframes for it, so the README wins and the bar looks like the older variant. |
| **The sign-in fields start empty.** | The prototype prefills an email and password. Fine in a mockup, indefensible in something that issues a real session cookie. |
| **No "Forgot password" link.** | There is no mail transport in this system, so the link would go nowhere. It says "Ask a manager for a reset", which is what actually happens. |
| **Dashboard signals are changes / delegated share / markdown share.** | The handoff names Sales, Margin and Markdown. There is no point-of-sale feed anywhere in this system, so a sales line would be invented. The screen says that out loud rather than leaving the omission to be noticed. |
| **Product detail separates Save from Publish.** | Editing a catalogue row and publishing a price to a shelf are different actions with different consequences, so they get different buttons. |
| **The Add product modal has no `product_id` field, and its Cost is required.** | `product_id` is the frozen contract key that four services join on, so the server allocates it under an advisory lock. `unit_cost` is `NOT NULL`, and defaulting it to `0.00` would publish a claim that the product costs nothing — an invented figure of exactly the kind rule 3 exists to prevent. |
| **A staff user cannot set an opening price.** | It becomes a `price_history` row carrying `approval` ∈ `auto\|manager\|override` and `approved_by`, plus a retained message that goes live the instant a label is paired. Writing `approval: "manager"` for a staff user would put a false claim of authority in an append-only audit table. They see an em dash and "A manager sets the opening price." |
| **The Add preview's LCD line 2 is `$3.49 New produ`, not the handoff's `$X.XX EA`.** | The preview is computed by `shelfLine2`, the same rule the publisher uses, so the mirror cannot disagree with the glass about anything but timing. "EA" is a unit-pricing idiom this system never publishes. |
| **The Add modal keeps the shared backdrop, body padding and close button** rather than the handoff's `flex-start / 56px 40px`, `22px 24px` body and 28px bordered ×. | One close affordance and one dialog position across every modal beats matching one prototype screen's 2px details. `.modal` already scrolls its body, so a tall form works centred. |
| **Category is a select here, free text on Product detail.** | A typo'd "Diary" would permanently fragment the filter panel's counts. The two screens disagreeing about one field is itself a defect — Product detail should converge to a select. |
| **"+ Add product" greys out via `aria-disabled`, not `disabled`.** | The handoff greys it while invalid. A truly disabled button cannot be focused and tells a keyboard user nothing, so the validation messages would be unreachable; `aria-disabled` looks the same, stays clickable, and clicking is what reveals the inline errors. |
| **Label detail says "Settled at", not the handoff's "Confirmed at".** | `label_pushes.result = 'confirmed'` is written by a 45-second timeout, not by anything the label said. There is no acknowledgement topic, so "confirmed" would claim a fact this system cannot observe. Renaming one screen label is cheaper and more honest than renaming a database value four services may come to read. |
| **Three roles, not the handoff's four.** | `users.role` is a CHECK of exactly `manager`, `staff`, `viewer`. A fourth tier in the UI would be a tab that maps to nothing the API enforces. They are relabelled "Store manager", "Shelf staff" and "Read only", and the permission matrix is built from the real auth dependencies (`require_manager`, `require_editor`, `current_user`) — each row names the dependency, so the screen cannot drift from what the server actually checks. |
| **Pricing rules are percentages ("Margin floor %", "Ceiling headroom %"), not the handoff's money fields.** | `category_rules` stores `min_margin_pct` and `ceiling_pct`, and `min_price`/`max_price` are derived from them per SKU against each product's own cost. A money field would have to pick one cost to divide by, which is the invented figure rule 3 forbids. The columns are relabelled instead. |
| **Blink is a console-owned topic, outside `contracts/`.** | `retail/label/{label_id}/blink`, qos 1, **`retain=False`** — a retained blink re-fires on every reconnect, forever. It goes through `gate.publish_label_command()` so every outbound publish still passes one module, and the button says "Blink sent", never "the label is blinking": nothing acknowledges it. |
| **Settings omits Notifications, Agent schedule and the field-packs card; the store form omits Address.** | Each would be a control wired to nothing — there is no mail transport, the agent's cadence lives in the publisher process rather than any table this API can write, and no column stores an address. The MQTT card keeps only what is real: Test connection opens an actual broker session, and the card states the running host beside the stored one, because a form echoing a host this service is not connected to is the failure the whole mirror discipline exists to prevent. |
| **Deleting a location asks for its blast radius back.** | `locations.parent_id` cascades and `shelf_labels.slot_code` is `ON DELETE SET NULL`, so one click on a zone can strip the position off a dozen labels. `DELETE` takes the slot and label counts the confirmation displayed, rechecks them inside the transaction, and 409s if they disagree — the dialog is an optimistic-concurrency token, not decoration. The cleared labels each get a `slot_cleared` event, because otherwise the only trace is absent data. |
| **`category_rules.floor_pct` was dropped.** | Written by `PUT /api/rules/categories/{c}` and read by nothing. Left in place it is a number the API keeps returning that no longer corresponds to any behaviour. |

## 8. What is not built yet

Every screen in the handoff now exists. What remains is behaviour the hardware
and the contracts cannot yet support:

- **Shelf confirmation.** Label liveness is inferred from silence and "settled"
  is a timeout. See `services/dashboard-api/README.md` for the acknowledgement
  topic that would fix it.
- **`confidence` and a prose `rationale`** on a proposal are nullable columns
  the UI reports as "Not reported" when a publisher omits them.
- **Per-SKU limits a manager set by hand are overwritten** by a category save.
  The preview modal now shows how many rows would change, but skipping SKUs
  whose `policy_set_by` is a username is its own decision and its own change.
