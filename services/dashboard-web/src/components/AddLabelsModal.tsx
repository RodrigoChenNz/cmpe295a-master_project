/* Add labels: find tags that aren't set up, and give each one a product.
 *
 * A tag out of the box knows only its device ID. Starting a search opens a
 * ten-minute window in which the gateway rings every tag not yet set up, and
 * the ones that answer are listed here. Telling identical tags apart is the
 * whole difficulty, so the list leads with the one whose button was pressed
 * last: press the button on the tag in your hand and it comes to the top,
 * already selected.
 *
 * A tag becomes either the next new label, or an existing label with no tag
 * behind it (one whose tag broke, or one of the demo's), which keeps that
 * label's product, position and history.
 */

import { useEffect, useMemo, useState } from 'react';
import { api } from '../lib/api';
import { plural, relative } from '../lib/format';
import { useResource } from '../lib/useStream';
import type { FoundTag } from '../lib/types';
import { useShell, useStreamEvents } from './AppShell';
import { Battery, Button, Modal, SignalBars } from './ui';
import s from './AddLabelsModal.module.css';

/** A button press this recent means "the tag in my hand". */
const IN_HAND_MS = 60_000;

/** "80B54EDCA3D4" as "80B5 4EDC A3D4": easier to compare against the tag's screen. */
function grouped(device: string) {
  return device.match(/.{1,4}/g)?.join(' ') ?? device;
}

function inHand(tag: FoundTag, now: number) {
  return tag.button_at !== null && now - Date.parse(tag.button_at) < IN_HAND_MS;
}

