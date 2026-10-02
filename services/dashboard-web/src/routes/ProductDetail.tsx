/* 06 Product detail. Two columns, minmax(0,1fr) minmax(0,452px).
 *
 * The 16-character display-name field with its live shelf preview is the piece
 * that matters: it is where a hardware constraint becomes visible to the person
 * who can violate it. The counter is not decoration — past sixteen characters
 * the firmware silently drops the rest, so the label would show a truncated,
 * misleading name and nobody would know until they walked the aisle.
 *
 * The price field is separate from Save, deliberately. Saving the catalogue row
 * is an edit; setting the price publishes to a shelf and lands in the audit log
 * with a name against it. Those are different actions and they get different
 * buttons.
 */

import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import { dateTime, money, pctPlain, relative } from '../lib/format';
import { useResource } from '../lib/useStream';
import { usePageChrome, useShell, useStreamEvents } from '../components/AppShell';
import { ShelfDisplay } from '../components/ShelfDisplay';
import {
  Battery, Button, CategoryPill, ErrorState, MarginPill, PriceHistoryChart,
  SignalBars, SkeletonRows, StatusTag, StockCell,
} from '../components/ui';
import s from './ProductDetail.module.css';

interface Draft {
  name: string;
  display_name: string;
  sku: string;
  upc: string;
  brand: string;
  supplier: string;
  category: string;
  unit_cost: string;
  stock_on_hand: string;
  min_price: string;
  max_price: string;
  auto_approve_pct: string;
  agent_excluded: boolean;
}

