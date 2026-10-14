/* Accept an invite: /join?token=…
 *
 * The link a manager or super admin copied from the Users screen. The person
 * it is for picks their own password here and is signed in; nobody else ever
 * knows it. The token works once — the service deletes it as soon as it is
 * used — and expires after a week.
 */

import { useEffect, useState } from 'react';
import { api, ApiError } from '../lib/api';
import { ROLE_LABEL } from '../lib/format';
import type { InviteInfo, User } from '../lib/types';
import { Button } from '../components/ui';
import s from './SignIn.module.css';

const MIN_PASSWORD = 10;

type Lookup =
  | { state: 'loading' }
  | { state: 'ok'; invite: InviteInfo }
  | { state: 'bad'; message: string };

export function Join({
  token,
  onSignedIn,
  onSignIn,
}: {
  token: string;
  onSignedIn: (user: User) => void;
  /** Leave for the ordinary sign-in screen. */
  onSignIn: () => void;
}) {
  const [lookup, setLookup] = useState<Lookup>({ state: 'loading' });
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setLookup({ state: 'bad', message: 'This link has no invite in it. Check that it was copied whole.' });
      return;
    }
    api
      .invite(token)
      .then((invite) => !cancelled && setLookup({ state: 'ok', invite }))
      .catch((err) => {
        if (cancelled) return;
        setLookup({
          state: 'bad',
          message:
            err instanceof ApiError && err.status === 0
              ? `Cannot reach the dashboard service. Is it running? (${api.base})`
              : err instanceof Error
                ? err.message
                : 'This invite link cannot be used.',
        });
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    if (password.length < MIN_PASSWORD) {
      setError(`Use at least ${MIN_PASSWORD} characters for the password.`);
      return;
    }
    if (password !== confirm) {
      setError('The two passwords do not match.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { user } = await api.acceptInvite(token, password);
      onSignedIn(user);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not finish joining.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={s.screen}>
      <div className={s.column}>
        <div className={s.brandBlock}>
          <div className={s.brand}>
            <span className={s.brandMark} aria-hidden="true" />
            <span className={s.brandText}>ShelfIQ</span>
          </div>
        </div>

        {lookup.state === 'loading' ? (
          <div className={s.card}>
            <p className={s.lede}>Checking your invite…</p>
          </div>
        ) : lookup.state === 'bad' ? (
          <div className={s.card}>
            <h1 className={s.heading}>This invite cannot be used</h1>
            <p className={s.error} role="alert">{lookup.message}</p>
            <p className={s.lede}>
              Ask whoever invited you to send a new link from the Users screen.
            </p>
            <Button variant="secondary" onClick={onSignIn}>Go to sign in</Button>
          </div>
        ) : (
          <form className={s.card} onSubmit={submit}>
            <h1 className={s.heading}>Welcome, {lookup.invite.full_name}</h1>
            <p className={s.lede}>
              You have been invited as{' '}
              <strong>{ROLE_LABEL[lookup.invite.role] ?? lookup.invite.role}</strong>.
              Choose a password; you will sign in with{' '}
              <strong>{lookup.invite.email}</strong>.
            </p>

            {/* Lets a password manager file the new password under the right
                account. */}
            <input
              type="email"
              value={lookup.invite.email}
              autoComplete="username"
              readOnly
              hidden
            />

            <div className={s.field}>
              <label className={s.label} htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                className={`field ${s.input}`}
                value={password}
                autoComplete="new-password"
                autoFocus
                required
                minLength={MIN_PASSWORD}
                onChange={(e) => setPassword(e.target.value)}
              />
              <span className={s.hint}>At least {MIN_PASSWORD} characters.</span>
            </div>

            <div className={s.field}>
              <label className={s.label} htmlFor="confirm">Confirm password</label>
              <input
                id="confirm"
                type="password"
                className={`field ${s.input}`}
                value={confirm}
                autoComplete="new-password"
                required
                onChange={(e) => setConfirm(e.target.value)}
              />
            </div>

            {error && (
              <p className={s.error} role="alert">
                {error}
              </p>
            )}

            <Button type="submit" className={s.submit} busy={busy}>
              {busy ? 'Joining' : 'Join'}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
