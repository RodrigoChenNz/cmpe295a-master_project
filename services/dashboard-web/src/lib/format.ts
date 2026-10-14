/* Formatting. The rules here are the reason numbers on screen can be trusted.
 *
 * Two that matter most:
 *
 * 1. `money()` takes the STRING the API sent and formats it. It does not parse
 *    a price into a float and re-render it, because that is how $12.99 becomes
 *    $12.989999999999998 in exactly one browser on demo day.
 * 2. Anything null renders as an em dash, never as 0, "N/A", or a guess. A
 *    margin of null means there is no price yet; showing 0% would be a claim
 *    about the business that nobody made.
 */

import type { LocationLevel, Role } from './types';

export const DASH = '—';

/** Format a money string for display. Input is "3.49" or "449.00". */
export function money(value: string | null | undefined, currency = '$'): string {
  if (value === null || value === undefined || value === '') return DASH;
  // Split on the decimal point rather than converting: the integer part gets
  // thousands separators, the cents are passed through untouched.
  const negative = value.startsWith('-');
  const [whole, cents = '00'] = (negative ? value.slice(1) : value).split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${negative ? '−' : ''}${currency}${grouped}.${cents.padEnd(2, '0').slice(0, 2)}`;
}

/* ── Shelf label lines ────────────────────────────────────────────────────
 *
 * What the firmware will actually render, computed here by the same rule the
 * publisher uses (`gate.render_lines` in dashboard-api). Any preview in this
 * app goes through these, so the mirror cannot disagree with the glass about
 * anything except timing.
 *
 * Both cut at sixteen characters because the hardware does. A longer string
 * does not wrap — it disappears.
 */

/** LCD line 1: the display name, uppercased and cut to sixteen cells. */
export function shelfLine1(displayName: string): string {
  return (displayName ?? '').toUpperCase().slice(0, 16);
}

/** `$X.XX` for a shelf label, from the money string the API sent.
 *
 * Deliberately not `money()`: that groups thousands, and the publisher's
 * `f"${price:.2f}"` does not — a label reading `$1,234.56` would be four
 * characters longer than the one the firmware draws. Also deliberately not
 * `Number(price).toFixed(2)`, which is the float round-trip DESIGN.md rule 1
 * forbids; this pads and slices the string it was given.
 */
function shelfMoney(price: string): string {
  const negative = price.startsWith('-');
  const [whole = '0', cents = ''] = (negative ? price.slice(1) : price).split('.');
  return `${negative ? '-' : ''}$${whole || '0'}.${cents.padEnd(2, '0').slice(0, 2)}`;
}

/** LCD line 2: the price, then the reason tag if what is left of the line can
 *  hold a meaningful amount of it. Mirrors `gate.render_lines` exactly. */
export function shelfLine2(price: string | null | undefined, reasonTag: string): string {
  if (!price) return '';
  const moneyPart = shelfMoney(price);
  const room = 16 - moneyPart.length - 1;
  return room >= 4 && reasonTag
    ? `${moneyPart} ${reasonTag.slice(0, room)}`.slice(0, 16)
    : moneyPart.slice(0, 16);
}

/** Coerce a percentage to a number, or null.
 *
 * It takes `unknown` on purpose. Postgres NUMERIC becomes a Python Decimal, and
 * pydantic v2 serialises Decimal to a *string* — so a percentage that slipped
 * past the API's own serializer arrives here as `"48.6"`. The API is the right
 * place to fix that and it is fixed there, but four people publish into this
 * schema, and a chart that throws because one field came back quoted is a worse
 * outcome than one that renders. A genuinely unparseable value still returns
 * null and shows an em dash rather than a wrong number. */
function asNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

/** A signed percentage, with a true minus sign rather than a hyphen. */
export function pct(value: number | string | null | undefined, digits = 1): string {
  const n = asNumber(value);
  if (n === null) return DASH;
  const sign = n < 0 ? '−' : n > 0 ? '+' : '';
  return `${sign}${Math.abs(n).toFixed(digits)}%`;
}

/** An unsigned percentage, for a margin or a share. */
export function pctPlain(value: number | string | null | undefined, digits = 1): string {
  const n = asNumber(value);
  return n === null ? DASH : `${n.toFixed(digits)}%`;
}

/** A whole-number percentage, for the margin pill in the Products table. */
export function pctWhole(value: number | string | null | undefined): string {
  const n = asNumber(value);
  return n === null ? DASH : `${Math.round(n)}%`;
}

export function int(value: number | string | null | undefined): string {
  const n = asNumber(value);
  return n === null ? DASH : n.toLocaleString('en-US');
}

/* ── Time ─────────────────────────────────────────────────────────────── */

/** HH:MM in the viewer's own locale and zone. The header clock. */
export function clock(d = new Date()): string {
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toLocaleString([], {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

export function timeOnly(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? DASH
    : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

export function dayMonth(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? DASH
    : d.toLocaleDateString([], { day: '2-digit', month: 'short' });
}

/** "12 seconds ago", "7 hours ago", "in 3 hours".
 *
 * Deliberately coarse. A label last seen "1 minute ago" and one seen "63
 * seconds ago" are the same fact, and false precision in a staleness figure
 * invites someone to trust it more than it deserves. */
export function relative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return DASH;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return DASH;

  const secs = Math.round((now - t) / 1000);
  const future = secs < 0;
  const a = Math.abs(secs);

  const say = (n: number, unit: string) =>
    future
      ? `in ${n} ${unit}${n === 1 ? '' : 's'}`
      : `${n} ${unit}${n === 1 ? '' : 's'} ago`;

  if (a < 10) return future ? 'in a moment' : 'just now';
  if (a < 60) return say(a, 'second');
  if (a < 3600) return say(Math.round(a / 60), 'minute');
  if (a < 86400) return say(Math.round(a / 3600), 'hour');
  if (a < 2592000) return say(Math.round(a / 86400), 'day');
  return dayMonth(iso);
}

/** "in 3h 42m", for a proposal's remaining life. Returns null once lapsed, so
 *  the caller shows "Expired" rather than a negative countdown. */
export function countdown(iso: string | null | undefined, now = Date.now()): string | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - now;
  if (Number.isNaN(ms) || ms <= 0) return null;
  const mins = Math.floor(ms / 60000);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/* ── Category pills ───────────────────────────────────────────────────── */

const CATEGORY_TOKENS: Record<string, string> = {
  dairy: 'dairy', bakery: 'bakery', produce: 'produce', pantry: 'pantry',
  seafood: 'seafood', electronics: 'electronics', clothing: 'clothing',
};

/** The CSS variable stem for a category's pill tint. An unknown category gets
 *  the neutral fallback rather than being hashed into an arbitrary colour,
 *  because a colour that means nothing is worse than no colour. */
export function categoryToken(category: string | null | undefined): string {
  if (!category) return 'fallback';
  return CATEGORY_TOKENS[category.toLowerCase()] ?? 'fallback';
}

/* ── Stock ────────────────────────────────────────────────────────────── */

/** Bar colour and width for the Products table's stock cell, per the handoff:
 *  fill is stock/60, at least 5% when stock > 0; <=10 red, <=20 amber, else
 *  green. */
export function stockBar(stock: number | null): { width: string; token: string } {
  if (stock === null || stock <= 0) return { width: '0%', token: 'red' };
  const raw = Math.min(100, (stock / 60) * 100);
  return {
    width: `${Math.max(5, raw)}%`,
    token: stock <= 10 ? 'red' : stock <= 20 ? 'amber' : 'green',
  };
}

/** Margin pill tint, per the handoff: >=30% green, 20-30% amber, <20% red. */
export function marginToken(marginPct: number | null): 'green' | 'amber' | 'red' | 'none' {
  if (marginPct === null || marginPct === undefined) return 'none';
  if (marginPct >= 30) return 'green';
  if (marginPct >= 20) return 'amber';
  return 'red';
}

/** Confidence bar colour. Only ever called when the agent actually reported a
 *  confidence; `confidence_reported: false` renders as text, not an empty bar. */
export function confidenceToken(c: number): 'green' | 'amber' | 'red' {
  if (c >= 0.85) return 'green';
  if (c >= 0.7) return 'amber';
  return 'red';
}

/** RSSI to a 0-3 bar count, using the thresholds the design's sample data
 *  implies: better than -60 is three bars, -75 two, -90 one, below that none. */
export function rssiBars(rssi: number | null): number {
  if (rssi === null) return 0;
  if (rssi >= -60) return 3;
  if (rssi >= -75) return 2;
  if (rssi >= -90) return 1;
  return 0;
}

export function initials(name: string): string {
  const parts = name.split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '??';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return n === 1 ? one : many;
}


/* ── Shelf labels and roles ───────────────────────────────────────────────── */

/** The words the console uses for the four roles the API enforces. */
export const ROLE_LABEL: Record<string, string> = {
  superadmin: 'Super admin',
  manager: 'Store manager',
  staff: 'Shelf staff',
  viewer: 'Read only',
};

/* ── Store layout levels ──────────────────────────────────────────────────── */

/** The store's name for a depth, e.g. 3 → "Aisle". */
export function levelName(levels: LocationLevel[], depth: number): string {
  return levels.find((l) => l.depth === depth)?.name ?? `Level ${depth}`;
}

/** "Floor › Zone › Aisle › Bay". */
export function levelPath(levels: LocationLevel[]): string {
  return levels.map((l) => l.name).join(' › ');
}

/** A level name as a count: "1 aisle", "3 aisles". Names are the store's own,
 *  so pluralising is a best effort: "Shelf" → "shelves", "Bay" → "bays". */
export function levelCount(n: number, name: string): string {
  const lower = name.toLowerCase();
  if (n === 1) return `${n} ${lower}`;
  const plural = /(s|x|z|ch|sh)$/.test(lower)
    ? `${lower}es`
    : /[^aeiou]y$/.test(lower)
      ? `${lower.slice(0, -1)}ies`
      : /lf$/.test(lower)
        ? `${lower.slice(0, -1)}ves`
        : `${lower}s`;
  return `${n} ${plural}`;
}

/** "a bay", "an aisle". */
export function withArticle(word: string): string {
  return /^[aeiou]/i.test(word) ? `an ${word}` : `a ${word}`;
}

/** Most privileged first; mirrors ROLE_ORDER in dashboard-api's auth.py. */
export const ROLE_ORDER: Role[] = ['superadmin', 'manager', 'staff', 'viewer'];

const rank = (role: string) => {
  const i = ROLE_ORDER.indexOf(role as Role);
  return i === -1 ? ROLE_ORDER.length : i;
};

/** May approve prices and change policy: what the API's require_manager admits. */
export function isManagerRole(role: string): boolean {
  return role === 'superadmin' || role === 'manager';
}

/** The API's rank rule: a super admin manages everyone; anyone else only the
 *  roles below their own. Used both for "may I touch this account" and "may I
 *  grant this role". */
export function canManageRole(actor: string, target: string): boolean {
  return actor === 'superadmin' || rank(target) > rank(actor);
}

/** The short hardware name for a table cell.
 *
 * Deliberately a second constant rather than a refactor of `ShelfDisplay`'s
 * HARDWARE_LABEL: that one names the part on a preview where there is room for
 * it ("Heltec Vision Master E213 · e-ink"), this one fits a column. */
export function labelDisplayName(kind: string | null | undefined): string {
  return kind === 'eink' ? 'E213 e-ink' : '16×2 LCD';
}

/** The CSS token stem for a label-status dot. */
export function statusDotToken(status: string | null | undefined): string {
  switch (status) {
    case 'online':
      return 'green';
    case 'syncing':
      return 'amber';
    case 'failed':
      return 'red';
    default:
      return 'muted';
  }
}

export interface DonutSegment {
  key: string;
  label: string;
  value: number;
  tone: string;
  /** stroke-dasharray */
  dash: string;
  /** stroke-dashoffset */
  offset: number;
  pct: number;
}

/** Arc maths for the operational-health ring, kept out of the component.
 *
 * Three cases the design does not cover, each of which draws something wrong if
 * handled naively:
 *   - a total of zero → no segments at all, so the caller renders the track
 *   - one non-zero segment → a full ring with no gap, or you draw a stray notch
 *   - a segment thinner than the gap → clamped, so one failed label is visible
 */
export function donutSegments(
  counts: Record<string, number>,
  order: { key: string; label: string; tone: string }[],
  radius = 58,
  gap = 3,
): { segments: DonutSegment[]; total: number; circumference: number } {
  const circumference = 2 * Math.PI * radius;
  const total = order.reduce((n, o) => n + (counts[o.key] ?? 0), 0);
  if (total === 0) return { segments: [], total: 0, circumference };

  const nonZero = order.filter((o) => (counts[o.key] ?? 0) > 0);
  const useGap = nonZero.length > 1 ? gap : 0;

  let acc = 0;
  const segments: DonutSegment[] = [];
  for (const o of order) {
    const value = counts[o.key] ?? 0;
    if (value > 0) {
      const len = (value / total) * circumference;
      segments.push({
        key: o.key,
        label: o.label,
        tone: o.tone,
        value,
        // At least 1px of arc: a single failed label out of several hundred
        // would otherwise round to nothing and read as "none failed".
        dash: `${Math.max(1, len - useGap).toFixed(2)} ${circumference.toFixed(2)}`,
        offset: -acc,
        pct: Math.round((value / total) * 100),
      });
      acc += len;
    }
  }
  return { segments, total, circumference };
}
