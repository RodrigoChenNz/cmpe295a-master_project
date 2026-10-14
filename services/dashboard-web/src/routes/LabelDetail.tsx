/* 08 Label detail — one device, and what the console actually knows about it.
 *
 * The honest-wording problems on this screen are the whole job:
 *
 * - The preview is captioned "read back from" the label, but nothing is read
 *   back. It is the last thing *published*, rendered by the same function the
 *   publisher uses.
 * - "Settled at", not "Confirmed at". `label_pushes.result = 'confirmed'` is
 *   written by a 45-second timeout in `ingest.settle_label_pushes`, not by an
 *   acknowledgement — there is no topic on which a label agrees it redrew.
 * - Blink reports that the message was sent or queued. Never that the label is
 *   blinking, because nothing tells us whether it is.
 */

import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import { dateTime, labelDisplayName, money, relative, isManagerRole } from '../lib/format';
import { useResource } from '../lib/useStream';
import { usePageChrome, useShell, useStreamEvents } from '../components/AppShell';
import { ShelfDisplay } from '../components/ShelfDisplay';
import {
  Battery, Button, ConfirmModal, ErrorState, SignalBars, SkeletonRows, StatusTag,
} from '../components/ui';
import s from './LabelDetail.module.css';

const EVENT_WORDS: Record<string, string> = {
  connected: 'Reported in',
  disconnected: 'Stopped reporting',
  paired: 'Paired to a product',
  unpaired: 'Unpaired',
  firmware_updated: 'Firmware updated',
  battery_low: 'Battery low',
  blink_requested: 'Identify requested',
  slot_cleared: 'Position removed',
};

