/* 02 Dashboard.
 *
 * Three panels, then two. No subtitle under any panel title, and no primary
 * action in the header — both per the handoff, which removed them.
 *
 * The thing to hold onto here: every number on this screen comes from a row in
 * the database, and anything with nothing to measure renders as an em dash
 * rather than a zero. The pricing-health circle carries its own definition in a
 * tooltip, because a composite score nobody can define is just decoration.
 */

import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { dateTime, int, money, pct, pctPlain, plural, relative, timeOnly } from '../lib/format';
import { useResource } from '../lib/useStream';
import { usePageChrome, useShell, useStreamEvents } from '../components/AppShell';
import { levelPath } from '../lib/format';
import type { Meta } from '../lib/types';
import { Button, ErrorState, Sparkline, SkeletonRows, StatusTag } from '../components/ui';
import s from './Dashboard.module.css';

const RANGES = [7, 30, 90] as const;

export function Dashboard() {
  const navigate = useNavigate();
  const [days, setDays] = useState(30);

  usePageChrome({ title: 'Dashboard' });

  const res = useResource(() => api.dashboard(days), [days]);
  useStreamEvents(['price', 'proposal', 'suggestions', 'expired', 'labels'], res.reload);

  if (res.error) {
    return (
      <ErrorState
        title="Cannot load the dashboard"
        body={res.error}
        actions={<Button onClick={res.reload}>Try again</Button>}
      />
    );
  }
  if (res.loading || !res.data) return <SkeletonRows rows={5} columns={4} />;

  const { snapshot, pricing_health, attention, signals, recent } = res.data;

  // The ring. A null score draws an empty track rather than a full one, because
  // "we cannot compute this" and "this is perfect" must not look the same.
  const circumference = 2 * Math.PI * 62;
  const filled = pricing_health.score !== null ? (pricing_health.score / 100) * circumference : 0;

  return (
    <>
      <GettingStarted />
      <div className={s.topRow}>
        {/* ── Store snapshot ───────────────────────────────────────────── */}
        <section className={`card ${s.snapshot}`}>
          <h2 className={s.panelTitle}>Store snapshot</h2>

          <div className={s.ring} title={pricing_health.definition}>
            <svg viewBox="0 0 148 148" width="148" height="148" className={s.ringSvg}>
              <circle cx="74" cy="74" r="62" className={s.ringTrack} />
              <circle
                cx="74"
                cy="74"
                r="62"
                className={s.ringFill}
                strokeDasharray={`${filled} ${circumference}`}
              />
            </svg>
            <div className={s.ringCentre}>
              <span className={s.ringScore}>
                {pricing_health.score === null ? '—' : pricing_health.score}
              </span>
              <span className={s.ringLabel}>Pricing health</span>
            </div>
          </div>

          {/* The score's parts, spelled out. A composite with no visible
              components is something nobody can argue with, which makes it
              useless as a management figure. */}
          <p className={s.ringDefinition}>{pricing_health.definition}</p>

          <div className={s.metrics}>
            <MetricRow
              label="Labels online"
              value={`${int(snapshot.labels_online)} / ${int(snapshot.labels_paired)}`}
              note={
                snapshot.labels_failed > 0
                  ? `${snapshot.labels_failed} ${plural(snapshot.labels_failed, 'label')} did not take the last price`
                  : snapshot.labels_offline > 0
                    ? `${snapshot.labels_offline} not seen recently`
                    : 'Every paired label is reporting'
              }
              onClick={() => navigate('/labels')}
            />
            <MetricRow
              label="Waiting on you"
              value={int(snapshot.pending)}
              note={
                snapshot.pending === 0
                  ? 'The queue is clear'
                  : `${plural(snapshot.pending, 'suggestion')} to approve, override or reject`
              }
              onClick={() => navigate('/suggestions')}
            />
            <MetricRow
              label="Catalogue margin"
              value={pctPlain(snapshot.margin_pct)}
              note="Weighted by units on hand"
              onClick={() => navigate('/products')}
            />
          </div>
        </section>

        {/* ── What needs your attention ────────────────────────────────── */}
        <section className={`card ${s.attention}`}>
          <div className={s.attentionHead}>
            <h2 className={s.panelTitleLg}>What needs your attention</h2>
            <span className={s.attentionCount}>
              {attention.length === 0
                ? 'Nothing right now'
                : `${attention.length} ${plural(attention.length, 'item')}`}
            </span>
          </div>

          {attention.length === 0 ? (
            <div className={s.attentionEmpty}>
              Nothing needs you right now. Every label is confirmed and the queue is clear.
            </div>
          ) : (
            attention.map((item) => (
              <div className={s.attentionRow} key={item.kind}>
                <span className={s.attentionN} data-priority={item.priority}>
                  {item.count}
                </span>
                <div className={s.attentionText}>
                  <span className={s.attentionTitleRow}>
                    <span className={s.priority} data-priority={item.priority}>
                      {item.priority}
                    </span>
                    <span className={s.attentionTitle}>{item.title}</span>
                  </span>
                  <span className={s.attentionDetail}>
                    {item.detail_at && (
                      <>
                        {item.detail_at_prefix ?? 'At'}{' '}
                        <strong>{dateTime(item.detail_at)}</strong>
                        {' · '}
                      </>
                    )}
                    {item.detail}
                  </span>
                </div>
                <Button size="sm" onClick={() => navigate(item.route)}>
                  {item.action}
                </Button>
              </div>
            ))
          )}
        </section>

        {/* ── Performance signals ──────────────────────────────────────── */}
        <section className={`card ${s.signals}`}>
          <div className={s.signalsHead}>
            <h2 className={s.panelTitle}>Performance signals</h2>
            {/* One select drives all three, per the handoff. */}
            <select
              className={`field ${s.rangeSelect}`}
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              aria-label="Time range for all three signals"
            >
              {RANGES.map((d) => (
                <option key={d} value={d}>
                  {d} days
                </option>
              ))}
            </select>
          </div>

          {signals.map((sig) => (
            <div className={s.signal} key={sig.key}>
              <span className={s.signalLabel}>{sig.label}</span>
              <span className={s.signalValue}>
                {sig.value === null
                  ? '—'
                  : sig.unit === 'percent'
                    ? pctPlain(sig.value)
                    : int(sig.value)}
              </span>
              <Sparkline points={sig.points} />
              <span className={s.signalNote}>{sig.note}</span>
            </div>
          ))}

          {/* Said out loud, because the obvious question about a pricing
              dashboard with no revenue line is "where is revenue". */}
          <p className={s.signalsFoot}>
            There is no sales or revenue series here: this system has no
            point-of-sale feed, so there is nothing real to plot.
          </p>
        </section>
      </div>

      {/* ── Recent price changes ───────────────────────────────────────── */}
      <div className={s.bottomRow}>
        <section className={`card ${s.recent}`}>
          <div className={s.cardHead}>
            <h2 className={s.cardTitle}>Recent price changes</h2>
            <Link to="/activity" className={s.viewAll}>
              View all
            </Link>
          </div>

          {recent.length === 0 ? (
            <div className={s.recentEmpty}>No price has reached a shelf yet.</div>
          ) : (
            recent.map((row) => (
              <Link
                to={`/products/${row.product_id}`}
                className={s.recentRow}
                key={`${row.product_id}-${row.ts}`}
              >
                <span className={s.recentTime}>{timeOnly(row.ts)}</span>
                <span className={s.recentText}>
                  <span className={s.recentName}>{row.product_name}</span>
                  <span className={s.recentPrices}>
                    <s className={s.recentOld}>{money(row.old_price)}</s> → {money(row.new_price)}
                    {row.change_pct !== null && (
                      <span
                        className={s.recentPct}
                        data-dir={row.change_pct < 0 ? 'down' : 'up'}
                      >
                        {pct(row.change_pct)}
                      </span>
                    )}
                    {row.slot_code && <span className={s.recentSlot}>· {row.slot_code}</span>}
                  </span>
                </span>
                <span className={s.recentWho}>
                  {row.approval === 'auto'
                    ? 'Your standing policy'
                    : row.approval === 'override'
                      ? `${row.approved_by_name ?? row.approved_by} · edited price`
                      : `${row.approved_by_name ?? row.approved_by} · as suggested`}
                </span>
                {/* The label's result, not the decision's. These are different
                    facts: an approved price can fail to reach a shelf. */}
                <StatusTag
                  status={row.shelf_result ?? 'unassigned'}
                  label={row.shelf_result === null ? 'No label' : undefined}
                />
              </Link>
            ))
          )}
        </section>

        <section className={`card ${s.queue}`}>
          <div className={s.cardHead}>
            <h2 className={s.cardTitle}>Delegation</h2>
            <Link to="/rules" className={s.viewAll}>
              Pricing Rules
            </Link>
          </div>
          <div className={s.delegation}>
            <p className={s.delegationLead}>
              {snapshot.pending === 0
                ? 'Nothing is waiting on you.'
                : `${snapshot.pending} ${plural(snapshot.pending, 'recommendation')} ${snapshot.pending === 1 ? 'needs' : 'need'} a decision.`}
            </p>
            <dl className={s.delegationStats}>
              <div>
                <dt>Labels reporting</dt>
                <dd>
                  {pricing_health.label_health_pct === null
                    ? '—'
                    : `${pricing_health.label_health_pct}%`}
                </dd>
              </div>
              <div>
                <dt>Queue drag on the score</dt>
                <dd>{pricing_health.queue_drag_pct}%</dd>
              </div>
              <div>
                <dt>Products out of stock</dt>
                <dd>{int(snapshot.out_of_stock)}</dd>
              </div>
              <div>
                <dt>Products low on stock</dt>
                <dd>{int(snapshot.low_stock)}</dd>
              </div>
            </dl>
            <p className={s.delegationNote}>
              A product with no auto-approve percentage always asks you, however
              small the move. Widening that is how the queue gets shorter —
              it is a delegation of authority, so it is deliberate.
            </p>
            <Button variant="secondary" onClick={() => navigate('/rules')}>
              Review pricing rules
            </Button>
          </div>
        </section>
      </div>

      {res.refreshing && <span className="srOnly">Updating</span>}
      <p className={s.asOf}>
        Updated {relative(new Date().toISOString())} · live over the shelf network
      </p>
    </>
  );
}

