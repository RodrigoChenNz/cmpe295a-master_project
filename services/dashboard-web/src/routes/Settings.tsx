/* 11 Settings — the store, its categories, and the link to the shelf network.
 *
 * The shelf network card is the super admin's alone: the broker is
 * infrastructure, set once by whoever installed the system, and the API leaves
 * its address out for everyone else.
 *
 * **Stored intent and running reality are two different facts, and this screen
 * shows both.** Saving a broker host records what a super admin wants; the MQTT
 * bridge keeps using its process environment until the service restarts. A form
 * that showed only the stored value would imply this service is talking to it —
 * the same failure as a shelf mirror that disagrees with the glass beside it.
 *
 * Deliberately absent: notifications (there is no mail transport anywhere in
 * this system), an agent schedule (the agent is another service and we do not
 * set its cadence), and product field packs (nothing stores a pack definition).
 * Switches for any of them would be furniture.
 */

import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '../lib/api';
import { isManagerRole, levelPath, relative } from '../lib/format';
import { Link } from 'react-router-dom';
import { useResource } from '../lib/useStream';
import { useDraft } from '../lib/useDraft';
import { usePageChrome, useShell, useStreamEvents } from '../components/AppShell';
import {
  Button, ErrorState, SkeletonRows, StatusTag, Toggle,
} from '../components/ui';
import s from './Settings.module.css';
import { STORE_PRESETS } from '../lib/storePresets';
import type { MqttTestResult, StoreType } from '../lib/types';
import { CategoryListEditor } from '../components/CategoryListEditor';
import { TimezoneSelect } from '../components/TimezoneSelect';

interface SettingsDraft extends Record<string, unknown> {
  store_name: string;
  store_code: string;
  store_type: StoreType | '';
  timezone: string;
  mqtt_host: string;
  mqtt_port: string;
  mqtt_tls: boolean;
  label_stale_secs: string;
}

