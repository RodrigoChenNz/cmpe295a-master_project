/* 01 Sign in.
 *
 * A 404px card on the page's paper. Nothing is prefilled: the design prototype
 * showed an email and a password in the fields, which is fine in a mockup and
 * indefensible in something that issues a real session cookie.
 *
 * Submitting folds the form into one loading bar while the credentials are
 * checked: the two fields, Remember me and the button collapse into a single
 * field-sized "Verifying" bar, and the card keeps its height so nothing else
 * on the page moves. A refusal unfolds it again with the error. Success plays
 * a short exit, about a second, because it happens every day: "Welcome back,
 * <name>", then the brand wipe carries you into the console and lands on your
 * avatar in the nav (see BrandWipe).
 * The exit runs while the app is still asking where you go next, so it costs
 * almost no extra time. Any key or click skips it; reduced motion goes
 * straight in.
 */

import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../lib/api';
import type { StoreBrand, User } from '../lib/types';
import { Button } from '../components/ui';
import { BrandWipe, prefersReducedMotion, swapScreen, WIPE_MS } from '../components/BrandWipe';
import s from './SignIn.module.css';

/** The loading bar stays at least this long, so a fast answer does not make
 *  the form flicker shut and open. */
const VERIFY_MIN_MS = 650;
const WELCOME_MS = 480;
const REMEMBER_KEY = 'shelfiq.rememberMe';

const wait = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

/** The checkbox's last state, as a per-browser convenience. Storage can be
 *  unavailable (private windows, blocked site data); then it starts unticked. */
function readRemember(): boolean {
  try {
    return window.localStorage.getItem(REMEMBER_KEY) === '1';
  } catch {
    return false;
  }
}
function writeRemember(on: boolean): void {
  try {
    window.localStorage.setItem(REMEMBER_KEY, on ? '1' : '0');
  } catch {
    /* Not worth telling anyone about. */
  }
}