export function AddLabelsModal({ onClose }: { onClose: () => void }) {
  const { toast } = useShell();
  const state = useResource(() => api.pairing(), []);
  const products = useResource(() => api.products({ page_size: 200, sort: 'name' }), []);
  const [busy, setBusy] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const [selected, setSelected] = useState<string | null>(null);
  const [target, setTarget] = useState('');   // '' = a new label, else a tagless label's id
  const [productId, setProductId] = useState('');
  const [slot, setSlot] = useState('');

  useStreamEvents(['pairing', 'labels'], state.reload);

  // The countdown, and a slow poll in case a stream event was missed.
  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 1000);
    const poll = setInterval(() => state.reload(), 5000);
    return () => {
      clearInterval(tick);
      clearInterval(poll);
    };
  }, [state.reload]);

  const data = state.data;
  const found = data?.found ?? [];
  const isOpen = !!data?.open && !!data.open_until && Date.parse(data.open_until) > now;
  const secondsLeft = data?.open_until ? Math.max(0, Math.round((Date.parse(data.open_until) - now) / 1000)) : 0;

  // A fresh button press selects that tag, whatever was selected before.
  const latestPress = useMemo(
    () => found.filter((t) => inHand(t, now)).sort((a, b) => Date.parse(b.button_at!) - Date.parse(a.button_at!))[0],
    [found, now],
  );
  useEffect(() => {
    if (latestPress) setSelected(latestPress.device_id);
  }, [latestPress?.device_id, latestPress?.button_at]);

  const chosen = found.find((t) => t.device_id === selected) ?? null;
  const takeover = data?.deviceless.find((l) => l.label_id === target) ?? null;

  const run = async (key: string, fn: () => Promise<unknown>, fail: string) => {
    setBusy(key);
    try {
      await fn();
      state.reload();
    } catch (err) {
      toast(err instanceof Error ? err.message : fail, 'bad');
    } finally {
      setBusy(null);
    }
  };

  const start = () => run('open', () => api.openPairing(10), 'Could not start searching.');
  const stop = () => run('close', () => api.closePairing(), 'Could not stop searching.');

  const assign = () =>
    run(
      'assign',
      async () => {
        const body: Parameters<typeof api.adoptTag>[0] = { device_id: chosen!.device_id };
        if (target) body.label_id = target;
        if (productId) body.product_id = productId;
        if (slot.trim()) body.slot_code = slot.trim().toUpperCase();
        const result = await api.adoptTag(body);
        toast(
          `${result.label.label_id} is set up. The tag redraws in a few seconds.`,
          'ok',
        );
        if (result.warning) toast(result.warning, 'bad');
        setSelected(null);
        setTarget('');
        setProductId('');
        setSlot('');
      },
      'Could not set up that tag.',
    );

  const productOptions = products.data?.rows ?? [];

  return (
    <Modal
      title="Add labels"
      subtitle="Find new tags and give each one a product."
      width={640}
      onClose={onClose}
      footer={
        <Button variant="secondary" onClick={onClose}>
          Done
        </Button>
      }
    >
      <div className={s.body}>
        {/* ── The search window ─────────────────────────────────────────── */}
        <div className={s.window} data-open={isOpen || undefined}>
          {isOpen ? (
            <>
              <span className={s.windowText}>
                <span className={s.pulse} aria-hidden="true" />
                Searching for tags for{' '}
                <span className="tnum">
                  {Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, '0')}
                </span>{' '}
                more
              </span>
              <Button size="toolbar" variant="ghost" busy={busy === 'close'} onClick={stop}>
                Stop
              </Button>
            </>
          ) : (
            <>
              <span className={s.windowText}>
                Tags that aren&rsquo;t set up show &ldquo;Not set up&rdquo; and their device ID.
              </span>
              <Button size="toolbar" busy={busy === 'open'} onClick={start} data-autofocus>
                Start searching (10 min)
              </Button>
            </>
          )}
        </div>

        <ol className={s.steps}>
          <li>Switch on the new tags near the gateway. They answer within a few seconds.</li>
          <li>
            Press the button marked <strong>21</strong> on the tag in your hand. It moves to the top
            of the list.
          </li>
          <li>Choose what it becomes, then stick it on the shelf. It redraws with its price.</li>
        </ol>

        {/* ── Found tags ────────────────────────────────────────────────── */}
        <section className={s.found} aria-label="Tags found">
          <div className={s.foundHead}>
            <span className="monoLabel">Found</span>
            <span className={s.count}>
              {found.length} {plural(found.length, 'tag')}
            </span>
          </div>
          {found.length === 0 ? (
            <p className={s.empty}>
              {isOpen
                ? 'No tags heard yet. A tag that was already switched on can take up to 5 minutes to answer: press its button to make it answer now.'
                : 'Start searching to list the tags around the gateway that aren’t set up.'}
            </p>
          ) : (
            <ul className={s.list}>
              {found.map((tag) => {
                const hand = inHand(tag, now);
                const active = tag.device_id === selected;
                return (
                  <li key={tag.device_id}>
                    <button
                      type="button"
                      className={s.tag}
                      data-active={active || undefined}
                      aria-pressed={active}
                      onClick={() => setSelected(active ? null : tag.device_id)}
                    >
                      <span className={s.device}>{grouped(tag.device_id)}</span>
                      {hand && <span className={s.hand}>In your hand</span>}
                      <span className={s.meta}>
                        <SignalBars rssi={tag.rssi_dbm} />
                        <Battery pct={tag.battery_pct} />
                        <span className={s.seen}>{relative(tag.last_seen_at)}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* ── What the selected tag becomes ─────────────────────────────── */}
        {chosen && (
          <section className={s.assign} aria-label={`Set up ${chosen.device_id}`}>
            <h3 className={s.assignTitle}>
              Set up <span className={s.device}>{grouped(chosen.device_id)}</span>
            </h3>

            <label className={s.label} htmlFor="adopt-target">
              It becomes
            </label>
            <select
              id="adopt-target"
              className={`field ${s.input}`}
              value={target}
              onChange={(e) => {
                setTarget(e.target.value);
                setProductId('');
              }}
            >
              <option value="">A new label</option>
              {(data?.deviceless ?? []).map((l) => (
                <option key={l.label_id} value={l.label_id}>
                  {l.label_id}
                  {l.product_name ? ` · ${l.product_name}` : ''} (no tag yet)
                </option>
              ))}
            </select>
            {takeover && (
              <span className={s.hint}>
                Keeps {takeover.label_id}&rsquo;s history
                {takeover.product_name ? `, its product (${takeover.product_name})` : ''}
                {takeover.slot_code ? ` and position ${takeover.slot_code}` : ''}. Choose a product
                below only to change it.
              </span>
            )}

            <label className={s.label} htmlFor="adopt-product">
              Product
            </label>
            <select
              id="adopt-product"
              className={`field ${s.input}`}
              value={productId}
              onChange={(e) => setProductId(e.target.value)}
            >
              <option value="">{takeover ? 'Keep its product' : 'None for now'}</option>
              {productOptions.map((p) => (
                <option
                  key={p.product_id}
                  value={p.product_id}
                  disabled={!!p.label_id && p.label_id !== target}
                >
                  {p.name}
                  {p.label_id && p.label_id !== target ? ` (on ${p.label_id})` : ''}
                </option>
              ))}
            </select>

            <label className={s.label} htmlFor="adopt-slot">
              Shelf position <span className={s.optional}>optional</span>
            </label>
            <input
              id="adopt-slot"
              className={`field ${s.input} ${s.mono}`}
              value={slot}
              placeholder={takeover?.slot_code ?? 'A3-04'}
              onChange={(e) => setSlot(e.target.value.toUpperCase())}
            />
            <span className={s.hint}>Must already exist under Locations.</span>

            <Button className={s.assignBtn} busy={busy === 'assign'} onClick={assign}>
              {target ? `Make it ${target}` : 'Make it a new label'}
            </Button>
          </section>
        )}
      </div>
    </Modal>
  );
}
