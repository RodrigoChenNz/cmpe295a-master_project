/* First-run store setup — the wizard between creating the super admin and the
 * console.
 *
 * Six steps, in the order the rest of the system depends on them: what kind of
 * store this is (which only seeds suggestions), its name and timezone, its
 * layout levels (no product can be added without them), its categories, the
 * team, and a review. Every step saves through the same endpoints the console
 * uses afterwards, so nothing here is a second way to configure anything.
 *
 * Deliberately absent: pricing rules (the Rules page, once there are products
 * to apply them to), the MQTT broker (infrastructure, set by whoever installed
 * the system; Settings shows it to the super admin only), and currency (the
 * shared price contract allows USD only).
 *
 * Each step saves when you move past it, and the starting step is worked out
 * from what the service already holds, so a refresh halfway through picks up
 * where it left off. Only the name and the layout are required — the service
 * refuses to finish without them.
 */

import { Fragment, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import { levelPath, ROLE_LABEL } from '../lib/format';
import { presetFor, STORE_PRESETS } from '../lib/storePresets';
import type { InviteIssued, Store, StoreType, User } from '../lib/types';
import { CategoryListEditor } from '../components/CategoryListEditor';
import { LevelsEditor } from '../components/LevelsEditor';
import { browserTimezone, TimezoneSelect } from '../components/TimezoneSelect';
import { Button, Icon } from '../components/ui';
import { BrandWipe, prefersReducedMotion, swapScreen, WIPE_MS } from '../components/BrandWipe';
import s from './SignIn.module.css';
import o from './Onboarding.module.css';
import e from '../components/ShelfDisplay.module.css';

const STEPS = ['Store type', 'Store profile', 'Layout', 'Categories', 'Team', 'Review'] as const;

const INVITE_ROLES = ['manager', 'staff', 'viewer'] as const;

interface InviteDraft {
  full_name: string;
  email: string;
  role: (typeof INVITE_ROLES)[number];
  error?: string;
}

const message = (err: unknown, fallback: string) =>
  err instanceof Error ? err.message : fallback;

export function Onboarding({
  user,
  onDone,
  onSignedOut,
}: {
  user: User;
  /** Called inside flushSync, so the console is on screen when it returns. */
  onDone: (store: Store) => void;
  onSignedOut: () => void;
}) {
  const [loadError, setLoadError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [step, setStep] = useState(0);
  /** The furthest step reached, so the stepper can tick off visited steps
   *  even after going back to an earlier one. */
  const [furthest, setFurthest] = useState(0);
  /** Which way the last move went, so the next pane slides in from that side. */
  const [dir, setDir] = useState<'forward' | 'back'>('forward');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [type, setType] = useState<StoreType | null>(null);
  const [profile, setProfile] = useState({ store_name: '', store_code: '', timezone: '' });
  // Not asked for here, but PUT /api/settings/store takes the whole row.
  const [staleSecs, setStaleSecs] = useState(4500);

  const [levels, setLevels] = useState<{ names: string[]; depthLocked: boolean } | null>(null);

  const [categories, setCategories] = useState<string[]>([]);
  /** The service already holds a list, so a change of store type leaves it. */
  const [categoriesSaved, setCategoriesSaved] = useState(false);

  /* The closing sequence, after the service has accepted Finish:
   *   1  every pill turns green, left to right
   *   2  the pills merge into one "All set"; the card becomes an e-ink label
   *      showing the store's name, which flashes and redraws like the panel
   *   3  the logo mark grows to cover the screen…
   * …and a view transition shrinks it into the console's own logo. Any key or
   * click skips straight to the console; reduced motion skips all of it. */
  const [finish, setFinish] = useState<0 | 1 | 2 | 3>(0);
  const [finishedStore, setFinishedStore] = useState<Store | null>(null);
  const timers = useRef<number[]>([]);
  const entered = useRef(false);

  const [invites, setInvites] = useState<InviteDraft[]>([]);
  const [issued, setIssued] = useState<(InviteIssued & { full_name: string; role: string })[]>([]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.settings(), api.levels(), api.categories()])
      .then(([settings, lv, cats]) => {
        if (cancelled) return;
        const store = settings.store;
        const t = store?.store_type ?? null;
        setType(t);
        // A store nobody has named yet takes its timezone from this browser
        // rather than from the schema's default.
        setProfile({
          store_name: store?.store_name ?? '',
          store_code: store?.store_code ?? '',
          timezone: store?.store_name ? store.timezone : browserTimezone(store?.timezone),
        });
        setStaleSecs(store?.label_stale_secs ?? 4500);
        setLevels(lv.configured ? { names: lv.levels.map((l) => l.name), depthLocked: lv.depth_locked } : null);
        const saved = cats.categories.map((c) => c.name);
        setCategoriesSaved(saved.length > 0);
        setCategories(saved.length ? saved : presetFor(t).categories);
        // Resume at the first required step that is not done yet.
        const resume = !store?.store_name ? 0 : !lv.configured ? 2 : 3;
        setStep(resume);
        setFurthest(resume);
        setReady(true);
      })
      .catch((err) => !cancelled && setLoadError(message(err, 'Could not load the store settings.')));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => () => timers.current.forEach(window.clearTimeout), []);

  const enterConsole = (store: Store, morph: boolean) => {
    if (entered.current) return;
    entered.current = true;
    timers.current.forEach(window.clearTimeout);
    swapScreen(() => onDone(store), morph);
  };

  const celebrate = (store: Store) => {
    if (prefersReducedMotion()) {
      enterConsole(store, false);
      return;
    }
    setFinishedStore(store);
    setFinish(1);
    const at = (ms: number, fn: () => void) => timers.current.push(window.setTimeout(fn, ms));
    at(600, () => setFinish(2));
    at(1800, () => setFinish(3));
    at(1800 + WIPE_MS + 30, () => enterConsole(store, true));
  };

  // Skippable: a key or a click goes straight in, without the morph.
  useEffect(() => {
    if (!finish || !finishedStore) return;
    const skip = () => enterConsole(finishedStore, false);
    window.addEventListener('keydown', skip);
    window.addEventListener('pointerdown', skip);
    return () => {
      window.removeEventListener('keydown', skip);
      window.removeEventListener('pointerdown', skip);
    };
    // enterConsole is guarded by a ref; re-binding on every render is noise.
  }, [finish, finishedStore]);

  const go = (next: number) => {
    setError(null);
    setDir(next > step ? 'forward' : 'back');
    setStep(next);
    setFurthest((f) => Math.max(f, next));
  };

  async function run(work: () => Promise<void>, fallback: string) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(message(err, fallback));
    } finally {
      setBusy(false);
    }
  }

  const signOut = async () => {
    try {
      await api.signOut();
    } finally {
      onSignedOut();
    }
  };

  if (loadError) {
    return (
      <Frame step={null} onSignOut={signOut} user={user}>
        <h1 className={s.heading}>Cannot load the store settings</h1>
        <p className={s.error} role="alert">{loadError}</p>
        <div className={o.actions}>
          <Button onClick={() => window.location.reload()}>Try again</Button>
        </div>
      </Frame>
    );
  }
  if (!ready) {
    return (
      <div className={s.screen}>
        <span className="srOnly">Loading the store settings</span>
      </div>
    );
  }

  const preset = presetFor(type);

  /* ── Steps ──────────────────────────────────────────────────────────── */

  let body: React.ReactNode;

  if (step === 0) {
    body = (
      <>
        <StepHead title="What kind of store is this?" />
        <div className={o.typeGrid} role="radiogroup" aria-label="Store type">
          {STORE_PRESETS.map((p) => (
            <button
              key={p.type}
              type="button"
              role="radio"
              aria-checked={type === p.type}
              className={o.typeCard}
              data-on={type === p.type || undefined}
              onClick={() => {
                setType(p.type);
                // Re-suggest only what the service does not already hold.
                if (!categoriesSaved) setCategories(p.categories);
              }}
            >
              <span className={o.typeName}>{p.label}</span>
              <span className={o.typeBlurb}>{p.blurb}</span>
            </button>
          ))}
        </div>
        <Actions>
          <Button disabled={!type} onClick={() => go(1)}>Continue</Button>
        </Actions>
      </>
    );
  } else if (step === 1) {
    const problem = !profile.store_name.trim()
      ? 'Give the store a name.'
      : !profile.timezone
        ? 'Choose a timezone.'
        : null;
    body = (
      <>
        <StepHead title="Store profile" />
        <div className={o.grid2}>
          <Field label="Store name" id="store_name">
            <input
              id="store_name"
              className={`field ${s.input}`}
              value={profile.store_name}
              maxLength={120}
              autoFocus
              placeholder="e.g. Downtown Market"
              onChange={(e) => setProfile((p) => ({ ...p, store_name: e.target.value }))}
            />
          </Field>
          <Field label="Store number" id="store_code">
            <input
              id="store_code"
              className={`field ${s.input} ${s.mono}`}
              value={profile.store_code}
              maxLength={40}
              placeholder="Optional"
              onChange={(e) => setProfile((p) => ({ ...p, store_code: e.target.value }))}
            />
          </Field>
        </div>
        <Field label="Timezone" id="timezone">
          <TimezoneSelect
            id="timezone"
            value={profile.timezone}
            onChange={(timezone) => setProfile((p) => ({ ...p, timezone }))}
          />
        </Field>
        {(error || problem) && <p className={s.error} role="alert">{error ?? problem}</p>}
        <Actions onBack={() => go(0)}>
          <Button
            busy={busy}
            aria-disabled={!!problem || undefined}
            onClick={() =>
              !problem &&
              run(async () => {
                await api.saveSettings({
                  store_name: profile.store_name.trim(),
                  store_code: profile.store_code.trim() || null,
                  store_type: type,
                  timezone: profile.timezone,
                  label_stale_secs: staleSecs,
                } as Partial<Store>);
                go(2);
              }, 'Could not save the store profile.')
            }
          >
            Save and continue
          </Button>
        </Actions>
      </>
    );
  } else if (step === 2) {
    body = (
      <>
        <StepHead title="Store layout" />
        <LevelsEditor
          compact
          onBack={() => go(1)}
          key={`${type}-${levels ? 'saved' : 'new'}`}
          initial={levels?.names ?? preset.levels}
          depthLocked={levels?.depthLocked ?? false}
          busy={busy}
          error={error}
          saveLabel="Save and continue"
          onSave={(names) =>
            run(async () => {
              const same = levels && names.join('\u0000') === levels.names.join('\u0000');
              if (!same) await api.saveLevels(names);
              setLevels({ names, depthLocked: levels?.depthLocked ?? false });
              go(3);
            }, 'Could not save the layout.')
          }
        />
      </>
    );
  } else if (step === 3) {
    body = (
      <>
        <StepHead title="Categories" />
        <CategoryListEditor names={categories} onChange={setCategories} />
        {error && <p className={s.error} role="alert">{error}</p>}
        <Actions onBack={() => go(2)}>
          <Button variant="secondary" onClick={() => go(4)}>Skip</Button>
          <Button
            busy={busy}
            aria-disabled={(!categories.length && !categoriesSaved) || undefined}
            onClick={() =>
              (categories.length > 0 || categoriesSaved) &&
              run(async () => {
                await api.saveCategories(categories);
                setCategoriesSaved(categories.length > 0);
                go(4);
              }, 'Could not save the categories.')
            }
          >
            Save and continue
          </Button>
        </Actions>
      </>
    );
  } else if (step === 4) {
    const pending = invites.filter((i) => i.full_name.trim() || i.email.trim());
    const problem = pending.some((i) => !i.full_name.trim() || !/^\S+@\S+\.\S+$/.test(i.email.trim()))
      ? 'Each invite needs a name and an email address.'
      : null;
    body = (
      <>
        <StepHead title="Invite your team" />
        {invites.map((inv, i) => (
          <div key={i} className={o.inviteRow}>
            <input
              className="field"
              placeholder="Full name"
              aria-label="Full name"
              value={inv.full_name}
              onChange={(e) =>
                setInvites((v) => v.map((x, j) => (j === i ? { ...x, full_name: e.target.value } : x)))
              }
            />
            <input
              className="field"
              type="email"
              placeholder="Email"
              aria-label="Email"
              value={inv.email}
              onChange={(e) =>
                setInvites((v) => v.map((x, j) => (j === i ? { ...x, email: e.target.value } : x)))
              }
            />
            <select
              className="field"
              aria-label="Role"
              value={inv.role}
              onChange={(e) =>
                setInvites((v) =>
                  v.map((x, j) => (j === i ? { ...x, role: e.target.value as InviteDraft['role'] } : x)),
                )
              }
            >
              {INVITE_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
            </select>
            <button
              type="button"
              className={o.remove}
              title="Remove"
              onClick={() => setInvites((v) => v.filter((_, j) => j !== i))}
            >
              ✕
            </button>
            {inv.error && <span className={o.rowError}>{inv.error}</span>}
          </div>
        ))}
        <button
          type="button"
          className={o.addRow}
          onClick={() => setInvites((v) => [...v, { full_name: '', email: '', role: 'manager' }])}
        >
          + Add a person
        </button>

        {issued.length > 0 && (
          <div className={o.issued}>
            <span className="monoLabel">Invite links · shown once</span>
            {issued.map((inv) => {
              const url = `${window.location.origin}${inv.invite_path}`;
              return (
                <div key={inv.username} className={o.issuedRow}>
                  <span className={o.issuedWho}>
                    {inv.full_name} · {ROLE_LABEL[inv.role] ?? inv.role}
                  </span>
                  <input className={`field ${s.mono}`} readOnly value={url} onFocus={(e) => e.target.select()} />
                  <Button variant="secondary" size="toolbar" onClick={() => void navigator.clipboard?.writeText(url)}>
                    Copy
                  </Button>
                </div>
              );
            })}
          </div>
        )}

        {(error || problem) && <p className={s.error} role="alert">{error ?? problem}</p>}
        <Actions onBack={() => go(3)}>
          {pending.length > 0 ? (
            <Button
              busy={busy}
              aria-disabled={!!problem || undefined}
              onClick={() =>
                !problem &&
                run(async () => {
                  const left: InviteDraft[] = [];
                  for (const inv of pending) {
                    try {
                      const res = await api.inviteUser({
                        username: usernameFrom(inv.email),
                        email: inv.email.trim(),
                        full_name: inv.full_name.trim(),
                        role: inv.role,
                      });
                      setIssued((v) => [...v, { ...res, full_name: inv.full_name.trim(), role: inv.role }]);
                    } catch (err) {
                      left.push({ ...inv, error: message(err, 'Could not invite them.') });
                    }
                  }
                  setInvites(left);
                }, 'Could not create the invites.')
              }
            >
              Create {pending.length} {pending.length === 1 ? 'invite' : 'invites'}
            </Button>
          ) : (
            <Button onClick={() => go(5)}>{issued.length ? 'Continue' : 'Skip'}</Button>
          )}
        </Actions>
      </>
    );
  } else {
    const rows: [string, React.ReactNode, number][] = [
      ['Store type', preset.label, 0],
      [
        'Store',
        `${profile.store_name}${profile.store_code ? ` · ${profile.store_code}` : ''} — ${profile.timezone}`,
        1,
      ],
      ['Layout', levels ? levelPath(levels.names.map((name, i) => ({ depth: i + 1, name }))) : 'Not set up', 2],
      ['Categories', categoriesSaved ? categories.join(', ') : 'None yet', 3],
      ['Team', issued.length ? `${issued.length} invited` : 'Nobody invited yet', 4],
    ];
    body = (
      <>
        <StepHead title="Review" />
        <dl className={o.review}>
          {rows.map(([label, value, target]) => (
            <div key={label} className={o.reviewRow}>
              <dt className="monoLabel">{label}</dt>
              <dd>{value}</dd>
              <button type="button" className={o.edit} onClick={() => go(target)}>Edit</button>
            </div>
          ))}
        </dl>
        {error && <p className={s.error} role="alert">{error}</p>}
        <Actions onBack={() => go(4)}>
          <Button
            busy={busy}
            disabled={finish > 0}
            onClick={() =>
              run(async () => {
                const { store } = await api.completeOnboarding();
                celebrate(store);
              }, 'Could not finish setup.')
            }
          >
            Finish
          </Button>
        </Actions>
      </>
    );
  }

  return (
    <Frame
      step={step}
      furthest={furthest}
      finish={finish}
      // Back to any step already reached, never past the furthest one.
      onStep={(i) => !finish && i !== step && i <= furthest && go(i)}
      onSignOut={signOut}
      user={user}
    >
      {/* Keyed per pane, so each one mounts fresh and plays its entrance. */}
      {finish >= 2 && finishedStore ? (
        <div key="ready" className={o.pane} data-dir="forward">
          <ReadyLabel store={finishedStore} />
        </div>
      ) : (
        <div key={step} className={o.pane} data-dir={dir}>
          {body}
        </div>
      )}
    </Frame>
  );
}

