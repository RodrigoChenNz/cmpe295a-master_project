/* The primitives the handoff repeats on every screen, in one file because they
 * are each a few lines and splitting them would cost more in imports than it
 * buys in navigation.
 *
 * Status vocabulary is defined once here and nowhere else. The design's StatusTag
 * accepts thirteen status words that collapse onto four meanings, and the
 * mapping is the interesting part — `syncing` and `pending` are the same amber
 * "we have not heard back yet", while `offline` and `unassigned` are both the
 * neutral "there is nothing to report", which is a different thing to say than
 * a failure.
 */

import { useEffect, useId, useRef, useState } from 'react';
import { categoryToken, confidenceToken, marginToken, pctWhole, rssiBars, stockBar } from '../lib/format';
import { useDismiss } from '../lib/useStream';
import s from './ui.module.css';

/* ── Buttons ──────────────────────────────────────────────────────────── */

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md';
  busy?: boolean;
};

export function Button({
  variant = 'primary',
  size = 'md',
  busy = false,
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      className={[s.btn, s[variant], s[size], className].filter(Boolean).join(' ')}
      // Busy implies disabled: a second click on a button that is mid-request
      // is the ordinary way to approve a price twice.
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {busy && <span className={s.spinner} aria-hidden="true" />}
      {children}
    </button>
  );
}

/* ── Status tag ───────────────────────────────────────────────────────── */

export type StatusWord =
  | 'confirmed' | 'online' | 'synced' | 'connected' | 'approved'
  | 'pending' | 'syncing' | 'stale' | 'queued'
  | 'failed' | 'rejected' | 'disconnected'
  | 'offline' | 'unassigned' | 'excluded' | 'expired' | 'superseded';

/** Four tones, not thirteen colours. The second element is the default label,
 *  which a caller may override when the screen's own wording is clearer. */
const STATUS: Record<StatusWord, ['good' | 'wait' | 'bad' | 'none', string]> = {
  confirmed: ['good', 'Confirmed'],
  online: ['good', 'Online'],
  synced: ['good', 'Synced'],
  connected: ['good', 'Connected'],
  approved: ['good', 'Approved'],

  pending: ['wait', 'Pending'],
  syncing: ['wait', 'Awaiting sync'],
  stale: ['wait', 'Stale'],
  // Approved and recorded, but the broker has not taken it. Amber rather than
  // green, because nothing has reached a shelf.
  queued: ['wait', 'Queued'],

  failed: ['bad', 'Failed'],
  rejected: ['bad', 'Rejected'],
  disconnected: ['bad', 'Disconnected'],

  offline: ['none', 'Offline'],
  unassigned: ['none', 'Unassigned'],
  excluded: ['none', 'Excluded'],
  expired: ['none', 'Lapsed'],
  superseded: ['none', 'Superseded'],
};

export function StatusTag({ status, label }: { status: string; label?: string }) {
  const key = (status?.toLowerCase() ?? 'offline') as StatusWord;
  const [tone, text] = STATUS[key] ?? STATUS.offline;
  return (
    <span className={`${s.tag} ${s[`tone_${tone}`]}`}>
      <span className={s.tagBar} aria-hidden="true" />
      <span className={s.tagText}>{label ?? text}</span>
    </span>
  );
}

/** The rounder, dotted variant, for use inline in prose and on cards. */
export function StatusChip({ status, label }: { status: string; label?: string }) {
  const key = (status?.toLowerCase() ?? 'offline') as StatusWord;
  const [tone, text] = STATUS[key] ?? STATUS.offline;
  return (
    <span className={`${s.chip} ${s[`tone_${tone}`]}`}>
      <span className={s.chipDot} aria-hidden="true" />
      {label ?? text}
    </span>
  );
}

/* ── Category pill ────────────────────────────────────────────────────── */

export function CategoryPill({ category }: { category: string | null }) {
  if (!category) return <span className={s.dash}>—</span>;
  return (
    <span className={s.catPill} data-cat={categoryToken(category)}>
      {category}
    </span>
  );
}

/* ── Margin pill ──────────────────────────────────────────────────────── */

