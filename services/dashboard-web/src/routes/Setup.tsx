/* First-run setup.
 *
 * Shown instead of sign-in while the store has no accounts at all — which is
 * how `make up` and `make fresh` leave it. The account made here is the super
 * admin: it can do everything a manager can, and it is the only role that can
 * grant or remove the manager role. The service makes this work exactly once,
 * so a second visitor racing the first gets a refusal, not a second admin.
 */

import { useState } from 'react';
import { api, ApiError } from '../lib/api';
import type { User } from '../lib/types';
import { Button } from '../components/ui';
import s from './SignIn.module.css';

const MIN_PASSWORD = 10;

export function Setup({
  onSignedIn,
  onAlreadySetUp,
}: {
  onSignedIn: (user: User) => void;
  onAlreadySetUp: () => void;
}) {
  const [form, setForm] = useState({ full_name: '', email: '', username: '', password: '' });
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    if (form.password.length < MIN_PASSWORD) {
      setError(`Use at least ${MIN_PASSWORD} characters for the password.`);
      return;
    }
    if (form.password !== confirm) {
      setError('The two passwords do not match.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { user } = await api.setup({
        ...form,
        email: form.email.trim(),
        full_name: form.full_name.trim(),
      });
      onSignedIn(user);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Someone finished setup first. Their account is the super admin;
        // this person needs an invite from them.
        onAlreadySetUp();
        return;
      }
      setError(
        err instanceof ApiError && err.status === 0
          ? `Cannot reach the dashboard service. Is it running? (${api.base})`
          : err instanceof Error
            ? err.message
            : 'Setup failed.',
      );
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

        <form className={s.card} onSubmit={submit}>
          <h1 className={s.heading}>Set up this store</h1>
          <p className={s.lede}>
            Nobody has an account yet. The account you create now is the{' '}
            <strong>Super admin</strong>: it can do everything, and it invites
            everyone else.
          </p>

          <div className={s.field}>
            <label className={s.label} htmlFor="full_name">Full name</label>
            <input
              id="full_name"
              className={`field ${s.input}`}
              value={form.full_name}
              autoComplete="name"
              autoFocus
              required
              onChange={set('full_name')}
            />
          </div>

          <div className={s.field}>
            <label className={s.label} htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              className={`field ${s.input}`}
              value={form.email}
              autoComplete="email"
              required
              onChange={set('email')}
            />
            <span className={s.hint}>You sign in with this or your username.</span>
          </div>

          <div className={s.field}>
            <label className={s.label} htmlFor="username">Username</label>
            <input
              id="username"
              className={`field ${s.input} ${s.mono}`}
              value={form.username}
              autoComplete="username"
              required
              minLength={2}
              maxLength={32}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  username: e.target.value.toLowerCase().replace(/[^a-z0-9_.-]/g, ''),
                }))
              }
            />
            <span className={s.hint}>
              Lower case, 2–32 characters. It is the name on every price you approve.
            </span>
          </div>

          <div className={s.field}>
            <label className={s.label} htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              className={`field ${s.input}`}
              value={form.password}
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD}
              onChange={set('password')}
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
            {busy ? 'Creating account' : 'Create super admin'}
          </Button>
        </form>
      </div>
    </div>
  );
}
