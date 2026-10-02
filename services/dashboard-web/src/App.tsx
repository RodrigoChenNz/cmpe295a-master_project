/* Routing and the session gate.
 *
 * `GET /api/auth/me` on load decides between the sign-in screen and the
 * console. It returns 200 with `user: null` rather than 401 for a cold start,
 * so a first visit is not treated as an error — and the three states (checking,
 * signed out, signed in) are distinct, because showing the sign-in form for a
 * frame before the session resolves is a flash nobody needs to see.
 */

import { useCallback, useEffect, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { api } from './lib/api';
import type { User } from './lib/types';
import { AppShell } from './components/AppShell';
import { SignIn } from './routes/SignIn';
import { Dashboard } from './routes/Dashboard';
import { Suggestions } from './routes/Suggestions';
import { SuggestionDetail } from './routes/SuggestionDetail';
import { Products } from './routes/Products';
import { ProductDetail } from './routes/ProductDetail';
import { Activity } from './routes/Activity';
import { NotFound, Planned } from './routes/Planned';
import s from './App.module.css';

type Session = { state: 'checking' } | { state: 'out' } | { state: 'in'; user: User };

export function App() {
  const [session, setSession] = useState<Session>({ state: 'checking' });

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then(({ user }) => {
        if (!cancelled) setSession(user ? { state: 'in', user } : { state: 'out' });
      })
      .catch(() => {
        // The service being unreachable is not the same as being signed out,
        // but from here the only useful screen is the sign-in form, which says
        // so when its own request fails.
        if (!cancelled) setSession({ state: 'out' });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signedOut = useCallback(() => setSession({ state: 'out' }), []);

  if (session.state === 'checking') {
    return (
      <div className={s.boot}>
        <span className={s.bootMark} aria-hidden="true" />
        <span className="srOnly">Checking your session</span>
      </div>
    );
  }

  if (session.state === 'out') {
    return <SignIn onSignedIn={(user) => setSession({ state: 'in', user })} />;
  }

  return (
    <BrowserRouter>
      <AppShell user={session.user} onSignedOut={signedOut}>
        <Routes>
          <Route path="/" element={<Dashboard />} />

          <Route path="/suggestions" element={<Suggestions />} />
          <Route path="/suggestions/:decisionId" element={<SuggestionDetail />} />

          <Route path="/products" element={<Products />} />
          <Route path="/products/:productId" element={<ProductDetail />} />

          <Route path="/activity" element={<Activity />} />

          {/* Second pass. In the nav because they are part of the design; each
              stub names the endpoints that already serve it. */}
          <Route path="/labels" element={<Planned which="labels" />} />
          <Route path="/labels/:labelId" element={<Planned which="labels" />} />
          <Route path="/rules" element={<Planned which="rules" />} />
          <Route path="/settings" element={<Planned which="settings" />} />
          <Route path="/users" element={<Planned which="users" />} />
          <Route path="/locations" element={<Planned which="locations" />} />

          {/* The design calls it the Dashboard; /dashboard is the address people
              type anyway. */}
          <Route path="/dashboard" element={<Navigate to="/" replace />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