/* ── Waiting screen for everyone else ───────────────────────────────────── */

/** A manager or staff member who joined before the super admin finished the
 *  wizard. There is nothing useful in a console with no layout yet. */
export function SetupPending({ user, onSignedOut }: { user: User; onSignedOut: () => void }) {
  return (
    <Frame
      step={null}
      user={user}
      onSignOut={async () => {
        try {
          await api.signOut();
        } finally {
          onSignedOut();
        }
      }}
    >
      <h1 className={s.heading}>This store is still being set up</h1>
      <p className={s.lede}>
        A super admin has to finish the store setup before the console opens.
        Ask them to finish it, then sign in again.
      </p>
    </Frame>
  );
}

/* ── Pieces ─────────────────────────────────────────────────────────────── */

function Frame({
  step,
  furthest = 0,
  finish = 0,
  onStep,
  user,
  onSignOut,
  children,
}: {
  step: number | null;
  furthest?: number;
  /** The closing sequence's phase; see Onboarding. */
  finish?: number;
  onStep?: (i: number) => void;
  user: User;
  onSignOut: () => void;
  children: React.ReactNode;
}) {
  const stepperRef = useRef<HTMLOListElement>(null);
  const markRef = useRef<HTMLSpanElement>(null);
  /** How far each pill travels to reach the stepper's centre when they merge. */
  const [dx, setDx] = useState<number[]>([]);

  // Measured before the phase that uses it paints, so nothing jumps.
  useLayoutEffect(() => {
    if (finish === 1 && stepperRef.current) {
      const box = stepperRef.current.getBoundingClientRect();
      const centre = box.left + box.width / 2;
      setDx(
        [...stepperRef.current.querySelectorAll<HTMLElement>('[data-pill]')].map((el) => {
          const r = el.getBoundingClientRect();
          return centre - (r.left + r.width / 2);
        }),
      );
    }
  }, [finish]);

  return (
    <div className={`${s.screen} ${o.screen}`}>
      <div className={`${s.column} ${o.column}`}>
        <div className={o.top}>
          <div className={s.brand}>
            <span ref={markRef} className={s.brandMark} aria-hidden="true" />
            <span className={s.brandText}>ShelfIQ</span>
          </div>
          <span className={o.who}>
            {user.full_name} ·{' '}
            <button type="button" className={o.signOut} onClick={onSignOut}>Sign out</button>
          </span>
        </div>

        {step !== null && (
          <div className={o.stepperWrap}>
          <ol
            ref={stepperRef}
            className={o.stepper}
            aria-label="Setup steps"
            data-finish={finish || undefined}
            data-merged={finish >= 2 || undefined}
          >
            {STEPS.map((name, i) => {
              const current = i === step && !finish;
              // Done: every step before the furthest one reached. The furthest
              // itself is still in progress, even after going back from it.
              // Finishing ticks them all.
              const done = finish > 0 || (!current && i < furthest);
              const canOpen = !finish && !current && i <= furthest;
              const order = { '--i': i, '--dx': `${dx[i] ?? 0}px` } as React.CSSProperties;
              return (
                <Fragment key={name}>
                  {i > 0 && (
                    <li
                      className={o.bridge}
                      aria-hidden="true"
                      style={order}
                      data-filled={finish > 0 || i < furthest || current || undefined}
                    />
                  )}
                  <li className={o.stepItem} style={order}>
                    <button
                      type="button"
                      data-pill
                      className={o.pill}
                      data-state={current ? 'current' : done ? 'done' : undefined}
                      aria-current={current ? 'step' : undefined}
                      disabled={!!finish || (!current && !canOpen)}
                      onClick={() => onStep?.(i)}
                    >
                      <span className={o.pillNum}>{done ? <Icon.check /> : i + 1}</span>
                      <span className={o.stepName}>{name}</span>
                    </button>
                  </li>
                </Fragment>
              );
            })}
          </ol>
          {finish >= 2 && (
            <span className={o.allSet} role="status">
              <Icon.check />
              All set
            </span>
          )}
          </div>
        )}

        <div className={`${s.card} ${o.card}`}>{children}</div>
      </div>

      {finish === 3 && <BrandWipe from={markRef} into="logo" />}
    </div>
  );
}

