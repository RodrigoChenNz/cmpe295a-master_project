/* 01 Sign in.
 *
 * A 404px card on the page's paper. Nothing is prefilled: the design prototype
 * showed an email and a password in the fields, which is fine in a mockup and
 * indefensible in something that issues a real session cookie.
 */

import { useState } from 'react';
import { api, ApiError } from '../lib/api';
import type { User } from '../lib/types';
import { Button } from '../components/ui';
import s from './SignIn.module.css';

export function SignIn({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const { user } = await api.signIn(email.trim(), password);
      onSignedIn(user);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 0
          ? `Cannot reach the dashboard service. Is it running? (${api.base})`
          : err instanceof Error
            ? err.message
            : 'Sign-in failed.',
      );
      // Clear the password, not the email: a typo in one is far more likely
      // than a typo in the other.
      setPassword('');
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
          <p className={s.tagline}>
            Dynamic shelf pricing you approve before it ships to the label.
          </p>
        </div>

        {/* A real form element, so password managers recognise it and Enter
            submits without a keydown handler. */}
        <form className={s.card} onSubmit={submit}>
          <div className={s.field}>
            <label className={s.label} htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              className={`field ${s.input}`}
              value={email}
              autoComplete="username"
              autoFocus
              required
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>

          <div className={s.field}>
            <label className={s.label} htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              className={`field ${s.input}`}
              value={password}
              autoComplete="current-password"
              required
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          {error && (
            <p className={s.error} role="alert">
              {error}
            </p>
          )}

          <Button type="submit" className={s.submit} busy={busy}>
            {busy ? 'Signing in' : 'Sign in'}
          </Button>

          <div className={s.foot}>
            <span>SJSU Market · Store 04</span>
            {/* Honest about what exists: there is no mail transport in this
                system, so there is no self-service reset to link to. */}
            <span className={s.footNote}>Ask a manager for a reset</span>
          </div>
        </form>
      </div>
    </div>
  );
}
