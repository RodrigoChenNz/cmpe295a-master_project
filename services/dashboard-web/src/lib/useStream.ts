/* The live connection to dashboard-api, and the small hooks every screen uses.
 *
 * The stream carries *hints*, never data. A `price` event means "something
 * about SKU-001 changed, refetch if you care"; it is never the only copy of the
 * new price. That is what makes a dropped socket a latency problem rather than
 * a correctness one, and it is why there is no reducer here merging event
 * payloads into screen state.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './api';
import type { StreamEvent } from './types';

const WS_URL =
  import.meta.env.VITE_WS_URL ??
  (import.meta.env.VITE_API_URL ?? 'http://localhost:8000')
    .replace(/^http/, 'ws')
    .replace(/\/$/, '') + '/ws/stream';

type Listener = (event: StreamEvent) => void;

/** Hold a socket open, reconnecting with backoff, and call `onEvent` for each
 *  message. One socket for the whole app: it lives in the shell, and screens
 *  subscribe to the events they care about through `useStreamEvent`.
 */
export function useStream(enabled: boolean, onEvent: Listener) {
  const [connected, setConnected] = useState(false);
  // The listener in a ref, so a re-render with a new closure does not tear down
  // and rebuild the socket on every keystroke in a search box.
  const listener = useRef(onEvent);
  listener.current = onEvent;

  useEffect(() => {
    if (!enabled) {
      setConnected(false);
      return;
    }

    let socket: WebSocket | null = null;
    let timer: number | undefined;
    let backoff = 1000;
    let closed = false;

    const open = () => {
      if (closed) return;
      socket = new WebSocket(WS_URL);

      socket.onopen = () => {
        backoff = 1000;
        setConnected(true);
      };

      socket.onmessage = (ev) => {
        let parsed: StreamEvent;
        try {
          parsed = JSON.parse(ev.data as string) as StreamEvent;
        } catch {
          return;
        }
        // Pings keep proxies from reaping an idle socket; they carry nothing.
        if (parsed.type === 'ping') return;
        listener.current(parsed);
      };

      socket.onclose = (ev) => {
        setConnected(false);
        // 1008 is the server saying "you are not signed in". Retrying that
        // forever would spin a socket against a door that will not open.
        if (closed || ev.code === 1008) return;
        timer = window.setTimeout(open, backoff);
        backoff = Math.min(backoff * 2, 20000);
      };

      socket.onerror = () => {
        // onclose always follows, and handles the retry. Swallowed here so the
        // console is not full of errors during a normal API restart.
      };
    };

    open();

    return () => {
      closed = true;
      if (timer) window.clearTimeout(timer);
      socket?.close();
    };
  }, [enabled]);

  return connected;
}

/* ── Data loading ─────────────────────────────────────────────────────── */

export interface Resource<T> {
  data: T | null;
  error: string | null;
  /** True only on the first load. A refetch keeps the old rows on screen
   *  rather than flashing a skeleton over data that is still correct. */
  loading: boolean;
  refreshing: boolean;
  reload: () => void;
}

/** Fetch on mount and whenever `deps` change.
 *
 * The in-flight request is tracked with a sequence number rather than
 * AbortController, because two successive filter changes must not leave the
 * slower response overwriting the faster one — aborting helps the network, but
 * the ordering guarantee is what prevents wrong rows on screen.
 */
export function useResource<T>(
  load: () => Promise<T>,
  deps: readonly unknown[],
  options: { onUnauthorized?: () => void } = {},
): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [nonce, setNonce] = useState(0);

  const seq = useRef(0);
  const hasData = useRef(false);
  const loader = useRef(load);
  loader.current = load;
  const onUnauthorized = useRef(options.onUnauthorized);
  onUnauthorized.current = options.onUnauthorized;

  useEffect(() => {
    const mine = ++seq.current;
    if (hasData.current) setRefreshing(true);
    else setLoading(true);

    loader
      .current()
      .then((result) => {
        if (mine !== seq.current) return; // a newer request has started
        setData(result);
        setError(null);
        hasData.current = true;
      })
      .catch((err: unknown) => {
        if (mine !== seq.current) return;
        if (err instanceof ApiError && err.status === 401) {
          onUnauthorized.current?.();
          return;
        }
        setError(err instanceof Error ? err.message : 'Something went wrong.');
      })
      .finally(() => {
        if (mine !== seq.current) return;
        setLoading(false);
        setRefreshing(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, refreshing, reload };
}

/** Debounce a value, for search inputs. 250ms: long enough to stop a request
 *  per keystroke, short enough that the list feels attached to the box. */
export function useDebounced<T>(value: T, delay = 250): T {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setOut(value), delay);
    return () => window.clearTimeout(t);
  }, [value, delay]);
  return out;
}

/** A ticking clock, for the header. Thirty seconds, because the header shows
 *  HH:MM and a one-second interval would re-render the whole shell sixty times
 *  for each visible change. */
export function useClock(intervalMs = 30000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(t);
  }, [intervalMs]);
  return now;
}

/** Close on Escape, and on a click outside the element. The two dismissals
 *  every menu, dropdown and the selection island share. */
export function useDismiss(
  open: boolean,
  close: () => void,
  ref?: React.RefObject<HTMLElement | null>,
) {
  useEffect(() => {
    if (!open) return;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        close();
      }
    };
    const onPointer = (e: PointerEvent) => {
      if (!ref?.current) return;
      if (!ref.current.contains(e.target as Node)) close();
    };

    document.addEventListener('keydown', onKey);
    // Capture phase, so a click on a button that also opens something else
    // closes this one first rather than racing it.
    document.addEventListener('pointerdown', onPointer, true);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onPointer, true);
    };
  }, [open, close, ref]);
}