/** The store's first shelf label, going live: an e-ink panel showing the
 *  store's name that flashes black and white and redraws, the way the real
 *  E213 does on a full refresh. Built from ShelfDisplay's own e-ink styles so
 *  it is the same panel the console shows everywhere else. */
function ReadyLabel({ store }: { store: Store }) {
  const today = new Date().toLocaleDateString([], { month: 'short', day: 'numeric' });
  return (
    <div className={o.ready}>
      <div className={`${e.einkBezel} ${o.readyBezel}`} style={{ '--eink-scale': 1.5 } as React.CSSProperties}>
        <div className={`${e.einkPanel} ${o.readyPanel}`}>
          <div className={e.einkTop}>
            <span className={e.einkBrand}>{store.store_code ? `Store ${store.store_code}` : 'ShelfIQ'}</span>
            <span className={e.einkPromo}>Live</span>
          </div>
          <div className={e.einkName}>{store.store_name}</div>
          <div className={e.einkBottom}>
            <span className={e.einkPrice}>Ready</span>
            <span className={e.einkMeta}>{today}</span>
          </div>
          <span className={o.refresh} aria-hidden="true" />
        </div>
      </div>
      <span className={o.readyCaption}>Opening the console</span>
    </div>
  );
}

function StepHead({ title }: { title: string }) {
  return <h1 className={s.heading}>{title}</h1>;
}

/** The button row, pinned to the bottom of the card so it does not move
 *  between steps. Back sits on the left, apart from the forward actions. */
function Actions({ onBack, children }: { onBack?: () => void; children: React.ReactNode }) {
  return (
    <div className={o.actions}>
      {onBack && (
        <Button variant="ghost" className={o.backButton} onClick={onBack}>Back</Button>
      )}
      {children}
    </div>
  );
}

function Field({ label, id, children }: { label: string; id: string; children: React.ReactNode }) {
  return (
    <div className={s.field}>
      <label className={s.label} htmlFor={id}>{label}</label>
      {children}
    </div>
  );
}

/* ── Helpers ────────────────────────────────────────────────────────────── */

/** The part of an email before the @, in the shape usernames are allowed. */
function usernameFrom(email: string): string {
  const local = email.trim().toLowerCase().split('@')[0].replace(/[^a-z0-9_.-]/g, '');
  return (local.length >= 2 ? local : `${local}user`).slice(0, 32);
}