export function Settings() {
  const { user, toast, reloadMeta, meta } = useShell();
  const canEdit = isManagerRole(user.role);
  const seesNetwork = user.role === 'superadmin';

  const res = useResource(() => api.settings(), []);
  useStreamEvents(['settings', 'broker'], res.reload);

  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [probe, setProbe] = useState<MqttTestResult | null>(null);

  // Memoised, and this is load-bearing rather than an optimisation: `useDraft`
  // keys its adopt-the-server-value effect on identity, so a fresh object every
  // render means that effect fires every render — setState, re-render, new
  // object, forever.
  const store = res.data?.store ?? null;
  const server: SettingsDraft | null = useMemo(
    () =>
      store
        ? {
            store_name: store.store_name ?? '',
            store_code: store.store_code ?? '',
            store_type: store.store_type ?? '',
            timezone: store.timezone,
            mqtt_host: store.mqtt_host ?? '',
            mqtt_port: String(store.mqtt_port ?? ''),
            mqtt_tls: store.mqtt_tls ?? false,
            label_stale_secs: String(store.label_stale_secs),
          }
        : null,
    [store],
  );

  const form = useDraft<SettingsDraft>(server);

  usePageChrome(
    {
      title: 'Settings',
      actions: canEdit ? (
        <>
          <Button variant="secondary" disabled={!form.dirty} onClick={form.discard}>
            Discard
          </Button>
          <Button busy={saving} disabled={!form.dirty} onClick={save}>
            Save settings
          </Button>
        </>
      ) : undefined,
    },
    [canEdit, form.dirty, saving],
  );

  // A new probe result is about the values that were in the form when it ran.
  useEffect(() => setProbe(null), [form.draft?.mqtt_host, form.draft?.mqtt_port, form.draft?.mqtt_tls]);

  async function save() {
    if (!form.draft) return;
    setSaving(true);
    try {
      await api.saveSettings({
        store_name: form.draft.store_name.trim(),
        store_code: form.draft.store_code.trim() || null,
        store_type: form.draft.store_type || null,
        timezone: form.draft.timezone.trim(),
        label_stale_secs: Number(form.draft.label_stale_secs),
      });
      // The broker has its own endpoint, super admin only; sent only when it
      // was actually edited.
      const d = form.dirtyKeys;
      if (seesNetwork && (d.has('mqtt_host') || d.has('mqtt_port') || d.has('mqtt_tls'))) {
        await api.saveNetwork({
          mqtt_host: form.draft.mqtt_host.trim(),
          mqtt_port: Number(form.draft.mqtt_port),
          mqtt_tls: form.draft.mqtt_tls,
        });
      }
      form.commit(form.draft);
      // The shell shows the store name and the broker banner, so it has to hear.
      reloadMeta();
      res.reload();
      toast('Settings saved.');
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not save the settings.', 'bad');
    } finally {
      setSaving(false);
    }
  }

  const test = async () => {
    if (!form.draft) return;
    setTesting(true);
    try {
      setProbe(
        await api.testMqtt({
          host: form.draft.mqtt_host.trim(),
          port: Number(form.draft.mqtt_port),
          tls: form.draft.mqtt_tls,
        }),
      );
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'The test could not run.', 'bad');
    } finally {
      setTesting(false);
    }
  };

  if (res.error) {
    return (
      <ErrorState
        title="Cannot load the settings"
        body={res.error}
        actions={<Button onClick={res.reload}>Try again</Button>}
      />
    );
  }
  if (res.loading || !res.data || !form.draft) return <SkeletonRows rows={4} columns={3} />;

  const broker = res.data.broker;
  const effective = res.data.effective;
  const set = form.set;

  return (
    <div className={s.page}>
      {form.serverChanged && (
        <p className={s.stale} role="status">
          Somebody else changed these settings while you were editing. Your
          changes are still here — discard them to see theirs.
        </p>
      )}

      {/* ── Store ──────────────────────────────────────────────────────── */}
      <section className={`card ${s.card}`}>
        <h2 className={s.cardTitle}>Store</h2>

        <div className={s.grid2}>
          <Field label="Store name" dirty={form.dirtyKeys.has('store_name')}>
            <input
              className="field"
              value={form.draft.store_name}
              disabled={!canEdit}
              onChange={(e) => set('store_name', e.target.value)}
            />
          </Field>
          <Field label="Store number" dirty={form.dirtyKeys.has('store_code')}>
            <input
              className={`field ${s.mono}`}
              value={form.draft.store_code}
              placeholder="04"
              disabled={!canEdit}
              onChange={(e) => set('store_code', e.target.value)}
            />
          </Field>
          <Field
            label="Store type"
            hint="Chosen during setup. It only seeded the suggested layout and pricing rules; changing it here changes neither."
            dirty={form.dirtyKeys.has('store_type')}
          >
            <select
              className="field"
              value={form.draft.store_type}
              disabled={!canEdit}
              onChange={(e) => set('store_type', e.target.value as StoreType | '')}
            >
              {!form.draft.store_type && <option value="">Not set</option>}
              {STORE_PRESETS.map((p) => (
                <option key={p.type} value={p.type}>{p.label}</option>
              ))}
            </select>
          </Field>
          <Field
            label="Timezone"
            hint="Used for the agent's daily windows and the activity log's day boundaries."
            dirty={form.dirtyKeys.has('timezone')}
          >
            <TimezoneSelect
              value={form.draft.timezone}
              disabled={!canEdit}
              onChange={(zone) => set('timezone', zone)}
            />
          </Field>
          {/* Not editable: the shared price contract (contracts/price.schema.json)
              allows USD only, and every published price carries it. */}
          <Field
            label="Currency"
            hint="Fixed by the shared price contract. Another currency needs a contract change agreed by the team."
          >
            <input className={`field ${s.mono}`} value={store?.currency ?? 'USD'} disabled readOnly />
          </Field>
        </div>

        {/* Read-only here: the levels are edited in one place, on Locations,
            where the rule about changing them while locations exist can be
            shown beside the tree it protects. */}
        <div className={s.layoutRow}>
          <span className={s.layoutLabel}>Store layout</span>
          <span className={s.layoutValue}>
            {meta?.layout.configured ? levelPath(meta.layout.levels) : 'Not set up yet'}
          </span>
          <Link to="/locations" className={s.layoutLink}>
            {meta?.layout.configured ? 'Edit in Locations' : 'Set up in Locations'}
          </Link>
        </div>
      </section>

      <CategoriesCard canEdit={canEdit} />

      {/* ── Shelf network (super admin only) ───────────────────────────── */}
      {seesNetwork && (
      <section className={`card ${s.card}`} data-down={broker.enabled && !broker.connected ? '' : undefined}>
        <div className={s.netHead}>
          <h2 className={s.cardTitle}>Shelf network (MQTT)</h2>
          <StatusTag
            status={broker.connected ? 'connected' : 'disconnected'}
            label={broker.connected ? 'Connected' : 'Disconnected'}
          />
        </div>

        <p className={s.cardSub}>
          {broker.connected
            ? 'Approved prices are published here. No label acknowledges one, so the console reports what it sent, not what a shelf is showing.'
            : `Nothing has reached a shelf since the link dropped${broker.since ? ` ${relative(broker.since)}` : ''}. Approved prices are recorded and queued.`}
        </p>

        <div className={s.grid3}>
          <Field label="Broker host" dirty={form.dirtyKeys.has('mqtt_host')}>
            <input
              className={`field ${s.mono}`}
              value={form.draft.mqtt_host}
              disabled={!canEdit}
              onChange={(e) => set('mqtt_host', e.target.value)}
            />
          </Field>
          <Field label="Port" dirty={form.dirtyKeys.has('mqtt_port')}>
            <input
              className={`field ${s.mono}`}
              inputMode="numeric"
              value={form.draft.mqtt_port}
              disabled={!canEdit}
              onChange={(e) => set('mqtt_port', e.target.value.replace(/\D/g, ''))}
            />
          </Field>
          <div className={s.testCell}>
            <Button variant="secondary" busy={testing} disabled={!canEdit} onClick={test}>
              Test connection
            </Button>
          </div>
        </div>

        <div className={s.tlsRow}>
          <Toggle
            checked={form.draft.mqtt_tls}
            disabled={!canEdit}
            onChange={(next) => set('mqtt_tls', next)}
            onLabel="TLS on"
            offLabel="TLS off"
          />
          <span className={s.tlsNote}>
            The bridge opens an encrypted connection when this is on. The broker
            has to be listening for TLS on the port above.
          </span>
        </div>

        {probe && (
          <p className={s.probe} data-ok={probe.ok || undefined} role="status">
            <strong>{probe.ok ? 'Reachable' : 'Not reachable'}</strong> —{' '}
            {probe.host}:{probe.port} in {probe.elapsed_ms}ms. {probe.detail}
            {probe.probe === 'tcp' && ' The MQTT handshake was not verified.'}
          </p>
        )}

        {/* Stored intent vs what the bridge is doing. */}
        {res.data.restart_required && (
          <p className={s.mismatch}>
            This service is connected to{' '}
            <span className={s.mono}>
              {effective?.host}:{effective?.port}
            </span>
            {effective?.tls ? ' over TLS' : ' without TLS'}. What you save here
            takes effect when the service restarts.
          </p>
        )}

        <dl className={s.netFacts}>
          <Fact label="Connected since">{broker.since ? relative(broker.since) : '—'}</Fact>
          <Fact label="Last message">
            {broker.last_message_at ? relative(broker.last_message_at) : 'Nothing yet'}
          </Fact>
          <Fact label="Queued">
            {broker.queued === 0 ? 'Nothing waiting' : `${broker.queued} to publish`}
          </Fact>
        </dl>
      </section>
      )}

      {/* ── Labels ─────────────────────────────────────────────────────── */}
      <section className={`card ${s.card}`}>
        <h2 className={s.cardTitle}>Shelf labels</h2>
        <div className={s.grid2}>
          <Field
            label="Call a label offline after"
            hint="Seconds without a sighting. E-ink labels check in every 30 minutes, so keep this above 3,600; the default, 4,500 (75 minutes), forgives one missed check-in. A label shown as offline may still be displaying the right price."
            dirty={form.dirtyKeys.has('label_stale_secs')}
          >
            <span className={s.suffixBox}>
              <input
                className={s.suffixInput}
                inputMode="numeric"
                value={form.draft.label_stale_secs}
                disabled={!canEdit}
                onChange={(e) => set('label_stale_secs', e.target.value.replace(/\D/g, ''))}
              />
              <span className={s.suffix}>seconds</span>
            </span>
          </Field>
        </div>
      </section>

      <p className={s.foot}>
        There are no notification settings here because this system has no mail
        transport, and no agent schedule because the pricing agent is a separate
        service that sets its own cadence. A switch for either would be a control
        that does nothing.
      </p>
    </div>
  );
}