function MetricRow({
  label,
  value,
  note,
  onClick,
}: {
  label: string;
  value: string;
  note: string;
  onClick: () => void;
}) {
  return (
    <button type="button" className={s.metric} onClick={onClick}>
      <span className="monoLabel">{label}</span>
      <span className={s.metricValue}>{value}</span>
      <span className={s.metricNote}>{note}</span>
    </button>
  );
}

/** A new store's setup, in the order it has to happen: the layout's levels,
 *  then locations, then products, then linking them. Shown until every step is
 *  done, then never again — it is a checklist, not a feature. */
function GettingStarted() {
  const { meta } = useShell();
  if (!meta) return null;
  const steps = setupSteps(meta);
  if (steps.every((x) => x.done)) return null;
  const next = steps.find((x) => !x.done);

  return (
    <section className={`card ${s.setup}`}>
      <div className={s.setupHead}>
        <h2 className={s.panelTitle}>Getting started</h2>
        <span className={s.setupProgress}>
          {steps.filter((x) => x.done).length} of {steps.length} done
        </span>
      </div>
      <ol className={s.setupList}>
        {steps.map((step, i) => (
          <li key={step.title} className={s.setupStep} data-done={step.done || undefined}>
            <span className={s.setupMark} aria-hidden="true">{step.done ? '✓' : i + 1}</span>
            <span className={s.setupText}>
              <span className={s.setupTitle}>{step.title}</span>
              <span className={s.setupNote}>{step.note}</span>
            </span>
            {!step.done && (
              <Link
                to={step.to}
                className={s.setupLink}
                data-next={step === next || undefined}
              >
                {step.action}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function setupSteps(meta: Meta) {
  const { layout, counts } = meta;
  return [
    {
      title: 'Name your layout’s levels',
      note: layout.configured
        ? levelPath(layout.levels)
        : 'For example Zone › Aisle › Bay, or Floor › Zone › Aisle › Bay.',
      done: layout.configured,
      to: '/locations',
      action: 'Set up layout',
    },
    {
      title: 'Add your locations',
      note: counts.locations
        ? `${counts.locations} ${plural(counts.locations, 'location')} in the layout`
        : 'Build the tree, from the top level down.',
      done: counts.locations > 0,
      to: '/locations',
      action: 'Add locations',
    },
    {
      title: 'Add your products',
      note: counts.products
        ? `${counts.products} ${plural(counts.products, 'product')} in the catalogue`
        : 'Enter the catalogue; locations can be linked afterwards.',
      done: counts.products > 0,
      to: '/products',
      action: 'Add products',
    },
    {
      title: 'Link products to locations',
      note:
        counts.products === 0
          ? 'Once products exist, place each one in the layout.'
          : counts.unassigned_products
            ? `${counts.unassigned_products} ${plural(counts.unassigned_products, 'product')} not linked yet`
            : 'Every product has a location',
      done: counts.products > 0 && counts.unassigned_products === 0,
      to: '/products?location=unassigned',
      action: 'Link products',
    },
  ];
}
