/* 09 Pricing rules — the policy every suggestion is measured against.
 *
 * The only screen in this console with Save and Discard in the header, because
 * it is the only one where a half-finished edit is a sentence rather than a
 * field: a margin floor without its ceiling is not a policy.
 *
 * **Saving a category rule is a large, irreversible action.** It rewrites
 * `min_price` and `max_price` on every SKU in that category and republishes a
 * retained guardrail for each, so the agent's view of policy changes for all of
 * them at once. The preview shows what would change before anything does, and
 * the per-SKU agent exclusions a manager set by hand are left alone unless the
 * checkbox is ticked deliberately.
 *
 * Columns are percentages, not money. `min_margin_pct` is the floor each SKU's
 * `min_price` is derived from; `ceiling_pct` is the headroom above the current
 * price. A single money floor across a whole category would mean nothing for
 * Electronics, where a $449 television and a $34 charger share one rule.
 */

import { useMemo, useState } from 'react';
import { api, ApiError } from '../lib/api';
import { money, plural, isManagerRole } from '../lib/format';
import { useResource } from '../lib/useStream';
import { useDraft } from '../lib/useDraft';
import { usePageChrome, useShell, useStreamEvents } from '../components/AppShell';
import {
  Button, ConfirmModal, ErrorState, SkeletonRows, Toggle,
} from '../components/ui';
import type { CategoryRule, ChangeLimits, RulesPreview } from '../lib/types';
import s from './Rules.module.css';

interface LimitsDraft extends Record<string, unknown> {
  max_change_pct: string;
  max_changes_per_day: string;
  min_hours_between: string;
  proposal_ttl_hours: string;
}

interface CatDraft {
  min_margin_pct: string;
  ceiling_pct: string;
  agent_may_suggest: boolean;
}

