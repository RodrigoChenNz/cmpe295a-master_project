/* The shell: left nav, sticky header, broker banner, and the page frame.
 *
 * It owns three things no screen should own:
 *   - the single WebSocket (screens subscribe through `onStreamEvent`)
 *   - `meta`, so the nav badge and the broker banner can never disagree with
 *     each other or with the screen between them
 *   - the page title and the header's per-screen actions, which screens declare
 *     through `usePageChrome` rather than rendering a header of their own
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import { NAV_ICON } from '../lib/navIcons';
import { clock, isManagerRole, plural, relative, ROLE_LABEL } from '../lib/format';
import { useClock, useDismiss, useStream } from '../lib/useStream';
import type { Meta, StreamEvent, User } from '../lib/types';
import { Button, Icon } from './ui';
import s from './AppShell.module.css';

/* ── Chrome context ───────────────────────────────────────────────────── */

export interface PageChrome {
  title: string;
  /** Shown above the title, with a back arrow. */
  crumb?: { label: string; to: string };
  actions?: React.ReactNode;
}

interface ShellValue {
  user: User;
  meta: Meta | null;
  reloadMeta: () => void;
  setChrome: (chrome: PageChrome) => void;
  /** Subscribe to stream events. Returns an unsubscribe. */
  onStreamEvent: (fn: (e: StreamEvent) => void) => () => void;
  streamConnected: boolean;
  toast: (message: string, tone?: 'ok' | 'bad') => void;
}

const ShellContext = createContext<ShellValue | null>(null);

export function useShell(): ShellValue {
  const ctx = useContext(ShellContext);
  if (!ctx) throw new Error('useShell must be used inside AppShell');
  return ctx;
}

/** Declare this screen's title and header actions.
 *
 * `deps` exists because `actions` is JSX and therefore a new object on every
 * render; without it this effect would fire forever. Screens list what the
 * actions actually depend on.
 */
export function usePageChrome(chrome: PageChrome, deps: readonly unknown[] = []) {
  const { setChrome } = useShell();
  useEffect(() => {
    setChrome(chrome);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chrome.title, chrome.crumb?.to, setChrome, ...deps]);
}

/** Subscribe to the shell's stream. `kinds` filters by event type, so a screen
 *  does not rerender because an unrelated label went offline. */
