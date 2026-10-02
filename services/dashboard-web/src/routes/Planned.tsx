/* The second-pass screens.
 *
 * These are in the nav because they are part of the design, and they are stubs
 * because this pass built the pricing loop. An honest stub that says what is
 * missing and what already works is more useful than a half-built screen, and
 * far more useful than a nav link that 404s.
 *
 * Each one names the API endpoints that already exist for it, because in every
 * case the backend half is done and only the screen is outstanding.
 */

import { Link } from 'react-router-dom';
import { usePageChrome } from '../components/AppShell';
import s from './Planned.module.css';

interface Spec {
  title: string;
  lead: string;
  willHave: string[];
  /** Endpoints that already work, so the next pass is screen-only. */
  apiReady: string[];
  insteadFor: string;
  insteadTo: string;
  insteadLabel: string;
}

const SPECS: Record<string, Spec> = {
  labels: {
    title: 'Shelf labels',
    lead:
      'The shelf network: every label, its position, how recently it was seen, signal, battery and firmware. Failed rows tinted red, offline rows muted.',
    willHave: [
      'A network status card with the MQTT broker’s state',
      'The label table, paginated at 25, failures first',
      'Label detail: a live render, push history and connection events',
      'Pairing a label to a product, and unpairing it',
      'Resending the current approved price to a selection',
    ],
    apiReady: [
      'GET /api/labels',
      'GET /api/labels/{label_id}',
      'POST /api/labels/{label_id}/pair',
      'POST /api/labels/resend',
    ],
    insteadFor:
      'Each product’s paired label — its status, signal, battery and firmware — is already on that product’s own page.',
    insteadTo: '/products',
    insteadLabel: 'Open Products',
  },
  rules: {
    title: 'Pricing rules',
    lead:
      'The store-wide change limits and the per-category table: floor, ceiling, minimum margin, and whether the agent may propose for that category at all.',
    willHave: [
      'Change limits: maximum move, changes per day, hours between, proposal lifetime',
      'The category table with a margin floor and an agent on/off switch per row',
      'Categories in the catalogue that have no rule yet, flagged as unconfigured',
      'Save rules / Discard changes in the header',
    ],
    apiReady: [
      'GET /api/rules',
      'PUT /api/rules/limits',
      'PUT /api/rules/categories/{category}',
    ],
    insteadFor:
      'The per-SKU version of the same policy — price band, auto-approve percentage, and excluding a product from the agent — is on each product’s page and is what the guardrail contract actually publishes.',
    insteadTo: '/products',
    insteadLabel: 'Open Products',
  },
  settings: {
    title: 'Settings',
    lead:
      'Store details, the shelf network’s broker settings, and the product field packs (Grocery, Electronics, Clothing).',
    willHave: [
      'Store name, code, timezone and currency',
      'MQTT host, port and TLS, with a connection test',
      'How long a label may go unseen before it reads as offline',
      'Field packs, and which categories each applies to',
    ],
    apiReady: ['GET /api/meta', 'PUT /api/settings/store'],
    insteadFor:
      'The broker’s live state is in the header banner whenever it is down, and on /health.',
    insteadTo: '/',
    insteadLabel: 'Back to the Dashboard',
  },
  users: {
    title: 'Users and roles',
    lead:
      'Who can do what. A manager approves prices and edits rules; staff maintain the catalogue; a viewer reads.',
    willHave: [
      'The user table with each person’s role and decision count',
      'Inviting someone, which issues a one-time token rather than a password',
      'Changing a role, with the last-manager guard',
      'Deactivating an account, which ends its sessions immediately',
    ],
    apiReady: [
      'GET /api/users',
      'POST /api/users',
      'PUT /api/users/{username}/role',
      'PUT /api/users/{username}/active',
    ],
    insteadFor:
      'Roles are already enforced everywhere: a viewer cannot approve a price, and the button says so rather than failing on click.',
    insteadTo: '/suggestions',
    insteadLabel: 'Open Price suggestions',
  },
  locations: {
    title: 'Locations',
    lead:
      'The store’s layout as a tree — zone, aisle, bay — and the slots on each bay that a label hangs in.',
    willHave: [
      'A 284px location tree beside a slot table',
      'First-run setup that asks how positions are described',
      'Moving a label from one slot to another',
    ],
    apiReady: [
      'The locations and location_slots tables are seeded and joined into /api/labels',
    ],
    insteadFor:
      'A product’s slot code is on its own page, and on every suggestion in the queue.',
    insteadTo: '/products',
    insteadLabel: 'Open Products',
  },
};

export function Planned({ which }: { which: keyof typeof SPECS }) {
  const spec = SPECS[which];
  usePageChrome({ title: spec.title }, [spec.title]);

  return (
    <div className={s.wrap}>
      <section className={`card ${s.card}`}>
        <span className={s.tag}>Not built yet</span>
        <h2 className={s.title}>{spec.title}</h2>
        <p className={s.lead}>{spec.lead}</p>

        <div className={s.cols}>
          <div className={s.col}>
            <h3 className={s.colTitle}>What this screen will have</h3>
            <ul className={s.list}>
              {spec.willHave.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>

          <div className={s.col}>
            <h3 className={s.colTitle}>Already working behind it</h3>
            <ul className={`${s.list} ${s.monoList}`}>
              {spec.apiReady.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
            <p className={s.apiNote}>
              The service half is done and tested, so this is a screen-only piece
              of work.
            </p>
          </div>
        </div>

        <div className={s.instead}>
          <p className={s.insteadText}>{spec.insteadFor}</p>
          <Link to={spec.insteadTo} className={s.insteadLink}>
            {spec.insteadLabel} →
          </Link>
        </div>
      </section>
    </div>
  );
}

export function NotFound() {
  usePageChrome({ title: 'Not found' });
  return (
    <div className={s.wrap}>
      <section className={`card ${s.card}`}>
        <h2 className={s.title}>No such page</h2>
        <p className={s.lead}>
          That address does not match any screen in this console.
        </p>
        <Link to="/" className={s.insteadLink}>
          Back to the Dashboard →
        </Link>
      </section>
    </div>
  );
}