export function MarginPill({ marginPct }: { marginPct: number | null }) {
  const tone = marginToken(marginPct);
  if (tone === 'none') return <span className={s.dash}>—</span>;
  return (
    <span className={`${s.marginPill} ${s[`margin_${tone}`]} tnum`}>
      {pctWhole(marginPct)}
    </span>
  );
}

/* ── Stock cell ───────────────────────────────────────────────────────── */

export function StockCell({ stock }: { stock: number | null }) {
  if (stock === null) return <span className={s.dash}>—</span>;
  const bar = stockBar(stock);
  return (
    <span className={s.stock}>
      <span className={`${s.stockNum} tnum`} data-low={stock <= 10 || undefined}>
        {stock}
      </span>
      <span className={s.stockTrack}>
        <span className={s.stockFill} style={{ width: bar.width }} data-tone={bar.token} />
      </span>
    </span>
  );
}

/* ── Confidence bar ───────────────────────────────────────────────────── */

export function Confidence({
  value,
  reported,
}: {
  value: number | null;
  reported: boolean;
}) {
  // An empty bar reads as zero confidence, which is a claim. When the agent
  // reported nothing, say nothing.
  if (!reported || value === null) {
    return <span className={s.notReported}>Not reported</span>;
  }
  return (
    <span className={s.conf} title={`The agent reported ${Math.round(value * 100)}% confidence`}>
      <span className={s.confTrack}>
        <span
          className={s.confFill}
          style={{ width: `${Math.round(value * 100)}%` }}
          data-tone={confidenceToken(value)}
        />
      </span>
      <span className={`${s.confNum} tnum`}>{Math.round(value * 100)}%</span>
    </span>
  );
}

/* ── Signal bars ──────────────────────────────────────────────────────── */

export function SignalBars({ rssi }: { rssi: number | null }) {
  const bars = rssiBars(rssi);
  return (
    <span className={s.bars} title={rssi === null ? 'No signal reported' : `${rssi} dBm`}>
      {[1, 2, 3].map((n) => (
        <span key={n} className={s.bar} data-on={n <= bars || undefined} data-h={n} />
      ))}
      <span className="srOnly">{bars} of 3 bars</span>
    </span>
  );
}

/* ── Battery ──────────────────────────────────────────────────────────── */

export function Battery({ pct }: { pct: number | null }) {
  if (pct === null) return <span className={s.dash}>—</span>;
  return (
    <span className={s.battery}>
      <span className={s.batteryShell}>
        <span
          className={s.batteryFill}
          style={{ width: `${Math.max(2, pct)}%` }}
          data-tone={pct <= 10 ? 'red' : pct <= 25 ? 'amber' : 'green'}
        />
      </span>
      <span className={`${s.batteryNum} tnum`}>{pct}%</span>
    </span>
  );
}

/* ── Icons ────────────────────────────────────────────────────────────────
 * Hand-drawn inline SVG rather than an icon package, so the app ships no
 * dependency for nine glyphs and every stroke width is the handoff's. */

export const Icon = {
  search: () => (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <circle cx="7" cy="7" r="4.6" />
      <line x1="10.6" y1="10.6" x2="14.5" y2="14.5" strokeLinecap="round" />
    </svg>
  ),
  check: () => (
    <svg viewBox="0 0 14 14" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M2.5 7.5l3 3 6-6.5" />
    </svg>
  ),
  cross: () => (
    <svg viewBox="0 0 14 14" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
      <path d="M3 3l8 8M11 3l-8 8" />
    </svg>
  ),
  chevronLeft: () => (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="10,3.5 5.5,8 10,12.5" />
    </svg>
  ),
  chevronRight: () => (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="6,3.5 10.5,8 6,12.5" />
    </svg>
  ),
  back: () => (
    <svg viewBox="0 0 16 16" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="13" y1="8" x2="3" y2="8" />
      <polyline points="7.2,3.8 3,8 7.2,12.2" />
    </svg>
  ),
  bell: () => (
    <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <path d="M4 11V7.2a4 4 0 0 1 8 0V11l1.3 1.4H2.7Z" strokeLinejoin="round" />
      <path d="M6.6 14.2a1.6 1.6 0 0 0 2.8 0" strokeLinecap="round" />
    </svg>
  ),
  tag: () => (
    <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" aria-hidden="true">
      <path d="M2 8.6V2.5h6.1l5.4 5.4-6.1 6.1z" />
      <circle cx="5.2" cy="5.7" r="1.1" />
    </svg>
  ),
  folder: () => (
    <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" aria-hidden="true">
      <path d="M1.8 4.2h4.3l1.4 1.6h6.7v7H1.8z" />
    </svg>
  ),
  trash: () => (
    <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" aria-hidden="true">
      <path d="M2.8 4.3h10.4M6.2 4.3V2.6h3.6v1.7M4.2 4.3l.7 9h6.2l.7-9" />
    </svg>
  ),
  send: () => (
    <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" aria-hidden="true">
      <path d="M2 8l12-5.5L10.5 14 8 9.5z" />
    </svg>
  ),
  download: () => (
    <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" aria-hidden="true">
      <path d="M8 2v8M4.6 7l3.4 3.4L11.4 7M2.5 13.4h11" />
    </svg>
  ),
};