export function useStreamEvents(
  kinds: readonly StreamEvent['type'][],
  handler: () => void,
) {
  const { onStreamEvent } = useShell();
  const cb = useRef(handler);
  cb.current = handler;
  const key = kinds.join(',');

  useEffect(() => {
    return onStreamEvent((event) => {
      // A `resync` means this client fell too far behind and dropped events,
      // so every screen refetches regardless of what it subscribed to.
      if (event.type === 'resync' || kinds.includes(event.type)) cb.current();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, onStreamEvent]);
}

/* ── Nav structure ────────────────────────────────────────────────────── */

interface NavEntry {
  to: string;
  label: string;
  icon: string;
  badge?: 'pending';
}

const NAV_MAIN: NavEntry[] = [{ to: '/', label: 'Dashboard', icon: NAV_ICON.overview }, { to: '/system', label: 'Software status', icon: NAV_ICON.activity }];

const NAV_RESOURCES: NavEntry[] = [
  { to: '/products', label: 'Products', icon: NAV_ICON.products },
  { to: '/locations', label: 'Locations', icon: NAV_ICON.locations },
  { to: '/labels', label: 'Shelf labels', icon: NAV_ICON.labels },
  { to: '/activity', label: 'Activity log', icon: NAV_ICON.activity },
  { to: '/users', label: 'Users and roles', icon: NAV_ICON.users },
];

const NAV_PRICING: NavEntry[] = [
  { to: '/suggestions', label: 'Price Suggestions', icon: NAV_ICON.suggestions, badge: 'pending' },
  { to: '/rules', label: 'Pricing Rules', icon: NAV_ICON.rules },
];

const NAV_ADMIN: NavEntry[] = [{ to: '/settings', label: 'Settings', icon: NAV_ICON.settings }];

/* ── Shell ────────────────────────────────────────────────────────────── */

export function AppShell({
  user,
  onSignedOut,
  children,
}: {
  user: User;
  onSignedOut: () => void;
  children: React.ReactNode;
}) {
  const location = useLocation();
  const now = useClock();

  const [navOpen, setNavOpen] = useState(() => {
    // Remembered, because a manager who collapsed the nav to see more of the
    // Products table does not want it back on every navigation.
    try {
      return localStorage.getItem('shelfiq.navOpen') !== 'false';
    } catch {
      return true;
    }
  });
  const [chrome, setChrome] = useState<PageChrome>({ title: 'Dashboard' });
  const [meta, setMeta] = useState<Meta | null>(null);
  const [toasts, setToasts] = useState<{ id: number; message: string; tone: 'ok' | 'bad' }[]>([]);

  /* Stream fan-out. A Set of listeners rather than one handler, so each screen
   * subscribes and unsubscribes independently of the others. */
  const listeners = useRef(new Set<(e: StreamEvent) => void>());
  const onStreamEvent = useCallback((fn: (e: StreamEvent) => void) => {
    listeners.current.add(fn);
    return () => listeners.current.delete(fn);
  }, []);

  const reloadMeta = useCallback(() => {
    api
      .meta()
      .then(setMeta)
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) onSignedOut();
      });
  }, [onSignedOut]);

  useEffect(reloadMeta, [reloadMeta]);

  const streamConnected = useStream(true, (event) => {
    // The broker's state and the nav badge both live in meta, so any event that
    // could change either refreshes it once for the whole shell.
    if (
      event.type === 'broker' ||
      event.type === 'proposal' ||
      event.type === 'suggestions' ||
      event.type === 'expired' ||
      event.type === 'labels' ||
      // The setup banner and the unlinked-products count live in meta too.
      event.type === 'locations' ||
      event.type === 'products' ||
      event.type === 'hello'
    ) {
      reloadMeta();
    }
    listeners.current.forEach((fn) => fn(event));
  });

  const toast = useCallback((message: string, tone: 'ok' | 'bad' = 'ok') => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, tone }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000);
  }, []);

  const toggleNav = () => {
    setNavOpen((open) => {
      try {
        localStorage.setItem('shelfiq.navOpen', String(!open));
      } catch {
        /* private browsing; the nav just will not be remembered */
      }
      return !open;
    });
  };

  // Changing screen scrolls to the top, per the handoff. Selections are cleared
  // by each screen's own state being keyed to the route.
  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [location.pathname]);

  const value = useMemo<ShellValue>(
    () => ({ user, meta, reloadMeta, setChrome, onStreamEvent, streamConnected, toast }),
    [user, meta, reloadMeta, onStreamEvent, streamConnected, toast],
  );

  const pending = meta?.counts.pending ?? 0;
  const brokerDown = meta ? meta.broker.enabled && !meta.broker.connected : false;

  return (
    <ShellContext.Provider value={value}>
      <div className={s.shell} data-nav={navOpen ? 'open' : 'closed'}>
        <SideNav
          open={navOpen}
          onToggle={toggleNav}
          user={user}
          pending={pending}
          onSignedOut={onSignedOut}
        />

        <main className={s.main}>
          <div className={s.stickyTop}>
            <header className={s.header}>
              <div className={s.headerInner}>
                <div className={s.titleBlock}>
                  {chrome.crumb && (
                    <Link to={chrome.crumb.to} className={s.crumb}>
                      <Icon.back />
                      {chrome.crumb.label}
                    </Link>
                  )}
                  <h1 className={s.title}>{chrome.title}</h1>
                </div>

                <div className={s.headerRight}>
                  {chrome.actions}
                  {/* Only when there is something to separate. Most screens put
                      their actions in their own toolbar, and a rule dividing
                      nothing from the store name is just a stray hairline. */}
                  {chrome.actions && <span className={s.headerDivider} aria-hidden="true" />}
                  <span className={s.store}>
                    <span className={s.storeName}>{meta?.store?.store_name ?? 'SJSU Market'}</span>
                    <span className={s.storeSep}>·</span>
                    <span className={s.clock}>{clock(now)}</span>
                  </span>
                  <AlertBell meta={meta} />
                </div>
              </div>
            </header>

            {brokerDown && <BrokerBanner meta={meta!} />}
            {meta && !meta.layout.configured && !location.pathname.startsWith('/locations') && (
              <SetupBanner canSetUp={isManagerRole(user.role)} />
            )}
            {meta?.sample_data && <SampleBanner note={meta.sample_data.note} />}
          </div>

          <div className={s.page}>{children}</div>
        </main>

        {toasts.length > 0 && (
          <div className={s.toasts} role="status" aria-live="polite">
            {toasts.map((t) => (
              <div className={s.toast} key={t.id} data-tone={t.tone}>
                <span className={s.toastDot} data-tone={t.tone} aria-hidden="true" />
                {t.message}
              </div>
            ))}
          </div>
        )}
      </div>
    </ShellContext.Provider>
  );
}

/* ── Side nav ─────────────────────────────────────────────────────────── */

