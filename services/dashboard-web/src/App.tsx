/* Routing and the session gate.
 *
 * `GET /api/auth/me` on load decides between the sign-in screen and the
 * console. It returns 200 with `user: null` rather than 401 for a cold start,
 * so a first visit is not treated as an error — and the three states (checking,
 * signed out, signed in) are distinct, because showing the sign-in form for a
 * frame before the session resolves is a flash nobody needs to see.
 *
 * Signed out has three screens: /join?token=… accepts an invite, a store with
 * no accounts at all gets first-run setup, and everything else gets sign-in.
 */

import { useCallback, useEffect, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { api } from './lib/api';
import type { User } from './lib/types';
import { AppShell } from './components/AppShell';
import { SignIn } from './routes/SignIn';
import { Setup } from './routes/Setup';
import { Join } from './routes/Join';
import { System } from './routes/System';
import { Dashboard } from './routes/Dashboard';
import { Suggestions } from './routes/Suggestions';
import { SuggestionDetail } from './routes/SuggestionDetail';
import { Products } from './routes/Products';
import { ProductDetail } from './routes/ProductDetail';
import { Activity } from './routes/Activity';
import { Labels } from './routes/Labels';
import { LabelDetail } from './routes/LabelDetail';
import { Locations } from './routes/Locations';
import { Rules } from './routes/Rules';
import { Settings } from './routes/Settings';
import { Users } from './routes/Users';
import { NotFound } from './routes/Planned';
import s from './App.module.css';

type Session =
  | { state: 'checking' }
  | { state: 'out'; setupRequired: boolean }
  | { state: 'in'; user: User };

/** The invite token, if this page was opened from an invite link. */
function joinToken(): string | null {
  if (window.location.pathname !== '/join') return null;
  return new URLSearchParams(window.location.search).get('token') ?? '';
}

export function App() {
  const [session, setSession] = useState<Session>({ state: 'checking' });

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then(({ user, setup_required }) => {
        if (!cancelled)
          setSession(user ? { state: 'in', user } : { state: 'out', setupRequired: setup_required });
      })
      .catch(() => {
        // The service being unreachable is not the same as being signed out,
        // but from here the only useful screen is the sign-in form, which says
        // so when its own request fails.
        if (!cancelled) setSession({ state: 'out', setupRequired: false });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signedOut = useCallback(() => setSession({ state: 'out', setupRequired: false }), []);
  const [token, setToken] = useState(joinToken);

  // Setup and join both end signed in at the console's front door. Dropping
  // the /join URL also keeps the token out of the address bar and history.
  const enter = useCallback((user: User) => {
    if (window.location.pathname === '/join') window.history.replaceState(null, '', '/');
    setToken(null);
    setSession({ state: 'in', user });
  }, []);

  if (session.state === 'checking') {
    return (
      <div className={s.boot}>
        <span className={s.bootMark} aria-hidden="true" />
        <span className="srOnly">Checking your session</span>
      </div>
    );
  }

  if (session.state === 'out') {
    if (token !== null) {
      return (
        <Join
          token={token}
          onSignedIn={enter}
          onSignIn={() => {
            window.history.replaceState(null, '', '/');
            setToken(null);
          }}
        />
      );
    }
    if (session.setupRequired) {
      return (
        <Setup
          onSignedIn={enter}
          onAlreadySetUp={() => setSession({ state: 'out', setupRequired: false })}
        />
      );
    }
    return <SignIn onSignedIn={(user) => setSession({ state: 'in', user })} />;
  }

  return (
    <BrowserRouter>
      <AppShell user={session.user} onSignedOut={signedOut}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/system" element={<System />} />

          <Route path="/suggestions" element={<Suggestions />} />
          <Route path="/suggestions/:decisionId" element={<SuggestionDetail />} />

          <Route path="/products" element={<Products />} />
          <Route path="/products/:productId" element={<ProductDetail />} />

          <Route path="/activity" element={<Activity />} />

          <Route path="/labels" element={<Labels />} />
          <Route path="/labels/:labelId" element={<LabelDetail />} />
          <Route path="/locations" element={<Locations />} />

          <Route path="/rules" element={<Rules />} />
          <Route path="/users" element={<Users />} />
          <Route path="/settings" element={<Settings />} />

          {/* The design calls it the Dashboard; /dashboard is the address people
              type anyway. */}
          <Route path="/dashboard" element={<Navigate to="/" replace />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