export function Rules() {
  const { user, toast } = useShell();
  const canEdit = isManagerRole(user.role);

  const res = useResource(() => api.rules(), []);
  useStreamEvents(['rules', 'guardrail'], res.reload);

  const serverLimits: LimitsDraft | null = useMemo(() => {
    const l = res.data?.limits as ChangeLimits | null | undefined;
    if (!l) return null;
    return {
      max_change_pct: String(l.max_change_pct),
      max_changes_per_day: String(l.max_changes_per_day),
      min_hours_between: String(l.min_hours_between),
      proposal_ttl_hours: String(l.proposal_ttl_hours),
    };
  }, [res.data?.limits]);

  const limits = useDraft<LimitsDraft>(serverLimits);

  // Category edits are held separately: each row saves on its own, because each
  // one republishes a different set of SKUs and the confirmation has to name
  // which.
  const [catEdits, setCatEdits] = useState<Record<string, CatDraft>>({});
  const [saving, setSaving] = useState(false);
  const [confirmCat, setConfirmCat] = useState<string | null>(null);
  const [preview, setPreview] = useState<RulesPreview | null>(null);
  const [applyFlag, setApplyFlag] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rows: (CategoryRule & { configured: boolean })[] = useMemo(() => {
    const configured = (res.data?.categories ?? []).map((c) => ({ ...c, configured: true }));
    const missing = (res.data?.unconfigured ?? []).map((u) => ({
      category: u.category,
      ceiling_pct: null,
      min_margin_pct: 0,
      agent_may_suggest: true,
      set_by: null,
      updated_at: '',
      product_count: u.product_count,
      configured: false,
    }));
    return [...configured, ...missing].sort((a, b) => a.category.localeCompare(b.category));
  }, [res.data]);

  const draftFor = (row: CategoryRule & { configured: boolean }): CatDraft =>
    catEdits[row.category] ?? {
      min_margin_pct: String(row.min_margin_pct ?? ''),
      ceiling_pct: row.ceiling_pct === null ? '' : String(row.ceiling_pct),
      agent_may_suggest: row.agent_may_suggest,
    };

  const catDirty = (row: CategoryRule & { configured: boolean }) => {
    const d = catEdits[row.category];
    if (!d) return false;
    return (
      d.min_margin_pct !== String(row.min_margin_pct ?? '') ||
      d.ceiling_pct !== (row.ceiling_pct === null ? '' : String(row.ceiling_pct)) ||
      d.agent_may_suggest !== row.agent_may_suggest
    );
  };

  const anyCatDirty = rows.some(catDirty);
  const dirty = limits.dirty || anyCatDirty;

  usePageChrome(
    {
      title: 'Pricing rules',
      actions: canEdit ? (
        <>
          <Button
            variant="secondary"
            disabled={!dirty}
            onClick={() => {
              limits.discard();
              setCatEdits({});
            }}
          >
            Discard changes
          </Button>
          <Button busy={saving} disabled={!limits.dirty} onClick={saveLimits}>
            Save limits
          </Button>
        </>
      ) : undefined,
    },
    [canEdit, dirty, limits.dirty, saving],
  );

  async function saveLimits() {
    if (!limits.draft) return;
    setSaving(true);
    try {
      const body = {
        max_change_pct: Number(limits.draft.max_change_pct),
        max_changes_per_day: Number(limits.draft.max_changes_per_day),
        min_hours_between: Number(limits.draft.min_hours_between),
        proposal_ttl_hours: Number(limits.draft.proposal_ttl_hours),
      };
      await api.putLimits(body);
      limits.commit(limits.draft);
      toast('Change limits saved. They apply to the next agent run.');
      res.reload();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not save the limits.', 'bad');
    } finally {
      setSaving(false);
    }
  }

  const openPreview = async (category: string) => {
    const row = rows.find((r) => r.category === category);
    if (!row) return;
    const d = draftFor(row);
    setConfirmCat(category);
    setPreview(null);
    setApplyFlag(false);
    setError(null);
    try {
      setPreview(
        await api.rulesPreview(category, {
          min_margin_pct: Number(d.min_margin_pct || 0),
          ceiling_pct: d.ceiling_pct === '' ? null : Number(d.ceiling_pct),
          agent_may_suggest: d.agent_may_suggest,
          apply_agent_flag: false,
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not work out what would change.');
    }
  };

  const saveCategory = async () => {
    if (!confirmCat) return;
    const row = rows.find((r) => r.category === confirmCat);
    if (!row) return;
    const d = draftFor(row);
    setSaving(true);
    setError(null);
    try {
      const result = await api.putCategoryRule(confirmCat, {
        min_margin_pct: Number(d.min_margin_pct || 0),
        ceiling_pct: d.ceiling_pct === '' ? null : Number(d.ceiling_pct),
        agent_may_suggest: d.agent_may_suggest,
        apply_agent_flag: applyFlag,
      });
      const { products, published, queued } = result.republished;
      toast(
        queued === 0
          ? `${confirmCat} saved. ${products} ${plural(products, 'product')} re-banded, ${published} published to the agent.`
          : `${confirmCat} saved. ${queued} guardrail ${plural(queued, 'update')} queued — the shelf network is down.`,
        queued === 0 ? 'ok' : 'bad',
      );
      setCatEdits((prev) => {
        const next = { ...prev };
        delete next[confirmCat];
        return next;
      });
      setConfirmCat(null);
      setPreview(null);
      res.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the rule.');
    } finally {
      setSaving(false);
    }
  };

  if (res.error) {
    return (
      <ErrorState
        title="Cannot load the pricing rules"
        body={res.error}
        actions={<Button onClick={res.reload}>Try again</Button>}
      />
    );
  }
  if (res.loading || !res.data) return <SkeletonRows rows={6} columns={4} />;

  return (
    <div className={s.page}>
      {limits.serverChanged && (
        <p className={s.stale} role="status">
          Somebody else changed these rules while you were editing. Your changes
          are still here — discard them to see theirs.
        </p>
      )}

      {/* ── Change limits ──────────────────────────────────────────────── */}
      <section className={`card ${s.limits}`}>
        <h2 className={s.cardTitle}>Change limits</h2>
        <p className={s.cardSub}>
          Applied to every suggestion before it reaches your queue.
        </p>

        <div className={s.limitsGrid}>
          <Limit
            label="Max change per update"
            suffix="%"
            value={limits.draft?.max_change_pct ?? ''}
            dirty={limits.dirtyKeys.has('max_change_pct')}
            disabled={!canEdit}
            onChange={(v) => limits.set('max_change_pct', v)}
            hint="A bigger move is never applied automatically."
          />
          <Limit
            label="Max updates per product per day"
            value={limits.draft?.max_changes_per_day ?? ''}
            dirty={limits.dirtyKeys.has('max_changes_per_day')}
            disabled={!canEdit}
            onChange={(v) => limits.set('max_changes_per_day', v)}
            hint="Keeps a shelf price from flickering."
          />
          <Limit
            label="Minimum hours between changes"
            value={limits.draft?.min_hours_between ?? ''}
            dirty={limits.dirtyKeys.has('min_hours_between')}
            disabled={!canEdit}
            onChange={(v) => limits.set('min_hours_between', v)}
            hint="Measured from the last price that reached the shelf."
          />
          <Limit
            label="A suggestion lapses after"
            suffix="hours"
            value={limits.draft?.proposal_ttl_hours ?? ''}
            dirty={limits.dirtyKeys.has('proposal_ttl_hours')}
            disabled={!canEdit}
            onChange={(v) => limits.set('proposal_ttl_hours', v)}
            hint="Undecided after this, and the shelf keeps its price."
          />
        </div>
      </section>

      {/* ── Categories ─────────────────────────────────────────────────── */}
      <section className={`card ${s.cats}`}>
        <div className={s.catsHead}>
          <h2 className={s.cardTitle}>Floors and ceilings by category</h2>
          <p className={s.cardSub}>
            A suggestion outside these bounds is never applied without you.
            Saving a row re-bands every product in that category.
          </p>
        </div>

        <div className={s.scroller}>
          <table className={s.table}>
            <thead>
              <tr>
                <th className={`${s.th} ${s.thFirst}`}>Category</th>
                <th className={`${s.th} ${s.right}`}>Margin floor %</th>
                <th className={`${s.th} ${s.right}`}>Ceiling headroom %</th>
                <th className={s.th}>Automatic suggestions</th>
                <th className={`${s.th} ${s.right}`}>Products</th>
                <th className={`${s.th} ${s.thLast}`} />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const d = draftFor(row);
                const isDirty = catDirty(row);
                const patch = (part: Partial<CatDraft>) =>
                  setCatEdits((prev) => ({
                    ...prev,
                    [row.category]: { ...draftFor(row), ...part },
                  }));
                return (
                  <tr key={row.category} data-dirty={isDirty || undefined}>
                    <td className={`${s.td} ${s.catCell}`}>
                      <span className={s.catInner}>
                        {row.category}
                        {!row.configured && (
                          <span className={s.unconfigured} title="No rule saved for this category yet">
                            No rule yet
                          </span>
                        )}
                      </span>
                    </td>
                    <td className={`${s.td} ${s.right}`}>
                      <PctInput
                        value={d.min_margin_pct}
                        disabled={!canEdit}
                        dirty={isDirty && d.min_margin_pct !== String(row.min_margin_pct ?? '')}
                        onChange={(v) => patch({ min_margin_pct: v })}
                        aria-label={`${row.category} margin floor`}
                      />
                    </td>
                    <td className={`${s.td} ${s.right}`}>
                      <PctInput
                        value={d.ceiling_pct}
                        disabled={!canEdit}
                        placeholder="none"
                        dirty={isDirty && d.ceiling_pct !== (row.ceiling_pct === null ? '' : String(row.ceiling_pct))}
                        onChange={(v) => patch({ ceiling_pct: v })}
                        aria-label={`${row.category} ceiling headroom`}
                      />
                    </td>
                    <td className={s.td}>
                      <Toggle
                        checked={d.agent_may_suggest}
                        disabled={!canEdit}
                        onChange={(next) => patch({ agent_may_suggest: next })}
                        onLabel="Agent may suggest"
                        offLabel="Excluded"
                      />
                    </td>
                    <td className={`${s.td} ${s.right} ${s.countCell}`}>
                      {row.product_count ?? 0}
                    </td>
                    <td className={`${s.td} ${s.right}`}>
                      <Button
                        size="sm"
                        disabled={!canEdit || !isDirty}
                        onClick={() => openPreview(row.category)}
                      >
                        Review &amp; save
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <p className={s.foot}>
        These bounds govern what the agent may propose and what your standing
        policy may apply without you. A product can still have its own band, set
        on its page — saving a category rule overwrites it, which is why the
        review step tells you how many bands would move.
      </p>

      {confirmCat && (
        <ConfirmModal
          title={`Save the ${confirmCat} rule?`}
          confirmLabel={preview ? `Re-band ${preview.products} ${plural(preview.products, 'product')}` : 'Checking…'}
          tone="primary"
          busy={saving}
          error={error}
          onClose={() => {
            setConfirmCat(null);
            setPreview(null);
            setError(null);
          }}
          onConfirm={saveCategory}
          body={
            preview === null ? (
              <p>Working out what this would change…</p>
            ) : (
              <>
                <p>
                  This re-derives the price band on{' '}
                  <strong>{preview.products}</strong>{' '}
                  {plural(preview.products, 'product')} and republishes{' '}
                  <strong>{preview.would_publish}</strong> of them to the agent.
                </p>

                {preview.examples.length > 0 && (
                  <ul className={s.examples}>
                    {preview.examples.map((e) => (
                      <li key={e.product_id}>
                        <span className={s.exName}>{e.name}</span>
                        <span className={s.exBand}>
                          {money(e.min_price)} – {money(e.max_price)} →{' '}
                          <strong>
                            {money(e.new_min_price)} – {money(e.new_max_price)}
                          </strong>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}

                {preview.skipped_no_band > 0 && (
                  <p className={s.note}>
                    {preview.skipped_no_band}{' '}
                    {plural(preview.skipped_no_band, 'product has', 'products have')} no
                    price yet, so nothing is published for{' '}
                    {preview.skipped_no_band === 1 ? 'it' : 'them'}.
                  </p>
                )}

                {/* Off by default: this overwrites exclusions a manager set per
                    product, and there is no undo. */}
                <label className={s.applyRow}>
                  <input
                    type="checkbox"
                    checked={applyFlag}
                    onChange={(e) => setApplyFlag(e.target.checked)}
                  />
                  <span>
                    Also apply this to each product&rsquo;s own agent setting.
                    {preview.individually_excluded > 0 ? (
                      <strong>
                        {' '}
                        {preview.individually_excluded}{' '}
                        {plural(preview.individually_excluded, 'product is', 'products are')}{' '}
                        excluded by hand and would be overwritten.
                      </strong>
                    ) : (
                      ' Nothing is excluded by hand right now.'
                    )}
                  </span>
                </label>
              </>
            )
          }
        />
      )}
    </div>
  );
}

function Limit({
  label,
  value,
  suffix,
  hint,
  dirty,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  suffix?: string;
  hint: string;
  dirty: boolean;
  disabled: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <div className={s.limit}>
      <label className={s.limitLabel}>
        {label}
        {dirty && <span className={s.dirtyMark} title="Edited, not saved" aria-label="edited" />}
      </label>
      <span className={s.suffixBox} data-dirty={dirty || undefined}>
        <input
          className={s.suffixInput}
          inputMode="numeric"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value.replace(/[^\d.]/g, ''))}
        />
        {suffix && <span className={s.suffix}>{suffix}</span>}
      </span>
      <span className={s.limitHint}>{hint}</span>
    </div>
  );
}

function PctInput({
  value,
  dirty,
  disabled,
  placeholder,
  onChange,
  ...rest
}: {
  value: string;
  dirty: boolean;
  disabled: boolean;
  placeholder?: string;
  onChange: (v: string) => void;
} & React.AriaAttributes) {
  return (
    <input
      className={`field ${s.pctInput}`}
      inputMode="decimal"
      value={value}
      placeholder={placeholder}
      disabled={disabled}
      data-dirty={dirty || undefined}
      onChange={(e) => onChange(e.target.value.replace(/[^\d.]/g, ''))}
      {...rest}
    />
  );
}