function SideNav({
  open,
  onToggle,
  user,
  pending,
  onSignedOut,
}: {
  open: boolean;
  onToggle: () => void;
  user: User;
  pending: number;
  onSignedOut: () => void;
}) {
  const [acctOpen, setAcctOpen] = useState(false);
  const acctRef = useRef<HTMLDivElement>(null);
  useDismiss(acctOpen, () => setAcctOpen(false), acctRef);

  const item = (entry: NavEntry) => (
    <NavLink
      key={entry.to}
      to={entry.to}
      end={entry.to === '/'}
      className={({ isActive }) => `${s.navItem} ${isActive ? s.navItemActive : ''}`}
      // The tooltip is how a collapsed nav stays usable; it is not decoration.
      title={open ? undefined : entry.label}
    >
      <span
        className={s.navIcon}
        style={{ ['--icon' as string]: `url("${entry.icon}")` }}
        aria-hidden="true"
      />
      <span className={s.navLabel}>{entry.label}</span>
      {entry.badge === 'pending' && pending > 0 && (
        <>
          {/* Two renderings of one fact: a count when there is room for it, a
              dot when the nav is collapsed. */}
          <span className={s.navBadge}>{pending}</span>
          <span className={s.navDot} aria-hidden="true" />
          <span className="srOnly">{pending} waiting</span>
        </>
      )}
    </NavLink>
  );

  return (
    <aside className={s.nav}>
      <div className={`${s.navTop} hideScrollbar`}>
        <Link to="/" className={s.brand}>
          <span className={s.brandMark} aria-hidden="true" />
          <span className={s.brandText}>ShelfIQ</span>
        </Link>

        <nav className={s.navGroups}>
          {NAV_MAIN.map(item)}

          <span className={s.navSpacer} />
          <span className={s.navHeading}>Resources</span>
          {NAV_RESOURCES.map(item)}

          <span className={s.navSpacer} />
          <span className={s.navHeading}>Smart Pricing</span>
          {NAV_PRICING.map(item)}
        </nav>
      </div>

      <div className={s.navBottom}>
        <span className={s.navRule} aria-hidden="true" />
        {NAV_ADMIN.map(item)}
        <span className={s.navRule} aria-hidden="true" />

        <div className={s.acctWrap} ref={acctRef}>
          <button
            type="button"
            className={s.acct}
            onClick={() => setAcctOpen((o) => !o)}
            aria-expanded={acctOpen}
            aria-haspopup="menu"
            title={open ? undefined : user.full_name}
          >
            <span className={s.avatar}>{user.initials}</span>
            <span className={s.acctText}>
              <span className={s.acctName}>{user.full_name}</span>
              <span className={s.acctRole}>{roleLabel(user.role)}</span>
            </span>
            <span className={s.acctCaret} aria-hidden="true">
              ▴
            </span>
          </button>

          {acctOpen && (
            <div className={s.acctMenu} role="menu">
              <div className={s.acctMenuHead}>
                <span className={s.acctMenuName}>{user.full_name}</span>
                <span className={s.acctMenuEmail}>{user.email}</span>
              </div>
              <Link to="/settings" role="menuitem" className={s.acctItem} onClick={() => setAcctOpen(false)}>
                Account settings
              </Link>
              <Link to="/users" role="menuitem" className={s.acctItem} onClick={() => setAcctOpen(false)}>
                Users and roles
              </Link>
              <button
                type="button"
                role="menuitem"
                className={s.acctItem}
                data-destructive
                onClick={() => {
                  setAcctOpen(false);
                  // Sign out locally even if the request fails: leaving someone
                  // apparently signed in because the network blipped is worse
                  // than ending a session the server will expire anyway.
                  api.signOut().catch(() => undefined).finally(onSignedOut);
                }}
              >
                Sign out
              </button>
            </div>
          )}
        </div>

        <button
          type="button"
          className={s.collapse}
          onClick={onToggle}
          title={open ? 'Collapse the sidebar' : 'Expand the sidebar'}
          aria-label={open ? 'Collapse the sidebar' : 'Expand the sidebar'}
        >
          <span className={s.collapseIcon} aria-hidden="true">
            «
          </span>
          <span className={s.navLabel}>Collapse</span>
        </button>
      </div>
    </aside>
  );
}

function roleLabel(role: User['role']): string {
  // One map, shared with Users and the permission matrix, so the words the
  // console uses for a role cannot drift between screens.
  return ROLE_LABEL[role] ?? role;
}

/* ── Banners ──────────────────────────────────────────────────────────── */

/** The broker is down. This is the single most important thing the header can
 *  say, because approved prices are being recorded and are NOT on any shelf. */
function BrokerBanner({ meta }: { meta: Meta }) {
  const navigate = useNavigate();
  const { queued, since, error } = meta.broker;
  return (
    <div className={s.brokerBanner} role="alert">
      <span className={s.brokerDot} aria-hidden="true" />
      <span className={s.brokerText}>
        Shelf network unreachable{since ? ` since ${relative(since)}` : ''} — approved prices
        are recorded and queued, and will not reach any shelf label until it is back.
      </span>
      {queued > 0 && (
        <span className={s.brokerCount}>
          {queued} {plural(queued, 'change')} queued
        </span>
      )}
      <span className={s.brokerActions}>
        {error && <span className={s.brokerError} title={error}>{error}</span>}
        <Button size="sm" variant="ghost" onClick={() => navigate('/settings')}>
          Broker settings
        </Button>
      </span>
    </div>
  );
}

