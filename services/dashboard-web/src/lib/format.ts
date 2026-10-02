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