export function SignIn({
  store,
  onSignedIn,
}: {
  /** Null on a store that has not been named yet. */
  store: StoreBrand | null;
  /** Starts working out where this user goes; resolves to the swap that puts
   *  it on screen. */
  onSignedIn: (user: User) => Promise<() => void>;
}) {
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(readRemember);
  const [error, setError] = useState<string | null>(null);

  /** 0 the form; 1 checking the credentials; 2 "Welcome back"; 3 the brand wipe. */
  const [phase, setPhase] = useState<0 | 1 | 2 | 3>(0);
  const [greeting, setGreeting] = useState('');
  /** The form's height when it was submitted, so the loading bar and the
   *  greeting keep the card the same size instead of letting the column jump. */
  const [cardHeight, setCardHeight] = useState<number | undefined>();
  const formRef = useRef<HTMLFormElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const markRef = useRef<HTMLSpanElement>(null);
  const skipRef = useRef<() => void>(() => {});
  const skipped = useRef(false);

  // Skipping is only for the exit, not while the credentials are checked.
  useEffect(() => {
    if (phase < 2) return;
    const skip = () => skipRef.current();
    window.addEventListener('keydown', skip);
    window.addEventListener('pointerdown', skip);
    return () => {
      window.removeEventListener('keydown', skip);
      window.removeEventListener('pointerdown', skip);
    };
  }, [phase]);

  /** The exit. The swap waits for both the animation and the app's answer;
   *  a skip cuts the animation short but still has to wait for the answer. */
  const leave = async (user: User) => {
    const ready = onSignedIn(user);
    if (prefersReducedMotion()) {
      swapScreen(await ready, false);
      return;
    }
    const skip = new Promise<void>((resolve) => {
      skipRef.current = () => {
        skipped.current = true;
        resolve();
      };
    });
    const step = (ms: number) => Promise.race([wait(ms), skip]);

    setGreeting(firstName(user.full_name));
    setPhase(2);
    await step(WELCOME_MS);
    if (!skipped.current) {
      setPhase(3);
      await step(WIPE_MS + 30);
    }
    swapScreen(await ready, !skipped.current);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (phase) return;
    setError(null);
    writeRemember(remember);
    setCardHeight(formRef.current?.offsetHeight);
    setPhase(1);

    // Both outcomes wait out the minimum, so a refusal unfolds the form as
    // smoothly as success moves on.
    const [result] = await Promise.allSettled([
      api.signIn(login.trim(), password, remember),
      wait(prefersReducedMotion() ? 0 : VERIFY_MIN_MS),
    ]);

    if (result.status === 'fulfilled') {
      await leave(result.value.user);
      return;
    }
    const err = result.reason;
    setError(
      err instanceof ApiError && err.status === 0
        ? `Cannot reach the dashboard service. Is it running? (${api.base})`
        : err instanceof Error
          ? err.message
          : 'Sign-in failed.',
    );
    // Clear the password, not the login: a typo in one is far more likely
    // than a typo in the other.
    setPassword('');
    setPhase(0);
  };

  // After a refusal, back to the password once the form has unfolded (it is
  // inert, and so cannot take focus, until this render commits).
  useEffect(() => {
    if (phase === 0 && error) passwordRef.current?.focus();
  }, [phase, error]);

  const checking = phase === 1;

  return (
    <div className={s.screen}>
      <div className={s.column}>
        <div className={s.brandBlock}>
          <div className={s.brand}>
            <span ref={markRef} className={s.brandMark} aria-hidden="true" />
            <span className={s.brandText}>ShelfIQ</span>
          </div>
          <p className={s.tagline}>
            Dynamic shelf pricing you approve before it ships to the label.
          </p>
        </div>

        {phase >= 2 ? (
          <div className={`${s.card} ${s.welcome}`} style={{ minHeight: cardHeight }} role="status">
            <span className={s.welcomeLabel}>Signed in</span>
            <span className={s.welcomeName}>Welcome back, {greeting}</span>
          </div>
        ) : (
          /* A real form element, so password managers recognise it and Enter
             submits without a keydown handler. It stays mounted while the
             credentials are checked, so a refusal keeps what was typed. */
          <form
            ref={formRef}
            className={`${s.card} ${s.signInCard}`}
            data-checking={checking || undefined}
            style={checking ? { minHeight: cardHeight } : undefined}
            aria-busy={checking || undefined}
            onSubmit={submit}
          >
            <Fold open={!checking}>
              <div className={s.field}>
                <label className={s.label} htmlFor="login">
                  Email or username
                </label>
                <input
                  id="login"
                  type="text"
                  className={`field ${s.input}`}
                  value={login}
                  autoComplete="username"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  autoFocus
                  required
                  onChange={(e) => setLogin(e.target.value)}
                />
              </div>
            </Fold>

            <Fold open={!checking}>
              <div className={s.field}>
                <label className={s.label} htmlFor="password">
                  Password
                </label>
                <input
                  ref={passwordRef}
                  id="password"
                  type="password"
                  className={`field ${s.input}`}
                  value={password}
                  autoComplete="current-password"
                  required
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
            </Fold>

            {/* Where the two fields meet as they fold: one bar in their place. */}
            <Fold open={checking} bare>
              <div className={s.verifying} role="status">
                <span className={s.verifyingText}>Verifying</span>
                <span className={s.verifyingTrack} aria-hidden="true" />
              </div>
            </Fold>

            <Fold open={!checking}>
              <label className={s.remember}>
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                />
                Remember me
              </label>
            </Fold>

            {error && (
              <Fold open={!checking}>
                <p className={s.error} role="alert">
                  {error}
                </p>
              </Fold>
            )}

            <Fold open={!checking}>
              <Button type="submit" className={s.submit} disabled={checking}>
                Sign in
              </Button>
            </Fold>

            <Fold open={!checking}>
              <div className={s.foot}>
                <span>
                  {store ? [store.name, store.code].filter(Boolean).join(' · ') : 'ShelfIQ'}
                </span>
                {/* Honest about what exists: there is no mail transport in this
                    system, so there is no self-service reset to link to. */}
                <span className={s.footNote}>Ask a manager for a reset</span>
              </div>
            </Fold>
          </form>
        )}
      </div>

      {phase === 3 && <BrandWipe from={markRef} into="account" />}
    </div>
  );
}

/** A row that folds to nothing (height and opacity) when closed. A grid row
 *  animating between 1fr and 0fr, because height cannot animate to `auto`.
 *  Closed rows are inert, so nothing folded away can be tabbed into. `bare`
 *  drops the spacing above, for the bar that takes the fields' place. */
function Fold({ open, bare, children }: { open: boolean; bare?: boolean; children: React.ReactNode }) {
  return (
    <div className={s.fold} data-open={open || undefined} data-bare={bare || undefined} inert={!open}>
      <div className={s.foldInner}>{children}</div>
    </div>
  );
}

function firstName(fullName: string): string {
  return fullName.trim().split(/\s+/)[0] || fullName;
}