export function LabelDetail() {
  const { labelId = '' } = useParams();
  const navigate = useNavigate();
  const { user, toast } = useShell();
  const canEdit = user.role !== 'viewer';
  const canPublish = isManagerRole(user.role);

  const [busy, setBusy] = useState<string | null>(null);
  const [confirmUnpair, setConfirmUnpair] = useState(false);
  const [reassignTo, setReassignTo] = useState('');
  const [slotDraft, setSlotDraft] = useState<string | null>(null);

  const res = useResource(() => api.labelDetail(labelId), [labelId]);
  const unpaired = useResource(
    () => api.products({ page_size: 100, sort: 'name' }),
    [],
  );
  useStreamEvents(['labels', 'price', 'locations'], res.reload);

  const label = res.data?.label;

  usePageChrome(
    {
      title: label ? label.label_id : 'Shelf label',
      crumb: { label: 'Shelf labels', to: '/labels' },
    },
    [label?.label_id],
  );

  if (res.error) {
    return (
      <ErrorState
        title="Cannot load this label"
        body={res.error}
        actions={<Button onClick={() => navigate('/labels')}>Back to Shelf labels</Button>}
      />
    );
  }
  if (res.loading || !res.data || !label) return <SkeletonRows rows={6} columns={3} />;

  const { pushes, events, shelf_render, as_published_at } = res.data;
  const lastPush = pushes[0] ?? null;
  const slot = slotDraft ?? label.slot_code ?? '';

  const act = async (what: string, fn: () => Promise<void>) => {
    setBusy(what);
    try {
      await fn();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Something went wrong.', 'bad');
    } finally {
      setBusy(null);
    }
  };

  const resend = () =>
    act('resend', async () => {
      const result = await api.resendLabels([label.label_id]);
      if (result.count === 0) {
        toast(result.skipped[0]?.reason ?? 'Nothing to resend.', 'bad');
      } else {
        toast(`Re-published the current price to ${label.label_id}.`);
      }
      res.reload();
    });

  const blink = () =>
    act('blink', async () => {
      const result = await api.blinkLabel(label.label_id);
      // Sent, or queued. Never "blinking".
      toast(
        result.published
          ? `Identify sent to ${label.label_id}. No label confirms a command, so watch the shelf.`
          : 'Identify queued — the shelf network is down.',
        result.published ? 'ok' : 'bad',
      );
      res.reload();
    });

  const unpair = () =>
    act('unpair', async () => {
      // Omitting slot_code deliberately: the label is still hanging where it
      // hangs, whatever it is now showing.
      await api.pairLabel(label.label_id, { product_id: null });
      toast(`${label.label_id} unpaired. It keeps position ${label.slot_code ?? '—'}.`);
      setConfirmUnpair(false);
      res.reload();
    });

  const reassign = () =>
    act('reassign', async () => {
      const result = await api.pairLabel(label.label_id, {
        product_id: reassignTo || label.product_id || null,
        slot_code: slot || null,
      });
      toast(`${label.label_id} updated and the current price re-published.`);
      // Not an error — the pairing went through — but almost always a slip.
      if (result.warning) toast(result.warning, 'bad');
      setReassignTo('');
      setSlotDraft(null);
      res.reload();
    });

  return (
    <div className={s.layout}>
      <div className={s.mainCol}>
        {/* ── The label itself ───────────────────────────────────────── */}
        <section className={`card ${s.live}`}>
          <div className={s.liveHead}>
            <h2 className={s.cardTitle}>
              {label.slot_code ? `Currently on shelf ${label.slot_code}` : 'Not on a shelf position'}
            </h2>
            <StatusTag
              status={label.status}
              label={`${statusWord(label.status)} · last seen ${relative(label.last_seen_at)}`}
            />
          </div>

          <div className={s.previewRow}>
            {shelf_render ? (
              <ShelfDisplay
                kind={label.kind}
                line1={shelf_render.line1}
                line2={shelf_render.line2}
                fullName={label.product_name ?? label.label_id}
                price={lastPush?.price ?? null}
                brand={label.brand ?? null}
                slot={label.slot_code}
                size={label.kind === 'eink' ? 'md' : 'lg'}
                publishedAt={as_published_at}
                caption="Shelf render — as published"
              />
            ) : (
              <p className={s.noRender}>
                Nothing has been published to this label, so there is nothing to
                render. Pair it to a product below.
              </p>
            )}

            <dl className={s.meta}>
              <MetaRow label="Pushed at">{dateTime(lastPush?.published_at)}</MetaRow>
              {/* Not "Confirmed at" — see the module docstring. */}
              <MetaRow label="Settled at" hint="No label acknowledges a price; this is when the push stopped being in flight.">
                {lastPush?.settled_at ? dateTime(lastPush.settled_at) : '—'}
              </MetaRow>
              <MetaRow label="Last result">
                {lastPush ? <StatusTag status={lastPush.result} /> : '—'}
              </MetaRow>
              <MetaRow label="Product">
                {label.product_id ? (
                  <a className={s.link} href={`/products/${label.product_id}`}>
                    {label.product_name}
                  </a>
                ) : (
                  <span className={s.none}>Not paired</span>
                )}
              </MetaRow>
            </dl>
          </div>

          <div className={s.actions}>
            <Button busy={busy === 'resend'} disabled={!canPublish || !label.product_id} onClick={resend}>
              Resend current price
            </Button>
            <Button
              variant="secondary"
              busy={busy === 'blink'}
              disabled={!canEdit || label.status === 'offline'}
              title={
                label.status === 'offline'
                  ? 'This label has not reported in — a command would only be queued.'
                  : undefined
              }
              onClick={blink}
            >
              Identify (blink)
            </Button>
            <Button
              variant="ghost"
              className={s.unpairBtn}
              disabled={!canEdit || !label.product_id}
              onClick={() => setConfirmUnpair(true)}
            >
              Unpair label
            </Button>
          </div>
        </section>

        {/* ── Connection history ─────────────────────────────────────── */}
        <section className={`card ${s.history}`}>
          <div className={s.cardHead}>
            <h2 className={s.cardTitle}>Connection history</h2>
            <span className={s.cardMeta}>Most recent first</span>
          </div>

          {events.length === 0 && pushes.length === 0 ? (
            <p className={s.empty}>Nothing has been recorded for this label yet.</p>
          ) : (
            <table className={s.histTable}>
              <tbody>
                {merge(events, pushes).map((row) => (
                  <tr key={row.key}>
                    <td className={s.histTime}>{dateTime(row.ts)}</td>
                    <td className={s.histEvent}>{row.text}</td>
                    <td className={s.histTag}>{row.tag && <StatusTag status={row.tag} />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>

      {/* ── Side column ────────────────────────────────────────────────── */}
      <aside className={s.sideCol}>
        <section className={`card ${s.device}`}>
          <h2 className={s.cardTitle}>Device</h2>
          <div className={s.deviceGrid}>
            <Cell label="Status">
              <StatusTag status={label.status} />
            </Cell>
            <Cell label="Hardware">{labelDisplayName(label.kind)}</Cell>
            <Cell label="Signal">
              <span className={s.inline}>
                <SignalBars rssi={label.rssi_dbm} />
                <span className={s.mono} data-bad={label.rssi_dbm !== null && label.rssi_dbm < -85 ? '' : undefined}>
                  {label.rssi_dbm === null ? '—' : `${String(label.rssi_dbm).replace('-', '−')} dBm`}
                </span>
              </span>
            </Cell>
            <Cell label="Battery">
              <Battery pct={label.battery_pct} />
            </Cell>
            <Cell label="Firmware">
              <span className={s.mono}>{label.firmware ?? '—'}</span>
            </Cell>
            <Cell label="Paired since">{label.paired_at ? relative(label.paired_at) : '—'}</Cell>
            <Cell label="Position">
              <span className={s.mono}>{label.slot_code ?? '—'}</span>
              {label.location_path && (
                <span className={s.cellSub}>{label.location_path}</span>
              )}
            </Cell>
            <Cell label="Writes today">{res.data.writes_today ?? 0}</Cell>
          </div>
        </section>

        {canEdit && (
          <section className={`card ${s.reassign}`}>
            <h2 className={s.cardTitle}>Reassign this label</h2>
            <p className={s.reassignNote}>
              The label is redrawn with the new product&rsquo;s display name and
              its current price, and that price is published again.
            </p>

            <label className={s.fieldLabel} htmlFor="reassign-product">
              Product
            </label>
            <select
              id="reassign-product"
              className={`field ${s.select}`}
              value={reassignTo || label.product_id || ''}
              onChange={(e) => setReassignTo(e.target.value)}
            >
              {label.product_id && (
                <option value={label.product_id}>{label.product_name} — current</option>
              )}
              {!label.product_id && <option value="">Choose a product…</option>}
              {(unpaired.data?.rows ?? [])
                .filter((p) => p.product_id !== label.product_id)
                .map((p) => (
                  <option key={p.product_id} value={p.product_id}>
                    {p.name}
                    {p.label_id ? ` (on ${p.label_id})` : ''}
                  </option>
                ))}
            </select>

            <label className={s.fieldLabel} htmlFor="reassign-slot">
              Shelf position
            </label>
            <input
              id="reassign-slot"
              className={`field ${s.monoInput}`}
              value={slot}
              placeholder="A3-04"
              onChange={(e) => setSlotDraft(e.target.value.toUpperCase())}
            />
            <span className={s.fieldHint}>
              Must already exist under Locations. Leave it as it is to move only
              the product.
            </span>

            <Button
              variant="secondary"
              className={s.reassignBtn}
              busy={busy === 'reassign'}
              disabled={!reassignTo && slot === (label.slot_code ?? '')}
              onClick={reassign}
            >
              Reassign and push
            </Button>
          </section>
        )}
      </aside>

      {confirmUnpair && (
        <ConfirmModal
          title={`Unpair ${label.label_id}?`}
          confirmLabel="Unpair"
          busy={busy === 'unpair'}
          onConfirm={unpair}
          onClose={() => setConfirmUnpair(false)}
          body={
            <>
              <p>
                {label.product_name} stops being tied to this label. Nothing is
                sent to the device, so it keeps showing whatever it last
                received until something else is published to it.
              </p>
              <p>
                It stays at position <strong>{label.slot_code ?? '—'}</strong> —
                a label coming off a product is still hanging on the same shelf.
              </p>
            </>
          }
        />
      )}
    </div>
  );
}

function statusWord(status: string): string {
  return (
    {
      online: 'Online',
      syncing: 'Awaiting sync',
      failed: 'Failed',
      offline: 'Offline',
      unassigned: 'Unassigned',
    }[status] ?? status
  );
}

/** Events and pushes are two tables describing one timeline, so they are shown
 *  as one, newest first. */
function merge(
  events: { event_type: string; detail: Record<string, unknown>; ts: string }[],
  pushes: { id: number; price: string; result: string; published_at: string; settled_at: string | null }[],
) {
  const rows = [
    ...events.map((e) => ({
      key: `e${e.ts}${e.event_type}`,
      ts: e.ts,
      text: EVENT_WORDS[e.event_type] ?? e.event_type.replace(/_/g, ' '),
      tag: null as string | null,
    })),
    ...pushes.map((p) => ({
      key: `p${p.id}`,
      ts: p.published_at,
      text: `Price ${money(p.price)} published`,
      tag: p.result,
    })),
  ];
  return rows.sort((a, b) => (a.ts < b.ts ? 1 : -1)).slice(0, 30);
}

function MetaRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={s.metaRow}>
      <dt className={s.metaKey} title={hint}>
        {label}
        {hint && <span className={s.metaHintMark} aria-hidden="true"> ⓘ</span>}
      </dt>
      <dd className={s.metaValue}>{children}</dd>
    </div>
  );
}

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={s.cell}>
      <span className="monoLabel">{label}</span>
      <span className={s.cellValue}>{children}</span>
    </div>
  );
}
