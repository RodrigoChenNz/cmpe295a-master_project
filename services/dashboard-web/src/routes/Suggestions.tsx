/* 03 Price suggestions.
 *
 * The product. Everything else in this console supports it.
 *
 * Two decisions worth knowing about:
 *
 * 1. The row actions are icon-only 31x31 buttons with `title` and `aria-label`,
 *    and there is deliberately NO hover-expand animation — the handoff tried one
 *    and removed it.
 * 2. Approving does not optimistically remove the row. A 409 ("someone else
 *    already decided this") is a real outcome here, and a row that vanishes and
 *    comes back is worse than one that waits a beat. The button goes busy, the
 *    list refetches, and a failure says why.
 */

import { useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import { countdown, money, pct, pctPlain, plural } from '../lib/format';
import { useDebounced, useResource } from '../lib/useStream';
import { usePageChrome, useShell, useStreamEvents } from '../components/AppShell';
import {
  Button, CategoryPill, Confidence, EmptyState, ErrorState, Icon, Pagination,
  SearchInput, SkeletonRows, StatusTag,
} from '../components/ui';
import s from './Suggestions.module.css';

type Direction = 'any' | 'up' | 'down';
type Size = 'any' | 'small' | 'large';

export function Suggestions() {
  const navigate = useNavigate();
  const { user, toast } = useShell();
  const canDecide = user.role === 'manager';

  const [q, setQ] = useState('');
  const [direction, setDirection] = useState<Direction>('any');
  const [size, setSize] = useState<Size>('any');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);

  const query = useDebounced(q);

  const res = useResource(
    () => api.suggestions({ q: query, direction, size, page, page_size: pageSize }),
    [query, direction, size, page, pageSize],
  );

  const reload = res.reload;
  useStreamEvents(['proposal', 'suggestions', 'expired', 'explain'], reload);

  usePageChrome(
    {
      title: 'Price suggestions',
      actions: (
        <>
          <Button
            variant="secondary"
            onClick={() =>
              window.open(api.activityCsvUrl({ range: '30d' }), '_blank', 'noopener')
            }
          >
            Export queue
          </Button>
        </>
      ),
    },
    [],
  );

  const rows = res.data?.rows ?? [];

  /* Selection is keyed by decision_id and pruned against the rows actually on
   * screen, so a bulk action can never act on a proposal that has since been
   * decided by somebody else or lapsed out of the queue. */
  const visibleIds = useMemo(() => rows.map((r) => r.decision_id), [rows]);
  const selectedVisible = useMemo(
    () => visibleIds.filter((id) => selected.has(id)),
    [visibleIds, selected],
  );
  const allSelected = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;

  const toggleRow = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const toggleAll = () =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) visibleIds.forEach((id) => next.delete(id));
      else visibleIds.forEach((id) => next.add(id));
      return next;
    });

  const markBusy = (id: string, on: boolean) =>
    setBusyIds((prev) => {
      const next = new Set(prev);
      on ? next.add(id) : next.delete(id);
      return next;
    });

  const decide = useCallback(
    async (id: string, action: 'approve' | 'reject', name: string) => {
      markBusy(id, true);
      try {
        if (action === 'approve') {
          const result = await api.approve(id);
          // The distinction that matters: approved and recorded, versus
          // actually on a shelf. Saying the second when only the first is true
          // is the one thing this screen must never do.
          toast(
            result.published
              ? `${name} is now ${money(result.final_price)} on the shelf.`
              : `${name} approved at ${money(result.final_price)} — queued, the shelf network is down.`,
            result.published ? 'ok' : 'bad',
          );
        } else {
          await api.reject(id);
          toast(`${name} rejected. The shelf keeps its current price.`);
        }
        setSelected((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        reload();
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.status === 409
              ? `${name}: ${err.message}`
              : err.message
            : 'Something went wrong.';
        toast(message, 'bad');
        // Refetch on a conflict too: the queue on screen is out of date, which
        // is exactly what the 409 was telling us.
        if (err instanceof ApiError && err.status === 409) reload();
      } finally {
        markBusy(id, false);
      }
    },
    [reload, toast],
  );

  const bulk = async (action: 'approve' | 'reject') => {
    const ids = selectedVisible;
    if (ids.length === 0) return;
    setBulkBusy(true);
    try {
      // Branch rather than index: the two responses are different shapes, and
      // reaching for a key only one of them has is how a count silently
      // becomes undefined in a message about shelf prices.
      let done: number;
      let failed: number;
      let firstError: string | undefined;
      if (action === 'approve') {
        const result = await api.bulkApprove(ids);
        done = result.counts.approved;
        failed = result.counts.failed;
        firstError = result.failed[0]?.error;
      } else {
        const result = await api.bulkReject(ids);
        done = result.counts.rejected;
        failed = result.counts.failed;
        firstError = result.failed[0]?.error;
      }
      // Partial success is named as partial. "12 approved" when one silently did
      // not is how a wrong price ends up on a shelf.
      toast(
        failed === 0
          ? `${done} ${plural(done, 'suggestion')} ${action === 'approve' ? 'approved' : 'rejected'}.`
          : `${done} ${action === 'approve' ? 'approved' : 'rejected'}, ${failed} could not be: ${firstError ?? ''}`,
        failed === 0 ? 'ok' : 'bad',
      );
      setSelected(new Set());
      reload();
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Bulk action failed.', 'bad');
    } finally {
      setBulkBusy(false);
    }
  };

  if (res.error) {
    return (
      <ErrorState
        title="Cannot reach the shelf network"
        body={res.error}
        actions={<Button onClick={reload}>Try again</Button>}
      />
    );
  }

  return (
    <>
      {/* ── Toolbar ───────────────────────────────────────────────────── */}
      <div className={s.toolbar}>
        <SearchInput
          value={q}
          onChange={(v) => {
            setQ(v);
            setPage(1);
          }}
          width="200px"
          aria-label="Search suggestions"
        />

        <select
          className="field"
          value={direction}
          onChange={(e) => {
            setDirection(e.target.value as Direction);
            setPage(1);
          }}
          aria-label="Direction"
        >
          <option value="any">Any direction</option>
          <option value="down">Markdowns only</option>
          <option value="up">Increases only</option>
        </select>

        <select
          className="field"
          value={size}
          onChange={(e) => {
            setSize(e.target.value as Size);
            setPage(1);
          }}
          aria-label="Size of change"
        >
          <option value="any">Any size</option>
          <option value="small">Up to 5%</option>
          <option value="large">More than 5%</option>
        </select>

        <span className={s.count}>
          {res.data
            ? `${res.data.total} ${plural(res.data.total, 'suggestion')} waiting`
            : ' '}
        </span>
      </div>

      {/* ── Bulk bar ──────────────────────────────────────────────────── */}
      {selectedVisible.length > 0 && (
        <div className={s.bulk}>
          <span className={s.bulkCount}>
            {selectedVisible.length} selected
          </span>
          <span className={s.bulkNote}>
            Approved prices are published to the shelf labels immediately.
          </span>
          <span className={s.bulkActions}>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
              Clear
            </Button>
            <Button
              size="sm"
              className={s.bulkReject}
              busy={bulkBusy}
              disabled={!canDecide}
              onClick={() => bulk('reject')}
            >
              Reject selected
            </Button>
            <Button size="sm" busy={bulkBusy} disabled={!canDecide} onClick={() => bulk('approve')}>
              Approve {selectedVisible.length} at suggested
            </Button>
          </span>
        </div>
      )}

      {!canDecide && (
        <p className={s.readOnly}>
          You are signed in as {user.role}. Only a manager can approve, override or
          reject a price — every decision has a name against it, so it has to be
          the name of someone with the authority to make it.
        </p>
      )}

      {/* ── Table ─────────────────────────────────────────────────────── */}
      {res.loading ? (
        <SkeletonRows rows={7} columns={6} />
      ) : rows.length === 0 ? (
        <EmptyState
          title={query || direction !== 'any' || size !== 'any'
            ? 'No suggestions match these filters'
            : 'No suggestions right now'}
          body={
            query || direction !== 'any' || size !== 'any'
              ? 'Clear the filters to see the whole queue.'
              : 'Every proposal the agent has made has been decided. New ones arrive as stock, sales and expiry change.'
          }
          actions={
            query || direction !== 'any' || size !== 'any' ? (
              <Button
                variant="secondary"
                onClick={() => {
                  setQ('');
                  setDirection('any');
                  setSize('any');
                  setPage(1);
                }}
              >
                Clear filters
              </Button>
            ) : (
              <Button variant="secondary" onClick={() => navigate('/activity')}>
                See what was applied
              </Button>
            )
          }
        />
      ) : (
        <div className={`card ${s.tableCard}`}>
          <div className={s.scroller}>
            <table className={s.table}>
              <thead>
                <tr>
                  <th className={s.thCheck}>
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      aria-label="Select every suggestion on this page"
                    />
                  </th>
                  <th className={s.th}>Product</th>
                  <th className={`${s.th} ${s.right}`}>Current</th>
                  <th className={`${s.th} ${s.right}`}>Suggested</th>
                  <th className={`${s.th} ${s.right}`}>Change</th>
                  <th className={s.th}>Why</th>
                  <th className={s.th}>Confidence</th>
                  <th className={s.th}>Lapses</th>
                  <th className={s.thActions}>
                    <span className="srOnly">Decision</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const open = () => navigate(`/suggestions/${row.decision_id}`);
                  const busy = busyIds.has(row.decision_id);
                  const remaining = countdown(row.expires_at);
                  return (
                    <tr
                      key={row.decision_id}
                      data-selected={selected.has(row.decision_id) || undefined}
                    >
                      <td className={s.tdCheck}>
                        <input
                          type="checkbox"
                          checked={selected.has(row.decision_id)}
                          onChange={() => toggleRow(row.decision_id)}
                          aria-label={`Select ${row.name}`}
                        />
                      </td>

                      <td className={s.td} onClick={open}>
                        <div className={s.product}>
                          <span className={s.productName}>{row.name}</span>
                          <span className={s.productMeta}>
                            <CategoryPill category={row.category} />
                            <span className={s.sku}>{row.sku ?? row.product_id}</span>
                            {row.slot_code && <span className={s.sku}>{row.slot_code}</span>}
                          </span>
                        </div>
                      </td>

                      <td className={`${s.td} ${s.right} ${s.current}`} onClick={open}>
                        {money(row.current_price)}
                      </td>

                      <td className={`${s.td} ${s.right} ${s.suggested}`} onClick={open}>
                        {money(row.proposed_price)}
                      </td>

                      <td className={`${s.td} ${s.right}`} onClick={open}>
                        <span className={s.pct} data-dir={row.delta_pct < 0 ? 'down' : 'up'}>
                          {pct(row.delta_pct)}
                        </span>
                        {/* Out of band is why a human is looking at this row at
                            all, so it is stated on the row rather than only in
                            the detail view. */}
                        {row.out_of_band && (
                          <span className={s.outOfBand} title="Outside this product's price band">
                            Outside band
                          </span>
                        )}
                      </td>

                      <td className={`${s.td} ${s.why}`} onClick={open}>
                        <span className={s.whyText}>{row.why.text}</span>
                        {row.why.source === 'composed' && (
                          <span
                            className={s.whyComposed}
                            title="The agent sent a short reason tag, not this sentence. The dashboard composed it from the tag, the explanation vector and the product's own figures."
                          >
                            composed by the dashboard
                          </span>
                        )}
                        {row.explanation_missing && (
                          <span className={s.noExplanation}>
                            No explanation arrived for this proposal
                          </span>
                        )}
                      </td>

                      <td className={s.td} onClick={open}>
                        <Confidence
                          value={row.agent_confidence}
                          reported={row.confidence_reported}
                        />
                      </td>

                      <td className={s.td} onClick={open}>
                        {remaining ? (
                          <span className={s.lapses}>in {remaining}</span>
                        ) : (
                          <StatusTag status="expired" />
                        )}
                      </td>

                      {/* Icon-only, 31x31, gap 7. No hover-expand: the handoff
                          tried one and removed it. */}
                      <td className={s.tdActions}>
                        <div className={s.actions}>
                          <button
                            type="button"
                            className={s.reject}
                            title={`Reject the suggestion for ${row.name}`}
                            aria-label={`Reject the suggestion for ${row.name}`}
                            disabled={busy || !canDecide}
                            onClick={() => decide(row.decision_id, 'reject', row.name)}
                          >
                            <Icon.cross />
                          </button>
                          <button
                            type="button"
                            className={s.approve}
                            title={`Approve ${row.name} at ${money(row.proposed_price)}`}
                            aria-label={`Approve ${row.name} at ${money(row.proposed_price)}`}
                            disabled={busy || !canDecide}
                            onClick={() => decide(row.decision_id, 'approve', row.name)}
                          >
                            {busy ? <span className={s.rowSpinner} /> : <Icon.check />}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <Pagination
            page={res.data!.page}
            pages={res.data!.pages}
            total={res.data!.total}
            pageSize={pageSize}
            onPage={setPage}
            onPageSize={(n) => {
              setPageSize(n);
              setPage(1); // changing rows per page resets to page 1
            }}
          />
        </div>
      )}

      <p className={s.foot}>
        A suggestion that lapses undecided changes nothing — the shelf keeps the
        price it has. Margin and band checks still apply to a price you type
        yourself.
      </p>
    </>
  );
}

/** Exported for the detail screen's sidebar, which shows the same margin test. */
export function MarginNote({
  marginPct,
  floorPct,
}: {
  marginPct: number | null;
  floorPct: number | null;
}) {
  if (marginPct === null) return null;
  const below = floorPct !== null && marginPct < floorPct;
  return (
    <span className={below ? s.marginBad : s.marginOk}>
      {pctPlain(marginPct)}
      {floorPct !== null && ` vs ${pctPlain(floorPct)} floor`}
    </span>
  );
}
