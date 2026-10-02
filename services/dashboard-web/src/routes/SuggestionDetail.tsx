/* 04 Suggestion detail. Two columns, minmax(0,1fr) 384px.
 *
 * This is the screen the project's central claim rests on: the manager reads
 * the explanation *before* putting their name on a price, not after the price
 * has already moved. So the explanation leads, and the decision controls sit
 * below and beside it rather than above.
 *
 * The honest states this screen has to handle, all of which are normal:
 *   - the explanation never arrived (SHAP can fail; that is not an error)
 *   - the agent reported no confidence
 *   - the proposal is outside the product's own band
 *   - approving at a different price means the explanation explains something
 *     that did not ship, and the screen says so before you do it
 */

import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import {
  countdown, dateTime, money, pct, pctPlain, relative,
} from '../lib/format';
import { useResource } from '../lib/useStream';
import { usePageChrome, useShell, useStreamEvents } from '../components/AppShell';
import { ShelfDisplay } from '../components/ShelfDisplay';
import {
  Button, ErrorState, PriceHistoryChart, ShapLadder, SkeletonRows, StatusTag,
} from '../components/ui';
import s from './SuggestionDetail.module.css';

export function SuggestionDetail() {
  const { decisionId = '' } = useParams();
  const navigate = useNavigate();
  const { user, toast } = useShell();
  const canDecide = user.role === 'manager';

  const [manual, setManual] = useState('');
  const [busy, setBusy] = useState<'approve' | 'override' | 'reject' | null>(null);

  const res = useResource(() => api.suggestion(decisionId), [decisionId]);
  useStreamEvents(['explain', 'suggestions', 'expired'], res.reload);

  const sug = res.data?.suggestion;

  // Seed the override box with the agent's own number, so the field starts from
  // the proposal rather than empty — overriding is an edit, not a fresh guess.
  useEffect(() => {
    if (sug) setManual(sug.proposed_price);
  }, [sug?.decision_id, sug?.proposed_price]);

  usePageChrome(
    {
      title: sug ? sug.name : 'Suggestion',
      crumb: { label: 'Price suggestions', to: '/suggestions' },
    },
    [sug?.name],
  );

  if (res.error) {
    return (
      <ErrorState
        title="This suggestion is no longer in the queue"
        body={res.error}
        actions={<Button onClick={() => navigate('/suggestions')}>Back to the queue</Button>}
      />
    );
  }
  if (res.loading || !res.data || !sug) return <SkeletonRows rows={6} columns={3} />;

  const { price_history, earlier_decisions, category_rules } = res.data;
  const manualDiffers = manual !== '' && manual !== sug.proposed_price;
  const remaining = countdown(sug.expires_at);

  /* The e-ink label shows a struck-through "was" price and a REDUCED tag. Both
   * are claims about the change, so they appear only on a markdown — an
   * increase presented as a reduction would mislead a shopper standing in
   * front of the shelf. */
  const wasPrice = sug.delta_pct < 0 ? sug.current_price : null;

  const belowFloor =
    sug.proposed_margin_pct !== null &&
    sug.category_min_margin_pct !== null &&
    sug.proposed_margin_pct < sug.category_min_margin_pct;

  const decide = async (action: 'approve' | 'override' | 'reject') => {
    setBusy(action);
    try {
      if (action === 'reject') {
        await api.reject(sug.decision_id);
        toast(`${sug.name} rejected. The shelf keeps ${money(sug.current_price)}.`);
      } else {
        const result = await api.approve(
          sug.decision_id,
          action === 'override' ? manual : undefined,
        );
        toast(
          result.published
            ? `${sug.name} is now ${money(result.final_price)} on the shelf.`
            : `${sug.name} approved at ${money(result.final_price)} — queued, the shelf network is down.`,
          result.published ? 'ok' : 'bad',
        );
      }
      navigate('/suggestions');
    } catch (err) {
      toast(
        err instanceof ApiError ? err.message : 'Something went wrong.',
        'bad',
      );
      // A 409 means the queue moved under us; reload so the screen stops
      // offering a decision that is no longer available.
      if (err instanceof ApiError && err.status === 409) res.reload();
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={s.layout}>
      {/* ── Main column: the explanation leads ─────────────────────────── */}
      <div className={s.mainCol}>
        <section className={`card ${s.explain}`}>
          <div className={s.explainHead}>
            <h2 className={s.sectionTitle}>Why the agent proposes this</h2>
            <span className={s.explainMeta}>
              {sug.agent_version} · {relative(sug.ts)}
            </span>
          </div>

          <p className={s.prose}>{sug.why.text}</p>

          {sug.why.source === 'composed' && (
            <p className={s.composedNote}>
              The agent sent the reason tag &ldquo;{sug.reason_tag}&rdquo;, not this
              sentence. The dashboard composed it from that tag, the explanation
              vector below and this product&rsquo;s own figures.
            </p>
          )}

          {/* The hero. Missing is a state this screen states plainly, because a
              manager approving a price whose explanation never arrived should
              know that is what they are doing. */}
          {sug.explanation_missing ? (
            <div className={s.noExplanation}>
              <strong>No explanation arrived for this proposal.</strong>
              <span>
                SHAP computation can fail or lag, and that is a normal state
                rather than an error — but it means the only thing behind this
                number is the reason tag. You can still decide; you would be
                deciding without the agent&rsquo;s reasoning.
              </span>
            </div>
          ) : (
            sug.shap && (
              <div className={s.shapBlock}>
                <h3 className={s.subTitle}>What moved the price</h3>
                <ShapLadder
                  shap={sug.shap}
                  baseValue={sug.base_value}
                  proposedPrice={sug.proposed_price}
                />
              </div>
            )
          )}

          <dl className={s.inputs}>
            <Input
              label="Stock on hand"
              value={sug.stock_on_hand === null ? '—' : String(sug.stock_on_hand)}
              note="As the catalogue records it"
            />
            <Input
              label="Unit cost"
              value={money(sug.unit_cost)}
              note="What the store paid"
            />
            <Input
              label="Margin after change"
              value={pctPlain(sug.proposed_margin_pct)}
              note={
                sug.category_min_margin_pct !== null
                  ? `${sug.category}: ${pctPlain(sug.category_min_margin_pct)} floor`
                  : 'No category floor set'
              }
              tone={belowFloor ? 'bad' : undefined}
            />
            <Input
              label="Confidence"
              value={
                sug.confidence_reported && sug.agent_confidence !== null
                  ? `${Math.round(sug.agent_confidence * 100)}%`
                  : '—'
              }
              note={
                sug.confidence_reported
                  ? 'As the agent reported it'
                  : 'The agent reported none'
              }
            />
          </dl>
        </section>

        <section className={`card ${s.history}`}>
          <div className={s.explainHead}>
            <h2 className={s.sectionTitle}>Price history</h2>
            <span className={s.explainMeta}>
              {price_history.length} changes · the dashed arm is this proposal
            </span>
          </div>
          <PriceHistoryChart points={price_history} proposed={sug.proposed_price} />
        </section>
      </div>

      {/* ── Decision column ───────────────────────────────────────────── */}
      <aside className={s.sideCol}>
        <section className={`card ${s.decide}`}>
          <div className={s.prices}>
            <span className={s.priceBlock}>
              <span className="monoLabel">Current</span>
              <span className={s.priceOld}>{money(sug.current_price)}</span>
            </span>
            <span className={s.arrow} aria-hidden="true">
              →
            </span>
            <span className={s.priceBlock}>
              <span className="monoLabel">Suggested</span>
              <span className={s.priceNew}>{money(sug.proposed_price)}</span>
            </span>
            <span className={s.deltaPct} data-dir={sug.delta_pct < 0 ? 'down' : 'up'}>
              {pct(sug.delta_pct)}
            </span>
          </div>

          <dl className={s.facts}>
            <Fact label="Price band">
              {sug.min_price && sug.max_price
                ? `${money(sug.min_price)} – ${money(sug.max_price)}`
                : 'Not set'}
            </Fact>
            {sug.out_of_band && (
              <div className={s.bandWarn}>
                This proposal is outside the band you set, which is why it is
                here rather than applied automatically.
              </div>
            )}
            <Fact label="Delegated up to">
              {sug.auto_approve_pct === null
                ? 'Always ask'
                : `${sug.auto_approve_pct}% moves`}
            </Fact>
            <Fact label="Shelf label">
              {sug.label_id ? (
                <span className={s.labelFact}>
                  <span className={s.mono}>{sug.label_id}</span>
                  {sug.slot_code && <span className={s.mono}>· {sug.slot_code}</span>}
                  <StatusTag status={sug.label_status ?? 'unassigned'} />
                </span>
              ) : (
                <span className={s.noLabel}>No label paired — nothing to update</span>
              )}
            </Fact>
            <Fact label="Lapses">
              {remaining ? `in ${remaining} (${dateTime(sug.expires_at)})` : 'Already lapsed'}
            </Fact>
          </dl>

          {sug.label_id && sug.label_kind && (
            <div className={s.preview}>
              <span className={s.previewLabel}>Shelf label after approval</span>
              <ShelfDisplay
                kind={sug.label_kind}
                line1={sug.shelf_render.line1}
                line2={sug.shelf_render.line2}
                fullName={sug.name}
                price={sug.proposed_price}
                was={wasPrice}
                brand={sug.brand}
                slot={sug.slot_code}
                size="sm"
                caption="Will be published on approve"
              />
            </div>
          )}

          {canDecide ? (
            <>
              <div className={s.manual}>
                <label className={s.manualLabel} htmlFor="manual-price">
                  Or approve at your own price
                </label>
                <div className={s.manualRow}>
                  <span className={s.manualField}>
                    <span className={s.currency}>$</span>
                    <input
                      id="manual-price"
                      className={s.manualInput}
                      inputMode="decimal"
                      value={manual}
                      onChange={(e) => setManual(e.target.value)}
                    />
                  </span>
                  <Button
                    variant="secondary"
                    disabled={!manualDiffers}
                    busy={busy === 'override'}
                    onClick={() => decide('override')}
                  >
                    Approve at {money(manual || '0')}
                  </Button>
                </div>
                <span className={s.manualNote}>
                  A price you type is still checked against the band above.
                </span>
                {/* Said before the click, not after: the contract is explicit
                    that the explanation attached to this decision explains what
                    the agent wanted, never what shipped. */}
                {manualDiffers && !sug.explanation_missing && (
                  <span className={s.overrideWarn}>
                    The explanation above explains {money(sug.proposed_price)}, not{' '}
                    {money(manual)}. The log will record both.
                  </span>
                )}
              </div>

              <div className={s.buttons}>
                <Button
                  variant="ghost"
                  className={s.rejectBtn}
                  busy={busy === 'reject'}
                  onClick={() => decide('reject')}
                >
                  Reject
                </Button>
                <Button
                  className={s.approveBtn}
                  busy={busy === 'approve'}
                  onClick={() => decide('approve')}
                >
                  Approve at {money(sug.proposed_price)}
                </Button>
              </div>
            </>
          ) : (
            <p className={s.readOnly}>
              Only a manager can decide on a price. You are signed in as{' '}
              {user.role}.
            </p>
          )}
        </section>

        <section className={`card ${s.earlier}`}>
          <div className={s.cardHead}>
            <h2 className={s.cardTitle}>Earlier decisions on this product</h2>
          </div>
          {earlier_decisions.length === 0 ? (
            <div className={s.earlierEmpty}>
              Nothing has been decided for this product yet.
            </div>
          ) : (
            earlier_decisions.map((d) => (
              <div className={s.earlierRow} key={d.decision_id}>
                <span className={s.earlierDate}>{relative(d.decided_at ?? d.ts)}</span>
                <span className={s.earlierText}>
                  {d.status === 'rejected'
                    ? `Declined ${money(d.proposed_price)}`
                    : d.status === 'expired'
                      ? `${money(d.proposed_price)} lapsed undecided`
                      : d.status === 'overridden'
                        ? `Proposed ${money(d.proposed_price)}, applied ${money(d.final_price)}`
                        : `Applied ${money(d.final_price ?? d.proposed_price)}`}
                  {d.decided_by && d.decided_by !== 'system' && (
                    <span className={s.earlierWho}>
                      {d.decided_by === 'policy' ? 'by your standing policy' : `by ${d.decided_by}`}
                    </span>
                  )}
                </span>
                <StatusTag status={d.status} />
              </div>
            ))
          )}
        </section>

        {category_rules && (
          <p className={s.rulesNote}>
            {category_rules.agent_may_suggest
              ? `The agent may propose prices for ${category_rules.category}, with a ${pctPlain(category_rules.min_margin_pct)} margin floor.`
              : `${category_rules.category} is excluded from the agent — this proposal predates that rule.`}
          </p>
        )}
      </aside>
    </div>
  );
}

function Input({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note: string;
  tone?: 'bad';
}) {
  return (
    <div className={s.input} data-tone={tone}>
      <dt className="monoLabel">{label}</dt>
      <dd className={s.inputValue}>{value}</dd>
      <dd className={s.inputNote}>{note}</dd>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={s.fact}>
      <dt className={s.factLabel}>{label}</dt>
      <dd className={s.factValue}>{children}</dd>
    </div>
  );
}
