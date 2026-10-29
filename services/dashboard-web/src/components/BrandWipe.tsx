/* The hand-off into the console, shared by sign-in and the end of the setup
 * wizard: the small ShelfIQ mark beside the wordmark grows until it covers
 * the screen in the accent colour, then — inside a view transition — shrinks
 * into a mark in the console's nav while the console fades in:
 *
 *   into="logo"     the ShelfIQ logo at the top (end of setup: the store is
 *                   up and running)
 *   into="account"  your avatar at the bottom (sign-in: you are in)
 *
 * The cover takes the view-transition-name of its target (AppShell gives
 * .brandMark `brand-mark` and .avatar `account-mark`), which is what lets the
 * browser morph one into the other. A browser without view transitions simply
 * swaps screens once the cover is full.
 */

import { useLayoutEffect, useState } from 'react';
import { flushSync } from 'react-dom';
import s from './BrandWipe.module.css';

/** How long the cover takes to fill the screen. Wait this long before swapping. */
export const WIPE_MS = 420;

export function prefersReducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Put the next screen up. With `morph`, inside a view transition, so the cover
 *  shrinks into the console's logo; flushSync because the transition snapshots
 *  whatever is on screen when the callback returns. */
export function swapScreen(swap: () => void, morph = true): void {
  const run = () => flushSync(swap);
  if (morph && typeof document.startViewTransition === 'function') {
    document.startViewTransition(run);
  } else {
    run();
  }
}

const TARGET_NAME = { logo: 'brand-mark', account: 'account-mark' } as const;

/** Mount to start the wipe; `from` is the mark it grows out of, `into` the
 *  nav mark it shrinks into. */
export function BrandWipe({
  from,
  into,
}: {
  from: React.RefObject<HTMLElement | null>;
  into: keyof typeof TARGET_NAME;
}) {
  const [box, setBox] = useState<{ x: number; y: number; size: number; scale: number } | null>(null);

  // Measured before paint, so the cover starts exactly on top of the mark.
  useLayoutEffect(() => {
    const el = from.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    const reach = Math.hypot(Math.max(cx, window.innerWidth - cx), Math.max(cy, window.innerHeight - cy));
    setBox({ x: r.left, y: r.top, size: r.width, scale: (reach * 2) / r.width + 1 });
  }, [from]);

  if (!box) return null;
  return (
    <span
      className={s.cover}
      aria-hidden="true"
      style={
        {
          '--x': `${box.x}px`,
          '--y': `${box.y}px`,
          '--size': `${box.size}px`,
          '--scale': box.scale,
          '--wipe-ms': `${WIPE_MS}ms`,
          '--vt-name': TARGET_NAME[into],
        } as React.CSSProperties
      }
    />
  );
}
