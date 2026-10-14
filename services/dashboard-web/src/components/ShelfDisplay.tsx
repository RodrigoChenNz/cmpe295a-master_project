/* The shelf-label previews — the bridge between software and hardware made
 * visible, and the component that carries the most weight in this interface.
 *
 * Two genuinely different devices, not two skins:
 *
 *   lcd   16x2 character LCD. Sixteen cells per line, a visible unlit-cell
 *         ground, and a hard cut at 16 characters because that is what the
 *         firmware does. The per-cell rendering is not decoration: it doubles
 *         as a spec Het can check his output against, and it is the reason the
 *         character counter can be trusted.
 *
 *   eink  Heltec Vision Master E213, 250x122 e-ink on an ESP32. A pixel
 *         display, so it is NOT bound by 16 characters — it gets the full
 *         product name, the unit price and the slot code.
 *
 * ── The caption is load-bearing ───────────────────────────────────────────
 * It says "as published", never "what the shelf is showing now". The dashboard
 * and the edge node are independent MQTT subscribers and there is no
 * acknowledgement topic in the contract, so a label can be displaying a stale
 * price out of flash while this preview shows the current one. At a booth, a
 * mirror that disagrees with the physical LCD beside it is the worst failure
 * available — so the component states what it actually knows.
 */

import type { LabelKind } from '../lib/types';
import { money, relative } from '../lib/format';
import s from './ShelfDisplay.module.css';

export type ShelfSize = 'sm' | 'md' | 'lg';

interface Props {
  kind: LabelKind;
  /** LCD line 1 — the 16-character display name. */
  line1: string;
  /** LCD line 2 — price and reason, composed by the API's render_lines(). */
  line2: string;
  /** E-ink fields. Ignored for an LCD, which has no room for any of them. */
  fullName?: string;
  price?: string | null;
  was?: string | null;
  unitPrice?: string | null;
  brand?: string | null;
  slot?: string | null;
  size?: ShelfSize;
  /** When this render was published. Shown beside the caption, because
   *  "as published" is only meaningful with a time attached. */
  publishedAt?: string | null;
  caption?: string;
  showHardwareTag?: boolean;
  showCounter?: boolean;
}

const HARDWARE_LABEL: Record<LabelKind, string> = {
  lcd: '16×2 LCD',
  eink: 'Heltec Vision Master E213 · e-ink',
};

const CELL: Record<ShelfSize, { w: number; h: number; fs: number }> = {
  sm: { w: 11, h: 17, fs: 11 },
  md: { w: 14, h: 22, fs: 14 },
  lg: { w: 20, h: 32, fs: 20 },
};

/** Sixteen cells, always. A short string pads with unlit cells rather than
 *  collapsing, because the physical display does not shrink to fit its
 *  content and a preview that does would misrepresent it. */
function cells(text: string): string[] {
  const raw = (text ?? '').slice(0, 16).toUpperCase();
  return Array.from({ length: 16 }, (_, i) => raw[i] ?? ' ');
}

export function ShelfDisplay({
  kind,
  line1,
  line2,
  fullName,
  price,
  was,
  unitPrice,
  brand,
  slot,
  size = 'md',
  publishedAt,
  caption,
  showHardwareTag = true,
  showCounter = true,
}: Props) {
  const over = line1.length > 16 || line2.length > 16;

  return (
    <div className={s.wrap}>
      {showHardwareTag && <span className={s.hardware}>{HARDWARE_LABEL[kind]}</span>}

      {kind === 'lcd' ? (
        <LcdPreview line1={line1} line2={line2} size={size} />
      ) : (
        <EinkPreview
          name={fullName ?? line1}
          price={price}
          was={was}
          unitPrice={unitPrice}
          brand={brand}
          slot={slot}
          size={size}
        />
      )}

      {showCounter && (
        <div className={s.footer}>
          {/* The default caption, not an override: every call site that does
              not set one still tells the truth about what this is. */}
          <span>{caption ?? 'Shelf render — as published'}</span>
          {kind === 'lcd' ? (
            <span className={over ? s.countOver : undefined}>
              {line1.length}/16 · {line2.length}/16
            </span>
          ) : (
            <span>{publishedAt ? relative(publishedAt) : '250×122'}</span>
          )}
        </div>
      )}

      {publishedAt && kind === 'lcd' && (
        <span className={s.publishedAt}>Published {relative(publishedAt)}</span>
      )}
    </div>
  );
}

/* ── 16x2 character LCD ───────────────────────────────────────────────── */

function LcdPreview({ line1, line2, size }: { line1: string; line2: string; size: ShelfSize }) {
  const cell = CELL[size];
  const style = {
    '--cell-w': `${cell.w}px`,
    '--cell-h': `${cell.h}px`,
    '--cell-fs': `${cell.fs}px`,
  } as React.CSSProperties;

  return (
    <div className={s.lcd} style={style}>
      {[line1, line2].map((line, row) => (
        <div className={s.lcdRow} key={row}>
          {cells(line).map((ch, i) => (
            // aria-hidden on the cells and one readable string below, so a
            // screen reader hears "MILK ORGANIC 1L" rather than fifteen
            // separate letters.
            <span className={s.lcdCell} key={i} aria-hidden="true">
              {ch === ' ' ? ' ' : ch}
            </span>
          ))}
        </div>
      ))}
      <span className="srOnly">
        Shelf label shows: {line1.trim()}, {line2.trim()}
      </span>
    </div>
  );
}

/* ── Heltec Vision Master E213 ────────────────────────────────────────── */

function EinkPreview({
  name,
  price,
  was,
  unitPrice,
  brand,
  slot,
  size,
}: {
  name: string;
  price?: string | null;
  was?: string | null;
  unitPrice?: string | null;
  brand?: string | null;
  slot?: string | null;
  size: ShelfSize;
}) {
  // The panel is 250x122 physical pixels. Scaling the whole thing by one factor
  // keeps the proportions honest instead of reflowing a different layout at
  // each size, which is what the hardware does too.
  const scale = size === 'sm' ? 0.8 : size === 'lg' ? 1.4 : 1;
  const style = { '--eink-scale': scale } as React.CSSProperties;

  return (
    <div className={s.einkBezel} style={style}>
      <div className={s.einkPanel}>
        <div className={s.einkTop}>
          <span className={s.einkBrand}>{brand || ' '}</span>
          {was && <span className={s.einkPromo}>Reduced</span>}
        </div>

        <div className={s.einkName}>{name}</div>

        <div className={s.einkBottom}>
          <span className={s.einkPriceGroup}>
            <span className={s.einkPrice}>{money(price)}</span>
            {was && <span className={s.einkWas}>{money(was)}</span>}
          </span>
          <span className={s.einkMeta}>
            {unitPrice && <span>{unitPrice}</span>}
            {slot && <span className={s.einkSlot}>{slot}</span>}
          </span>
        </div>
      </div>
    </div>
  );
}