/** A new store: the layout's levels come before anything else, and products
 *  cannot be added until they exist. Not dismissible, because it describes a
 *  requirement rather than a notice. Hidden on Locations, which is where it
 *  points and which shows the setup itself. */
function SetupBanner({ canSetUp }: { canSetUp: boolean }) {
  const navigate = useNavigate();
  return (
    <div className={s.setupBanner} role="status">
      <span className={s.setupTag}>Setup</span>
      <span className={s.setupText}>
        {canSetUp
          ? 'Set up your store layout — its levels, such as Zone › Aisle › Bay — before adding locations and products.'
          : 'A manager is still setting up this store’s layout. Products can be added once that is done.'}
      </span>
      {canSetUp && (
        <Button size="sm" onClick={() => navigate('/locations')}>
          Set up layout
        </Button>
      )}
    </div>
  );
}

/** While the seed is in place, every figure on screen is sample data. Saying so
 *  in the chrome is the difference between a prototype and a misleading one. */
function SampleBanner({ note }: { note: string }) {
  const [dismissed, setDismissed] = useState(() => {
    try {
      return sessionStorage.getItem('shelfiq.sampleDismissed') === 'true';
    } catch {
      return false;
    }
  });
  if (dismissed) return null;
  return (
    <div className={s.sampleBanner}>
      <span className={s.sampleTag}>Sample data</span>
      <span className={s.sampleText}>{note}</span>
      <button
        type="button"
        className={s.sampleClose}
        onClick={() => {
          setDismissed(true);
          try {
            // Per session, not forever: a new tab says it again, because the
            // claim stops being true the moment real data arrives.
            sessionStorage.setItem('shelfiq.sampleDismissed', 'true');
          } catch {
            /* ignore */
          }
        }}
        aria-label="Dismiss"
      >
        <Icon.cross />
      </button>
    </div>
  );
}

/* ── Alert bell ───────────────────────────────────────────────────────── */

function AlertBell({ meta }: { meta: Meta | null }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const navigate = useNavigate();
  useDismiss(open, () => setOpen(false), ref);

  const counts = meta?.counts;
  const alerts = [
    counts?.pending
      ? {
          tone: 'wait' as const,
          title: `${counts.pending} ${plural(counts.pending, 'suggestion')} waiting`,
          detail: 'The shelf keeps its current price if one lapses undecided.',
          to: '/suggestions',
        }
      : null,
    counts?.labels_failed
      ? {
          tone: 'bad' as const,
          title: `${counts.labels_failed} ${plural(counts.labels_failed, 'label')} did not take the last price`,
          detail: 'A customer may be seeing the old number.',
          to: '/labels?status=failed',
        }
      : null,
    counts?.labels_offline
      ? {
          tone: 'none' as const,
          title: `${counts.labels_offline} ${plural(counts.labels_offline, 'label')} not seen recently`,
          detail: 'They are showing whatever they last received.',
          to: '/labels?status=offline',
        }
      : null,
    counts?.out_of_stock
      ? {
          tone: 'bad' as const,
          title: `${counts.out_of_stock} ${plural(counts.out_of_stock, 'product')} out of stock`,
          detail: 'Priced on a shelf with nothing on it.',
          to: '/products?stock=out',
        }
      : null,
  ].filter((a): a is NonNullable<typeof a> => a !== null);

  const urgent = (counts?.labels_failed ?? 0) + (counts?.out_of_stock ?? 0);

  return (
    <span className={s.bellWrap} ref={ref}>
      <button
        type="button"
        className={s.bell}
        title="Alerts"
        aria-label={`Alerts${alerts.length ? `, ${alerts.length} items` : ''}`}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <Icon.bell />
        {urgent > 0 && <span className={s.bellCount}>{urgent}</span>}
      </button>

      {open && (
        <div className={s.bellPanel}>
          <div className={s.bellHead}>Needs your attention</div>
          {alerts.length === 0 ? (
            <div className={s.bellEmpty}>
              You&rsquo;re all caught up. Every label is confirmed and the queue is clear.
            </div>
          ) : (
            alerts.map((a) => (
              <button
                type="button"
                key={a.title}
                className={s.bellItem}
                onClick={() => {
                  setOpen(false);
                  navigate(a.to);
                }}
              >
                <span className={s.bellItemDot} data-tone={a.tone} aria-hidden="true" />
                <span className={s.bellItemText}>
                  <span className={s.bellItemTitle}>{a.title}</span>
                  <span className={s.bellItemDetail}>{a.detail}</span>
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </span>
  );
}