/* ── Search input ─────────────────────────────────────────────────────── */

export function SearchInput({
  value,
  onChange,
  placeholder = 'Search',
  width,
  ...rest
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  width?: string;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'width'>) {
  return (
    <span className={s.searchWrap} style={width ? { flex: `0 1 ${width}` } : undefined}>
      <span className={s.searchIcon}>
        <Icon.search />
      </span>
      <input
        type="search"
        className={`field ${s.searchInput}`}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        {...rest}
      />
    </span>
  );
}

/* ── Dropdown menu ────────────────────────────────────────────────────── */

export interface MenuItem {
  label: string;
  onSelect: () => void;
  destructive?: boolean;
  disabled?: boolean;
  href?: string;
}

/** The ⋯ button and its menu. Closes on Escape and on an outside click, which
 *  `useDismiss` handles once for every menu in the app. */
export function MoreMenu({
  items,
  title = 'More actions',
  align = 'right',
  up = false,
  bordered = true,
}: {
  items: MenuItem[];
  title?: string;
  align?: 'left' | 'right';
  up?: boolean;
  bordered?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const id = useId();
  useDismiss(open, () => setOpen(false), ref);

  return (
    <span className={s.menuWrap} ref={ref}>
      <button
        type="button"
        className={bordered ? s.moreBtn : s.moreBtnPlain}
        title={title}
        aria-label={title}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((o) => !o)}
      >
        ⋯
      </button>
      {open && (
        <div
          id={id}
          role="menu"
          className={`${s.menu} ${align === 'left' ? s.menuLeft : s.menuRight} ${up ? s.menuUp : ''}`}
        >
          {items.map((item) =>
            item.href ? (
              <a
                key={item.label}
                role="menuitem"
                href={item.href}
                className={s.menuItem}
                onClick={() => setOpen(false)}
              >
                {item.label}
              </a>
            ) : (
              <button
                key={item.label}
                type="button"
                role="menuitem"
                className={s.menuItem}
                data-destructive={item.destructive || undefined}
                disabled={item.disabled}
                onClick={() => {
                  setOpen(false);
                  item.onSelect();
                }}
              >
                {item.label}
              </button>
            ),
          )}
        </div>
      )}
    </span>
  );
}

/* ── Pagination ───────────────────────────────────────────────────────── */

export function Pagination({
  page,
  pages,
  total,
  pageSize,
  onPage,
  onPageSize,
}: {
  page: number;
  pages: number;
  total: number;
  pageSize: number;
  onPage: (p: number) => void;
  onPageSize: (n: number) => void;
}) {
  // A five-wide window that slides, so the current page is never at the edge
  // unless it is genuinely at the start or end of the range.
  const windowSize = Math.min(5, pages);
  let start = Math.max(1, page - Math.floor(windowSize / 2));
  start = Math.min(start, Math.max(1, pages - windowSize + 1));
  const numbers = Array.from({ length: windowSize }, (_, i) => start + i);

  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(total, page * pageSize);

  return (
    <div className={s.pagination}>
      <span className={s.pageSize}>
        <label htmlFor="rows-per-page" className={s.pageSizeLabel}>
          Rows per page
        </label>
        <select
          id="rows-per-page"
          className={`field ${s.pageSizeSelect}`}
          value={pageSize}
          onChange={(e) => onPageSize(Number(e.target.value))}
        >
          <option value={10}>10</option>
          <option value={25}>25</option>
          <option value={50}>50</option>
        </select>
      </span>

      <span className={`${s.range} tnum`}>
        {total === 0 ? 'No rows' : `${first}–${last} of ${total.toLocaleString('en-US')}`}
      </span>

      <span className={s.pageNums}>
        <button
          type="button"
          className={s.pageArrow}
          title="Previous page"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          <Icon.chevronLeft />
        </button>
        {numbers.map((n) => (
          <button
            key={n}
            type="button"
            className={`${s.pageNum} tnum`}
            data-current={n === page || undefined}
            aria-current={n === page ? 'page' : undefined}
            onClick={() => onPage(n)}
          >
            {n}
          </button>
        ))}
        <button
          type="button"
          className={s.pageArrow}
          title="Next page"
          aria-label="Next page"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
        >
          <Icon.chevronRight />
        </button>
      </span>
    </div>
  );
}

/* ── States ───────────────────────────────────────────────────────────── */

export function EmptyState({
  title,
  body,
  actions,
}: {
  title: string;
  body: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className={`card ${s.empty}`}>
      <span className={s.emptyMark} aria-hidden="true" />
      <h3 className={s.emptyTitle}>{title}</h3>
      <p className={s.emptyBody}>{body}</p>
      {actions && <div className={s.emptyActions}>{actions}</div>}
    </div>
  );
}

export function ErrorState({
  title,
  body,
  actions,
}: {
  title: string;
  body: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className={`card ${s.errorCard}`} role="alert">
      <span className={s.errorMark} aria-hidden="true">
        !
      </span>
      <h3 className={s.emptyTitle}>{title}</h3>
      <p className={s.emptyBody}>{body}</p>
      {actions && <div className={s.emptyActions}>{actions}</div>}
    </div>
  );
}

/** Shimmer rows, sized to the table they stand in for. Shown only on a first
 *  load; a refetch keeps the previous rows, because replacing correct data with
 *  a skeleton makes a fast connection look broken. */
export function SkeletonRows({ rows = 7, columns = 5 }: { rows?: number; columns?: number }) {
  const widths = ['38%', '14%', '12%', '16%', '10%', '8%', '12%'];
  return (
    <div className={`card ${s.skeleton}`} aria-hidden="true">
      {Array.from({ length: rows }).map((_, r) => (
        <div className={s.skelRow} key={r}>
          <span className={s.skelBox} />
          {Array.from({ length: columns }).map((_, c) => (
            <span
              className={s.skelBar}
              key={c}
              style={{ width: widths[c % widths.length] }}
            />
          ))}
        </div>
      ))}
      <span className="srOnly">Loading</span>
    </div>
  );
}

/* ── Modal ────────────────────────────────────────────────────────────── */

export function Modal({
  title,
  width = 420,
  onClose,
  children,
  footer,
}: {
  title: string;
  width?: number;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDismiss(true, onClose, ref);

  // Move focus into the dialog, so a keyboard user is not left tabbing the page
  // behind the backdrop.
  useEffect(() => {
    const first = ref.current?.querySelector<HTMLElement>(
      'input, select, textarea, button, [tabindex]:not([tabindex="-1"])',
    );
    first?.focus();
  }, []);

  return (
    <div className={s.backdrop}>
      <div
        className={s.modal}
        style={{ width }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={ref}
      >
        <div className={s.modalHead}>
          <h2 id={titleId} className={s.modalTitle}>
            {title}
          </h2>
          <button type="button" className={s.modalClose} onClick={onClose} aria-label="Close">
            <Icon.cross />
          </button>
        </div>
        <div className={s.modalBody}>{children}</div>
        {footer && <div className={s.modalFoot}>{footer}</div>}
      </div>
    </div>
  );
}

/* ── Sparkline ────────────────────────────────────────────────────────────
 * Hand-built SVG rather than a chart library: it is a polyline and a fill, and
 * no library's defaults survive contact with this design anyway. Nulls in the
 * series are gaps, not zeroes — a day with nothing to report is not a day with
 * a value of nothing. */

export function Sparkline({
  points,
  width = 240,
  height = 64,
}: {
  points: (number | null)[];
  width?: number;
  height?: number;
}) {
  const real = points.filter((p): p is number => p !== null);
  if (real.length < 2) {
    return (
      <div className={s.sparkEmpty} style={{ height }}>
        Not enough data yet
      </div>
    );
  }

  const min = Math.min(...real);
  const max = Math.max(...real);
  // A flat series would divide by zero; it is drawn through the middle instead
  // of being stretched to fill the box, which would invent variation.
  const span = max - min || 1;
  const pad = 4;
  const usable = height - pad * 2;

  const coords = points.map((p, i) => {
    const x = (i / (points.length - 1)) * width;
    const y = p === null ? null : pad + usable - ((p - min) / span) * usable;
    return { x, y };
  });

  // Split on nulls, so a gap is a break in the line rather than a line drawn
  // through a day that has no value.
  const segments: string[] = [];
  let current: string[] = [];
  for (const c of coords) {
    if (c.y === null) {
      if (current.length > 1) segments.push(current.join(' '));
      current = [];
    } else {
      current.push(`${c.x.toFixed(1)},${c.y.toFixed(1)}`);
    }
  }
  if (current.length > 1) segments.push(current.join(' '));

  const filled = coords.filter((c) => c.y !== null);
  const area =
    filled.length > 1
      ? `${filled[0].x.toFixed(1)},${height} ` +
        filled.map((c) => `${c.x.toFixed(1)},${(c.y as number).toFixed(1)}`).join(' ') +
        ` ${filled[filled.length - 1].x.toFixed(1)},${height}`
      : '';

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      width="100%"
      height={height}
      className={s.spark}
      role="img"
      aria-label={`Trend over ${points.length} days`}
    >
      {area && <polygon points={area} className={s.sparkArea} />}
      {segments.map((seg, i) => (
        <polyline key={i} points={seg} className={s.sparkLine} vectorEffect="non-scaling-stroke" />
      ))}
    </svg>
  );
}

/* ── Price history chart ──────────────────────────────────────────────────
 * A step chart, not a smooth line. A price is a step function: it was $3.49
 * until the moment it became $2.99, and interpolating between them draws prices
 * the shelf never showed. The dashed arm is a proposal — a price that has not
 * happened — and it is dashed for exactly that reason. */

export function PriceHistoryChart({
  points,
  proposed,
  height = 170,
}: {
  points: { price: string; ts: string }[];
  proposed?: string | null;
  height?: number;
}) {
  if (points.length < 2) {
    return (
      <div className={s.sparkEmpty} style={{ height }}>
        Not enough price history to chart yet
      </div>
    );
  }

  const width = 640;
  const values = points.map((p) => Number(p.price));
  const proposedNum = proposed ? Number(proposed) : null;
  const all = proposedNum !== null ? [...values, proposedNum] : values;
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const span = hi - lo || 1;
  const pad = 10;
  const plot = height - pad * 2;
  const y = (v: number) => pad + plot - ((v - lo) / span) * plot;
  // Leave the last eighth for the proposal arm when there is one, so the dashed
  // segment is visible rather than crammed against the right edge.
  const usableW = proposedNum !== null ? width * 0.86 : width;
  const x = (i: number) => (i / (points.length - 1)) * usableW;

  const steps: string[] = [];
  points.forEach((p, i) => {
    const vy = y(Number(p.price));
    if (i === 0) steps.push(`${x(i).toFixed(1)},${vy.toFixed(1)}`);
    else {
      steps.push(`${x(i).toFixed(1)},${y(values[i - 1]).toFixed(1)}`);
      steps.push(`${x(i).toFixed(1)},${vy.toFixed(1)}`);
    }
  });

  const lastX = x(points.length - 1);
  const lastY = y(values[values.length - 1]);

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
        className={s.chart}
        role="img"
        aria-label={`Price history, ${points.length} changes between ${lo.toFixed(2)} and ${hi.toFixed(2)}`}
      >
        {/* Three gridlines and a baseline, in the divider token. Neutral, so the
            data is the figure and the grid is the ground. */}
        {[0, 0.5, 1].map((f) => (
          <line
            key={f}
            x1="0"
            x2={width}
            y1={pad + plot * f}
            y2={pad + plot * f}
            className={s.chartGrid}
          />
        ))}
        <polyline points={steps.join(' ')} className={s.chartLine} vectorEffect="non-scaling-stroke" />

        {proposedNum !== null && (
          <>
            <polyline
              points={`${lastX.toFixed(1)},${lastY.toFixed(1)} ${width},${lastY.toFixed(1)} ${width},${y(proposedNum).toFixed(1)}`}
              className={s.chartProposed}
              vectorEffect="non-scaling-stroke"
            />
            <circle cx={width - 2} cy={y(proposedNum)} r="3.5" className={s.chartDot} />
          </>
        )}
        <circle cx={lastX} cy={lastY} r="3" className={s.chartDotNow} />
      </svg>
      <div className={s.chartAxis}>
        <span>{new Date(points[0].ts).toLocaleDateString([], { day: '2-digit', month: 'short' })}</span>
        <span>
          {new Date(points[Math.floor(points.length / 2)].ts).toLocaleDateString([], {
            day: '2-digit',
            month: 'short',
          })}
        </span>
        <span>Today</span>
      </div>
    </div>
  );
}

/* ── SHAP explanation ────────────────────────────────────────────────────
 * The hero of the suggestion detail screen, and the reason this project claims
 * its explanation is decision support rather than post-hoc justification.
 *
 * Diverging, with a neutral midpoint: a cool arm for factors that pushed the
 * price down and a warm arm for factors that pushed it up. The polarity IS the
 * chart's job, so the two hues read as opposite rather than as two shades. */

export function ShapLadder({
  shap,
  baseValue,
  proposedPrice,
}: {
  shap: { feature: string; value: number }[];
  baseValue: string | null;
  proposedPrice: string;
}) {
  if (!shap.length) return null;
  const widest = Math.max(...shap.map((f) => Math.abs(f.value)));
  const sum = shap.reduce((acc, f) => acc + f.value, 0);
  const base = baseValue !== null ? Number(baseValue) : null;
  // Additivity is the one property this chart depends on. If it does not hold,
  // say so rather than drawing bars that imply a decomposition they are not.
  const additive =
    base === null || Math.abs(base + sum - Number(proposedPrice)) < 0.015;

  return (
    <div className={s.shap}>
      <div className={s.shapRows}>
        {shap.map((f) => {
          const magnitude = (Math.abs(f.value) / widest) * 50;
          const up = f.value > 0;
          return (
            <div className={s.shapRow} key={f.feature}>
              <span className={s.shapFeature}>{f.feature.replace(/_/g, ' ')}</span>
              <span className={s.shapTrack}>
                <span className={s.shapAxis} aria-hidden="true" />
                <span
                  className={s.shapBar}
                  data-dir={up ? 'up' : 'down'}
                  style={
                    up
                      ? { left: '50%', width: `${magnitude}%` }
                      : { right: '50%', width: `${magnitude}%` }
                  }
                />
              </span>
              <span className={`${s.shapValue} tnum`} data-dir={up ? 'up' : 'down'}>
                {f.value > 0 ? '+' : '−'}
                {Math.abs(f.value).toFixed(2)}
              </span>
            </div>
          );
        })}
      </div>

      <div className={s.shapLegend}>
        <span>
          <span className={s.shapSwatch} data-dir="down" /> pushed the price down
        </span>
        <span>
          <span className={s.shapSwatch} data-dir="up" /> pushed it up
        </span>
      </div>

      {base !== null && (
        <p className={s.shapSum}>
          {additive ? (
            <>
              Starting from {base.toFixed(2)}, these factors sum to{' '}
              <strong>
                {sum >= 0 ? '+' : '−'}
                {Math.abs(sum).toFixed(2)}
              </strong>
              , which is the proposed {Number(proposedPrice).toFixed(2)}.
            </>
          ) : (
            <>
              These factors do not sum to the proposed price
              {` (${base.toFixed(2)} ${sum >= 0 ? '+' : '−'} ${Math.abs(sum).toFixed(2)} ≠ ${Number(proposedPrice).toFixed(2)})`}
              , so the explanation is incomplete. Treat it as indicative only.
            </>
          )}
        </p>
      )}
    </div>
  );
}
