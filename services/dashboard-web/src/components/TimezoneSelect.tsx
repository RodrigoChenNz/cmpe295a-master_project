/* The store's timezone, as a searchable list rather than a text box.
 *
 * Every IANA zone the browser knows, grouped by region and labelled with its
 * current UTC offset, plus the local time there — the quickest way to notice
 * that "America/Los_Angeles" was not the store you meant. Search matches the
 * city, the region, the zone's own name ("Pacific", "Central European") and
 * the offset ("utc-7", "+5:30"), so a manager can find a zone by whatever
 * they happen to know about it. Used by the setup wizard and by Settings.
 *
 * A trigger that looks like every other field and a panel that looks like
 * every other menu, rather than a native <select>: the native one cannot be
 * searched and draws differently on every platform.
 */

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useClock, useDismiss } from '../lib/useStream';
import { Icon } from './ui';
import s from './TimezoneSelect.module.css';

interface Zone {
  id: string;
  region: string;
  place: string;
  /** "Pacific Daylight Time" — what most people actually call it. */
  name: string;
  /** "UTC−07:00", for display. */
  offset: string;
  /** "-7", "+5:30", "+0" — what an offset search is compared with. */
  offsetKey: string;
  /** Every word a search may match, lower case. */
  haystack: string;
}

let cache: Zone[] | null = null;

/** Built once per page load: ~400 zones × two formatters is cheap, but not
 *  per render or per keystroke. Sorted by region, then place. */
function allZones(): Zone[] {
  if (cache) return cache;
  let ids: string[] = [];
  try {
    ids = Intl.supportedValuesOf('timeZone');
  } catch {
    ids = [];
  }
  cache = ids
    .map((id) => {
      const [first, ...rest] = id.split('/');
      const region = rest.length ? first : 'Other';
      const place = (rest.length ? rest.join(' – ') : first).replace(/_/g, ' ');
      const offset = zonePart(id, 'longOffset');
      const name = zonePart(id, 'long');
      return {
        id,
        region,
        place,
        name: name.startsWith('GMT') ? '' : name,
        offset,
        offsetKey: offsetKey(offset.replace('±', '+')) ?? '+0',
        haystack: [id.replace(/_/g, ' '), region, place, name].join(' ').toLowerCase(),
      };
    })
    .sort((a, b) => a.region.localeCompare(b.region) || a.place.localeCompare(b.place));
  return cache;
}

function zonePart(timeZone: string, style: 'longOffset' | 'long'): string {
  try {
    const part = new Intl.DateTimeFormat('en-US', { timeZone, timeZoneName: style })
      .formatToParts(new Date())
      .find((p) => p.type === 'timeZoneName')?.value;
    if (style === 'long') return part ?? '';
    if (!part || part === 'GMT') return 'UTC±00:00';
    return part.replace('GMT', 'UTC').replace('-', '−');
  } catch {
    return style === 'long' ? '' : 'UTC';
  }
}

/** "UTC−07:00", "gmt-7", "+05:30" → "-7", "-7", "+5:30"; null for a word that
 *  is not an offset. Offsets compare whole, so "utc+1" does not find +10. */
function offsetKey(word: string): string | null {
  const m = /^(?:utc|gmt)?([+-])0?(\d{1,2})(?::?(\d{2}))?$/i.exec(word.replace(/−/g, '-'));
  if (!m) return null;
  const [, sign, hours, minutes] = m;
  const key = `${sign}${Number(hours)}${minutes && minutes !== '00' ? `:${minutes}` : ''}`;
  return key === '-0' ? '+0' : key;
}

function matches(zones: Zone[], query: string): Zone[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return zones;
  return zones.filter((z) =>
    words.every((w) => {
      const key = offsetKey(w);
      return key ? z.offsetKey === key : z.haystack.includes(w);
    }),
  );
}

/** The browser's own zone, for a store nobody has set one for yet. */
export function browserTimezone(fallback = 'UTC'): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || fallback;
  } catch {
    return fallback;
  }
}