function Field({
  label,
  hint,
  dirty,
  children,
}: {
  label: string;
  hint?: string;
  dirty?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={s.field}>
      <label className={s.fieldLabel}>
        {label}
        {dirty && <span className={s.dirtyMark} title="Edited, not saved" aria-label="edited" />}
      </label>
      {children}
      {hint && <span className={s.fieldHint}>{hint}</span>}
    </div>
  );
}

/** The store's category list. Saved on its own, because it is a list rather
 *  than a field and its Save says exactly what it changes. */
function CategoriesCard({ canEdit }: { canEdit: boolean }) {
  const { toast } = useShell();
  const res = useResource(() => api.categories(), []);
  useStreamEvents(['categories', 'products'], res.reload);
  const [draft, setDraft] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);

  const saved = useMemo(() => res.data?.categories.map((c) => c.name) ?? [], [res.data]);
  const counts = useMemo(
    () => Object.fromEntries((res.data?.categories ?? []).map((c) => [c.name, c.product_count])),
    [res.data],
  );
  const names = draft ?? saved;
  const dirty = draft !== null && draft.join('\u0000') !== saved.join('\u0000');

  const save = async () => {
    setSaving(true);
    try {
      await api.saveCategories(names);
      setDraft(null);
      res.reload();
      toast('Categories saved.');
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not save the categories.', 'bad');
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className={`card ${s.card}`}>
      <h2 className={s.cardTitle}>Categories</h2>
      <p className={s.cardSub}>
        What the Add product form offers. Removing one only stops it being
        suggested; products keep their category. Pricing rules per category are
        on <Link to="/rules">Rules</Link>.
      </p>
      {res.error ? (
        <p className={s.mismatch}>{res.error}</p>
      ) : (
        <CategoryListEditor names={names} counts={counts} disabled={!canEdit} onChange={setDraft} />
      )}
      {canEdit && dirty && (
        <div className={s.cardActions}>
          <Button variant="secondary" onClick={() => setDraft(null)}>Discard</Button>
          <Button busy={saving} onClick={save}>Save categories</Button>
        </div>
      )}
    </section>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={s.fact}>
      <dt className="monoLabel">{label}</dt>
      <dd className={s.factValue}>{children}</dd>
    </div>
  );
}