export function ProductDetail() {
  const { productId = '' } = useParams();
  const navigate = useNavigate();
  const { user, toast } = useShell();
  const canEdit = user.role !== 'viewer';
  const canPrice = user.role === 'manager';

  const res = useResource(() => api.product(productId), [productId]);
  useStreamEvents(['products', 'price', 'proposal', 'labels'], res.reload);

  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [priceDraft, setPriceDraft] = useState('');
  const [reasonTag, setReasonTag] = useState('Manager set');
  const [pricing, setPricing] = useState(false);

  const product = res.data?.product;

  // Reset the draft whenever the record changes underneath, including after a
  // save. A stale draft silently overwriting someone else's edit is the failure
  // mode here, and this is the cheap version of avoiding it.
  useEffect(() => {
    if (!product) return;
    setDraft({
      name: product.name,
      display_name: product.display_name ?? '',
      sku: product.sku ?? '',
      upc: product.upc ?? '',
      brand: product.brand ?? '',
      supplier: product.supplier ?? '',
      category: product.category ?? '',
      unit_cost: product.unit_cost,
      stock_on_hand: product.stock_on_hand === null ? '' : String(product.stock_on_hand),
      min_price: product.min_price ?? '',
      max_price: product.max_price ?? '',
      auto_approve_pct:
        product.auto_approve_pct === null ? '' : String(product.auto_approve_pct),
      agent_excluded: product.agent_excluded,
    });
    setPriceDraft(product.current_price ?? '');
  }, [product?.product_id, product?.priced_at, product?.name]);

  usePageChrome(
    {
      title: product?.name ?? 'Product',
      crumb: { label: 'Products', to: '/products' },
    },
    [product?.name],
  );

  if (res.error) {
    return (
      <ErrorState
        title="Cannot load this product"
        body={res.error}
        actions={<Button onClick={() => navigate('/products')}>Back to Products</Button>}
      />
    );
  }
  if (res.loading || !res.data || !product || !draft) return <SkeletonRows rows={6} columns={3} />;

  const { price_history, decisions, label, fields } = res.data;

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((d) => (d ? { ...d, [key]: value } : d));

  const nameLen = draft.display_name.length;
  const nameOver = nameLen > 16;

  // What the label will actually show, computed the same way the API does so
  // the preview and the published strings cannot disagree.
  const previewLine1 = (draft.display_name || draft.name).toUpperCase().slice(0, 16);
  const previewLine2 = composeLine2(priceDraft || product.current_price, reasonTag);

  const dirty =
    draft.name !== product.name ||
    draft.display_name !== (product.display_name ?? '') ||
    draft.sku !== (product.sku ?? '') ||
    draft.upc !== (product.upc ?? '') ||
    draft.brand !== (product.brand ?? '') ||
    draft.supplier !== (product.supplier ?? '') ||
    draft.category !== (product.category ?? '') ||
    draft.unit_cost !== product.unit_cost ||
    draft.stock_on_hand !== (product.stock_on_hand === null ? '' : String(product.stock_on_hand)) ||
    draft.min_price !== (product.min_price ?? '') ||
    draft.max_price !== (product.max_price ?? '') ||
    draft.auto_approve_pct !==
      (product.auto_approve_pct === null ? '' : String(product.auto_approve_pct)) ||
    draft.agent_excluded !== product.agent_excluded;

  const policyChanged =
    draft.min_price !== (product.min_price ?? '') ||
    draft.max_price !== (product.max_price ?? '') ||
    draft.auto_approve_pct !==
      (product.auto_approve_pct === null ? '' : String(product.auto_approve_pct));

  const save = async () => {
    setSaving(true);
    try {
      await api.saveProduct(productId, {
        name: draft.name,
        display_name: draft.display_name || null,
        sku: draft.sku || null,
        upc: draft.upc || null,
        brand: draft.brand || null,
        supplier: draft.supplier || null,
        category: draft.category || null,
        unit_cost: draft.unit_cost,
        stock_on_hand: draft.stock_on_hand === '' ? null : Number(draft.stock_on_hand),
        min_price: draft.min_price || null,
        max_price: draft.max_price || null,
        auto_approve_pct: draft.auto_approve_pct === '' ? null : Number(draft.auto_approve_pct),
        agent_excluded: draft.agent_excluded,
        image_url: product.image_url,
      });
      toast(
        policyChanged
          ? 'Saved. The new policy was published to the agent.'
          : 'Saved.',
      );
      res.reload();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not save.', 'bad');
    } finally {
      setSaving(false);
    }
  };

  const setPrice = async () => {
    setPricing(true);
    try {
      const result = await api.setPrice(productId, priceDraft, reasonTag);
      toast(
        result.published
          ? `${money(priceDraft)} published to the shelf.`
          : `${money(priceDraft)} recorded — queued, the shelf network is down.`,
        result.published ? 'ok' : 'bad',
      );
      res.reload();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not set the price.', 'bad');
    } finally {
      setPricing(false);
    }
  };

  return (
    <div className={s.layout}>
      {/* ── Main column ───────────────────────────────────────────────── */}
      <div className={s.mainCol}>
        <section className={`card ${s.form}`}>
          <Field label="Full product name" hint="Used in this dashboard and in reports. Never shown on the shelf.">
            <input
              className={`field ${s.input}`}
              value={draft.name}
              disabled={!canEdit}
              onChange={(e) => set('name', e.target.value)}
            />
          </Field>

          {/* The hardware constraint, made visible. */}
          <div className={s.shelfBlock}>
            <div className={s.shelfHead}>
              <label className={s.label} htmlFor="display-name">
                Shelf display name — line 1
              </label>
              <span className={s.counter} data-over={nameOver || undefined}>
                {nameLen}/16
              </span>
            </div>
            <input
              id="display-name"
              className={`field ${s.monoInput}`}
              value={draft.display_name}
              maxLength={16}
              disabled={!canEdit}
              onChange={(e) => set('display_name', e.target.value.toUpperCase())}
            />
            <div className={s.shelfRow}>
              <ShelfDisplay
                kind={label?.kind ?? 'lcd'}
                line1={previewLine1}
                line2={previewLine2}
                fullName={draft.name}
                price={priceDraft || product.current_price}
                brand={draft.brand}
                slot={product.slot_code}
                publishedAt={product.priced_at}
                caption={label ? 'Shelf render — as published' : 'No label paired yet'}
              />
              <p className={s.shelfNote}>
                Line 2 is composed from the price and the reason tag, so it is
                never edited by hand. The label firmware cuts both lines at
                sixteen characters — a longer name does not wrap, it disappears.
                {label?.kind === 'eink' && (
                  <>
                    {' '}
                    This product&rsquo;s label is a Heltec E213, which is a pixel
                    display and shows the full name. The sixteen-character limit
                    still applies to the reason tag, because the contract enforces
                    it for every label so the LCDs never truncate.
                  </>
                )}
              </p>
            </div>
          </div>

          <div className={s.grid3}>
            <Field label="Unit cost">
              <input
                className={`field ${s.input}`}
                inputMode="decimal"
                value={draft.unit_cost}
                disabled={!canEdit}
                onChange={(e) => set('unit_cost', e.target.value)}
              />
            </Field>
            <Field label="Stock on hand">
              <input
                className={`field ${s.input}`}
                inputMode="numeric"
                value={draft.stock_on_hand}
                disabled={!canEdit}
                onChange={(e) => set('stock_on_hand', e.target.value)}
              />
            </Field>
            <Field label="Category">
              <input
                className={`field ${s.input}`}
                value={draft.category}
                disabled={!canEdit}
                onChange={(e) => set('category', e.target.value)}
              />
            </Field>
          </div>

          <div className={s.grid3}>
            <Field label="SKU">
              <input
                className={`field ${s.monoInput}`}
                value={draft.sku}
                disabled={!canEdit}
                onChange={(e) => set('sku', e.target.value)}
              />
            </Field>
            <Field label="UPC">
              <input
                className={`field ${s.monoInput}`}
                value={draft.upc}
                disabled={!canEdit}
                onChange={(e) => set('upc', e.target.value)}
              />
            </Field>
            <Field label="Brand">
              <input
                className={`field ${s.input}`}
                value={draft.brand}
                disabled={!canEdit}
                onChange={(e) => set('brand', e.target.value)}
              />
            </Field>
          </div>

          <Field label="Supplier">
            <input
              className={`field ${s.input}`}
              value={draft.supplier}
              disabled={!canEdit}
              onChange={(e) => set('supplier', e.target.value)}
            />
          </Field>

          {/* ── Policy ──────────────────────────────────────────────────
              This block is the mechanism by which a manager delegates
              authority, so it is set apart from the catalogue fields above
              rather than being one more input among them. */}
          <fieldset className={s.policy}>
            <legend className={s.policyLegend}>What the agent may do without you</legend>
            <div className={s.grid3}>
              <Field label="Minimum price">
                <input
                  className={`field ${s.monoInput}`}
                  inputMode="decimal"
                  value={draft.min_price}
                  disabled={!canPrice}
                  onChange={(e) => set('min_price', e.target.value)}
                />
              </Field>
              <Field label="Maximum price">
                <input
                  className={`field ${s.monoInput}`}
                  inputMode="decimal"
                  value={draft.max_price}
                  disabled={!canPrice}
                  onChange={(e) => set('max_price', e.target.value)}
                />
              </Field>
              <Field label="Auto-approve up to %" hint="Empty means always ask">
                <input
                  className={`field ${s.monoInput}`}
                  inputMode="decimal"
                  placeholder="always ask"
                  value={draft.auto_approve_pct}
                  disabled={!canPrice}
                  onChange={(e) => set('auto_approve_pct', e.target.value)}
                />
              </Field>
            </div>

            <label className={s.checkRow}>
              <input
                type="checkbox"
                checked={draft.agent_excluded}
                disabled={!canPrice}
                onChange={(e) => set('agent_excluded', e.target.checked)}
              />
              <span>
                <strong>Exclude from the agent entirely.</strong> No proposals at
                all for this product — different from &ldquo;always ask&rdquo;,
                which still sends them to your queue.
              </span>
            </label>

            <p className={s.policyNote}>
              {draft.auto_approve_pct === ''
                ? 'Every proposal for this product goes to your queue, however small.'
                : `A move of up to ${draft.auto_approve_pct}% inside the band above is applied with no human. Anything bigger, or outside the band, waits for you.`}{' '}
              An automatic change is still logged, with{' '}
              <span className={s.mono}>approved_by: policy</span> against it.
            </p>
          </fieldset>

          {canEdit && (
            <div className={s.actions}>
              <Button busy={saving} disabled={!dirty} onClick={save}>
                {dirty ? 'Save changes' : 'Saved'}
              </Button>
              <Button variant="ghost" disabled={!dirty} onClick={() => res.reload()}>
                Discard
              </Button>
              {policyChanged && (
                <span className={s.policyWarn}>
                  Saving republishes this product&rsquo;s policy to the agent.
                </span>
              )}
            </div>
          )}
        </section>

        {/* ── Set a price ───────────────────────────────────────────────
            Its own card, because publishing a price is not the same action as
            editing a catalogue row. */}
        {canPrice && (
          <section className={`card ${s.priceCard}`}>
            <h2 className={s.cardTitle}>Set the price yourself</h2>
            <p className={s.priceLead}>
              This publishes straight to the shelf with your name on it. There is
              no proposal behind it and therefore no explanation — the log will
              show that, rather than attaching the agent&rsquo;s last reasoning to
              a number you chose.
            </p>
            <div className={s.priceRow}>
              <span className={s.priceField}>
                <span className={s.currency}>$</span>
                <input
                  className={s.priceInput}
                  inputMode="decimal"
                  value={priceDraft}
                  onChange={(e) => setPriceDraft(e.target.value)}
                  aria-label="New price"
                />
              </span>
              <span className={s.reasonField}>
                <input
                  className={`field ${s.monoInput}`}
                  value={reasonTag}
                  maxLength={16}
                  onChange={(e) => setReasonTag(e.target.value)}
                  aria-label="Reason tag, shown on the shelf label"
                />
                <span className={s.counter} data-over={reasonTag.length > 16 || undefined}>
                  {reasonTag.length}/16
                </span>
              </span>
              <Button
                busy={pricing}
                disabled={!priceDraft || priceDraft === product.current_price}
                onClick={setPrice}
              >
                Publish {money(priceDraft || '0')}
              </Button>
            </div>
            <p className={s.priceNote}>
              The reason tag goes on line 2 of the shelf label, verbatim. It is
              checked against this product&rsquo;s band:{' '}
              {product.min_price && product.max_price
                ? `${money(product.min_price)} – ${money(product.max_price)}`
                : 'no band set'}
              .
            </p>
          </section>
        )}
      </div>

      {/* ── Side column ───────────────────────────────────────────────── */}
      <aside className={s.sideCol}>
        <section className={`card ${s.summary}`}>
          <div className={s.summaryTop}>
            <span className={s.summaryPrice}>{money(product.current_price)}</span>
            <MarginPill marginPct={product.margin_pct} />
          </div>
          <span className={s.summaryWhen}>
            {product.priced_at ? `Set ${relative(product.priced_at)}` : 'Never priced'}
            {product.previous_price && ` · was ${money(product.previous_price)}`}
          </span>
          <dl className={s.summaryList}>
            <Row label="Category">
              <CategoryPill category={product.category} />
            </Row>
            <Row label="Stock">
              <StockCell stock={product.stock_on_hand} />
            </Row>
            <Row label="Waiting on you">
              {product.pending_count > 0 ? (
                <a className={s.link} href="/suggestions">
                  {product.pending_count}
                </a>
              ) : (
                'None'
              )}
            </Row>
            {product.dead_stock_risk !== null && (
              <Row label="Dead-stock risk">
                {pctPlain(product.dead_stock_risk * 100, 0)}
                {product.predicted_sellby && (
                  <span className={s.rowNote}>sells by {product.predicted_sellby}</span>
                )}
              </Row>
            )}
          </dl>
        </section>

        {/* The paired label. "As published" in the preview above, and here the
            device's own state — two different facts, kept apart. */}
        <section className={`card ${s.labelCard}`}>
          <div className={s.cardHead}>
            <h2 className={s.cardTitle}>Paired label</h2>
            {label && <StatusTag status={label.status} />}
          </div>
          {label ? (
            <dl className={s.summaryList}>
              <Row label="Label">
                <span className={s.mono}>{label.label_id}</span>
              </Row>
              <Row label="Hardware">
                {label.kind === 'eink' ? 'Heltec E213 e-ink' : '16×2 character LCD'}
              </Row>
              <Row label="Position">
                <span className={s.mono}>{label.slot_code ?? '—'}</span>
              </Row>
              <Row label="Last seen">{relative(label.last_seen_at)}</Row>
              <Row label="Signal">
                <SignalBars rssi={label.rssi_dbm} />
              </Row>
              <Row label="Battery">
                <Battery pct={label.battery_pct} />
              </Row>
              <Row label="Firmware">
                <span className={s.mono}>{label.firmware ?? '—'}</span>
              </Row>
            </dl>
          ) : (
            <p className={s.noLabel}>
              No shelf label is paired, so an approved price has nowhere to go.
              The agent will still propose prices; they just will not reach a
              shelf until a label is paired.
            </p>
          )}
        </section>

        <section className={`card ${s.historyCard}`}>
          <div className={s.cardHead}>
            <h2 className={s.cardTitle}>Price history</h2>
            <span className={s.cardMeta}>{price_history.length} changes</span>
          </div>
          <div className={s.chartPad}>
            <PriceHistoryChart points={price_history} height={140} />
          </div>
        </section>

        <section className={`card ${s.logCard}`}>
          <div className={s.cardHead}>
            <h2 className={s.cardTitle}>Suggestions and decisions</h2>
          </div>
          {decisions.length === 0 ? (
            <p className={s.noLabel}>
              No suggestions for this product yet. The agent starts proposing once
              it has stock, sales and expiry to work from.
            </p>
          ) : (
            decisions.map((d) => (
              <div className={s.logRow} key={d.decision_id}>
                <span className={s.logDate}>{dateTime(d.decided_at ?? d.ts)}</span>
                <span className={s.logText}>
                  {d.status === 'pending'
                    ? `Proposing ${money(d.proposed_price)} — waiting on you`
                    : d.status === 'rejected'
                    ? `Declined ${money(d.proposed_price)}`
                    : d.status === 'expired'
                      ? `${money(d.proposed_price)} lapsed undecided`
                      : d.status === 'superseded'
                        ? `${money(d.proposed_price)} replaced by a newer proposal`
                        : d.status === 'overridden'
                          ? `Proposed ${money(d.proposed_price)}, applied ${money(d.final_price)}`
                          : d.status === 'auto_approved'
                            ? `${money(d.final_price ?? d.proposed_price)} applied by your policy`
                            : `${money(d.final_price ?? d.proposed_price)} approved`}
                  {d.decided_by && d.decided_by !== 'system' && d.decided_by !== 'policy' && (
                    <span className={s.logWho}>by {d.decided_by}</span>
                  )}
                </span>
                <StatusTag status={d.status} />
              </div>
            ))
          )}
        </section>

        {fields && Object.keys(fields.values).length > 0 && (
          <section className={`card ${s.fieldsCard}`}>
            <div className={s.cardHead}>
              <h2 className={s.cardTitle}>{title(fields.pack)} fields</h2>
            </div>
            <dl className={s.summaryList}>
              {Object.entries(fields.values).map(([key, value]) => (
                <Row key={key} label={key.replace(/_/g, ' ')} capitalise>
                  <span className={s.mono}>{String(value)}</span>
                </Row>
              ))}
            </dl>
            <p className={s.fieldsNote}>
              {fields.pack === 'grocery'
                ? 'Best-before drives markdown proposals. The others are reference only.'
                : 'Reference only — none of these affect a proposed price.'}
            </p>
          </section>
        )}
      </aside>
    </div>
  );
}

/** Sentence case for a machine-supplied name like "grocery". */
function title(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** Line 2 as the firmware will see it, mirroring gate.render_lines() exactly. */
function composeLine2(price: string | null, reasonTag: string): string {
  if (!price) return '';
  const moneyPart = `$${Number(price).toFixed(2)}`;
  const room = 16 - moneyPart.length - 1;
  return room >= 4 && reasonTag
    ? `${moneyPart} ${reasonTag.slice(0, room)}`.slice(0, 16)
    : moneyPart.slice(0, 16);
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={s.field}>
      <label className={s.label}>{label}</label>
      {children}
      {hint && <span className={s.hint}>{hint}</span>}
    </div>
  );
}

function Row({
  label,
  children,
  capitalise,
}: {
  label: string;
  children: React.ReactNode;
  /* Only for labels derived from a snake_case key. Hand-written labels are
   * already written the way they should read, and capitalising every word of
   * them produces "Waiting On You". */
  capitalise?: boolean;
}) {
  return (
    <div className={s.summaryRow}>
      <dt className={s.summaryLabel} data-capitalise={capitalise || undefined}>{label}</dt>
      <dd className={s.summaryValue}>{children}</dd>
    </div>
  );
}