export function TimezoneSelect({
  id,
  value,
  disabled,
  onChange,
}: {
  id?: string;
  value: string;
  disabled?: boolean;
  onChange: (zone: string) => void;
}) {
  const zones = useMemo(allZones, []);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const now = useClock(30000);

  const close = () => {
    setOpen(false);
    setQuery('');
  };
  useDismiss(open, close, wrapRef);

  const selected = zones.find((z) => z.id === value);
  const shown = useMemo(() => matches(zones, query), [zones, query]);

  // Opening puts the cursor in the search box and the current zone in view.
  useEffect(() => {
    if (!open) return;
    searchRef.current?.focus();
    const i = shown.findIndex((z) => z.id === value);
    setActive(i >= 0 ? i : 0);
    // Only on open; typing resets the highlight instead.
  }, [open]);

  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-index="${active}"]`)
      ?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  const pick = (zone: Zone) => {
    onChange(zone.id);
    close();
    triggerRef.current?.focus();
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, shown.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (shown[active]) pick(shown[active]);
    } else if (e.key === 'Tab') {
      close();
    }
  };

  let localTime = '';
  try {
    localTime = now.toLocaleTimeString([], { timeZone: value, hour: 'numeric', minute: '2-digit' });
  } catch {
    localTime = '';
  }

  return (
    <div className={s.wrap} ref={wrapRef}>
      <button
        ref={triggerRef}
        id={id}
        type="button"
        className={`field ${s.trigger}`}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => (open ? close() : setOpen(true))}
        onKeyDown={(e) => {
          if (!open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        <span className={s.triggerText}>
          {selected ? (
            <>
              <span className={s.place}>{selected.place}</span>
              <span className={s.region}>{selected.region}</span>
            </>
          ) : (
            // A stored zone this browser does not list (an alias such as
            // "US/Pacific") is shown as stored rather than silently changed.
            <span className={s.place}>{value || 'Choose a timezone'}</span>
          )}
        </span>
        {selected && <span className={s.offset}>{selected.offset}</span>}
        <span className={s.chevron} data-open={open || undefined}>
          <Icon.chevronRight />
        </span>
      </button>

      {open && (
        <div className={s.panel}>
          <span className={s.searchWrap}>
            <span className={s.searchIcon}>
              <Icon.search />
            </span>
            <input
              ref={searchRef}
              type="search"
              className={`field ${s.search}`}
              value={query}
              placeholder="Search city, region or offset, e.g. Pacific, utc-7"
              aria-label="Search timezones"
              aria-controls={listId}
              aria-activedescendant={shown[active] ? `${listId}-${active}` : undefined}
              onChange={(e) => {
                setQuery(e.target.value);
                setActive(0);
              }}
              onKeyDown={onKey}
            />
          </span>

          <div id={listId} role="listbox" className={s.list} ref={listRef} aria-label="Timezones">
            {shown.length === 0 && (
              <p className={s.empty}>No timezone matches “{query.trim()}”.</p>
            )}
            {shown.map((z, i) => (
              <div key={z.id} role="presentation">
                {(i === 0 || shown[i - 1].region !== z.region) && (
                  <div className={`monoLabel ${s.group}`} role="presentation">{z.region}</div>
                )}
                <div
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={z.id === value}
                  data-index={i}
                  data-active={i === active || undefined}
                  className={s.option}
                  onPointerMove={() => i !== active && setActive(i)}
                  onClick={() => pick(z)}
                >
                  <span className={s.check}>{z.id === value && <Icon.check />}</span>
                  <span className={s.optionText}>
                    <span className={s.place}>{z.place}</span>
                    {z.name && <span className={s.zoneName}>{z.name}</span>}
                  </span>
                  <span className={s.offset}>{z.offset}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {localTime && (
        <span className={s.now}>
          Local time there: <span className={s.time}>{localTime}</span>
        </span>
      )}
    </div>
  );
}
