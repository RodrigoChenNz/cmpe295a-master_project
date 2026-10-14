/* A dirty-tracked form over a server value.
 *
 * Pricing rules is the only screen in this console with Save and Discard in the
 * header, so this pattern is new rather than copied from a sibling.
 *
 * Two rules it has to encode, both of which are bugs if missed:
 *
 * 1. **A refetch must not clobber an edit in progress.** The shell pushes a
 *    `rules` stream event on every save, including the user's own, so this
 *    screen refetches while someone may be halfway through typing. A new server
 *    value is adopted as the baseline only when the form is clean; when it is
 *    dirty the draft is kept and `serverChanged` goes true so the screen can
 *    say someone else changed them. That is DESIGN.md rule 7 ("refetch keeps
 *    the old rows") applied to a form.
 *
 * 2. **Leaving with unsaved work warns.** `beforeunload` while dirty.
 *
 * Comparison is per key rather than a deep stringify, because the values arrive
 * as strings from some columns and numbers from others, and because `dirtyKeys`
 * is what drives the per-cell "edited" marker.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export interface Draft<T> {
  draft: T | null;
  /** The last value adopted from the server. */
  baseline: T | null;
  dirty: boolean;
  dirtyKeys: Set<string>;
  /** True when the server moved underneath an edit in progress. */
  serverChanged: boolean;
  set: <K extends keyof T>(key: K, value: T[K]) => void;
  patch: (part: Partial<T>) => void;
  discard: () => void;
  /** Adopt a value as the new baseline — call after a successful save. */
  commit: (next: T) => void;
}

export function useDraft<T extends Record<string, unknown>>(
  server: T | null,
): Draft<T> {
  const [baseline, setBaseline] = useState<T | null>(server);
  const [draft, setDraft] = useState<T | null>(server);
  const [serverChanged, setServerChanged] = useState(false);

  // Read inside the effect without making it a dependency: adopting a new
  // server value depends on whether the form is dirty *now*, and listing the
  // draft would re-run this on every keystroke.
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const baselineRef = useRef(baseline);
  baselineRef.current = baseline;

  useEffect(() => {
    if (server === null) return;
    // Callers must memoise `server`; a fresh object each render would make this
    // effect fire on every render. Bailing out when nothing actually differs
    // turns that mistake into wasted work rather than an infinite loop.
    if (baselineRef.current !== null && same(server, baselineRef.current)) {
      if (draftRef.current !== null && same(draftRef.current, baselineRef.current)) return;
    }
    const current = draftRef.current;
    const base = baselineRef.current;
    const isDirty = current !== null && base !== null && !same(current, base);

    if (!isDirty) {
      setBaseline(server);
      setDraft(server);
      setServerChanged(false);
    } else if (base !== null && !same(server, base)) {
      // Someone else saved while this form was being edited. Keep the edit and
      // say so, rather than silently discarding either side.
      setServerChanged(true);
    }
  }, [server]);

  const dirtyKeys = useMemo(() => {
    const keys = new Set<string>();
    if (!draft || !baseline) return keys;
    for (const k of Object.keys(draft)) {
      if (!Object.is(draft[k], baseline[k])) keys.add(k);
    }
    return keys;
  }, [draft, baseline]);

  const dirty = dirtyKeys.size > 0;

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const set = useCallback(<K extends keyof T>(key: K, value: T[K]) => {
    setDraft((d) => (d === null ? d : { ...d, [key]: value }));
  }, []);

  const patch = useCallback((part: Partial<T>) => {
    setDraft((d) => (d === null ? d : { ...d, ...part }));
  }, []);

  const discard = useCallback(() => {
    setDraft(baseline);
    setServerChanged(false);
  }, [baseline]);

  const commit = useCallback((next: T) => {
    setBaseline(next);
    setDraft(next);
    setServerChanged(false);
  }, []);

  return { draft, baseline, dirty, dirtyKeys, serverChanged, set, patch, discard, commit };
}

function same<T extends Record<string, unknown>>(a: T, b: T): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) {
    if (!Object.is(a[k], b[k])) return false;
  }
  return true;
}
