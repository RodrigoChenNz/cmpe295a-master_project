/* 10 Activity log.
 *
 * A time-first view, which is why it is its own screen rather than a tab on
 * Product detail: the task starts as "a customer is disputing a price they saw
 * on Tuesday" and nobody knows the SKU yet.
 *
 * One toolbar row — filters and search on the left, the entry count, then the ⋯
 * menu. These used to be header buttons and the handoff moved them here.
 * Ten rows per page, per the handoff's default.
 */

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import { dateTime, money, pct, plural } from '../lib/format';
import { useDebounced, useResource } from '../lib/useStream';
import { usePageChrome, useStreamEvents } from '../components/AppShell';
import {
  Button, EmptyState, ErrorState, MoreMenu, Pagination, SearchInput, SkeletonRows, StatusTag,
} from '../components/ui';
import s from './Activity.module.css';

type Range = 'today' | '7d' | '30d' | '90d' | 'all';
type Decision = 'all' | 'suggested' | 'edited' | 'auto' | 'manual';
type Shelf = 'all' | 'confirmed' | 'pending' | 'failed' | 'none';

export function Activity() {
  const [q, setQ] = useState('');
  const [range, setRange] = useState<Range>('7d');
  const [decision, setDecision] = useState<Decision>('all');
  const [shelf, setShelf] = useState<Shelf>('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const query = useDebounced(q);
  const filters = { q: query, range, decision, shelf };

  const res = useResource(
    () => api.activity({ ...filters, page, page_size: pageSize }),
    [query, range, decision, shelf, page, pageSize],
  );
  useStreamEvents(['price', 'labels'], res.reload);

  usePageChrome({ title: 'Activity log' });

  const rows = res.data?.rows ?? [];
  const reset = () => {
    setQ('');
    setRange('7d');
    setDecision('all');
    setShelf('all');
    setPage(1);
  };

  if (res.error) {
    return (
      <ErrorState
        title="Cannot load the activity log"
        body={res.error}
        actions={<Button onClick={res.reload}>Try again</Button>}
      />
    );
  }

  return (
    <>
      <div className={s.toolbar}>
        <select
          className="field"
          value={range}
          onChange={(e) => {
            setRange(e.target.value as Range);
            setPage(1);
          }}
          aria-label="Time range"
        >
          <option value="today">Today</option>
          <option value="7d">Last 7 days</option>
          <option value="30d">Last 30 days</option>
          <option value="90d">Last 90 days</option>
          <option value="all">All time</option>
        </select>

        <SearchInput
          value={q}
          onChange={(v) => {
            setQ(v);
            setPage(1);
          }}
          width="220px"
          placeholder="Product or label ID"
          aria-label="Search by product or label"
        />

        <select
          className="field"
          value={decision}
          onChange={(e) => {
            setDecision(e.target.value as Decision);
            setPage(1);
          }}
          aria-label="Decision type"
        >
          <option value="all">All decisions</option>
          <option value="suggested">Approved as suggested</option>
          <option value="edited">Approved at an edited price</option>
          <option value="auto">Applied by standing policy</option>
          <option value="manual">Set by a manager, no proposal</option>
        </select>

        <select
          className="field"
          value={shelf}
          onChange={(e) => {
            setShelf(e.target.value as Shelf);
            setPage(1);
          }}
          aria-label="Shelf result"
        >
          <option value="all">Any shelf result</option>
          <option value="confirmed">Confirmed on shelf</option>
          <option value="pending">Awaiting confirmation</option>
          <option value="failed">Failed to update</option>
          <option value="none">No label paired</option>
        </select>

        <span className={s.count}>
          {res.data ? `${res.data.total.toLocaleString('en-US')} ${plural(res.data.total, 'entry', 'entries')}` : ' '}
        </span>

        <MoreMenu
          title="More actions"
          items={[
            {
              label: 'Export this view as CSV',
              // A URL, not a fetch: the browser's own download handling beats
              // anything this app would do with a blob, and the export carries
              // exactly the filters on screen.
              href: api.activityCsvUrl(filters),
              onSelect: () => undefined,
            },
            { label: 'Print this range', onSelect: () => window.print() },
          ]}
        />
      </div>

      {res.loading ? (
        <SkeletonRows rows={10} columns={6} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="Nothing in this range"
          body="No price reached a shelf in the window and filters you have selected."
          actions={
            <Button variant="secondary" onClick={reset}>
              Reset filters
            </Button>
          }
        />
      ) : (
        <div className={`card ${s.tableCard}`}>
          <div className={s.scroller}>
            <table className={s.table}>
              <thead>
                <tr>
                  <th className={s.th}>Timestamp</th>
                  <th className={s.th}>Product</th>
                  <th className={`${s.th} ${s.right}`}>Old</th>
                  <th className={`${s.th} ${s.right}`}>New</th>
                  <th className={`${s.th} ${s.right}`}>Change</th>
                  <th className={s.th}>Decision</th>
                  <th className={s.th}>Reason on the label</th>
                  <th className={s.th}>Label</th>
                  <th className={s.th}>Shelf result</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td className={`${s.td} ${s.tsCell}`}>{dateTime(row.ts)}</td>
                    <td className={s.td}>
                      <Link className={s.productLink} to={`/products/${row.product_id}`}>
                        {row.product_name}
                      </Link>
                    </td>
                    <td className={`${s.td} ${s.right} ${s.oldCell}`}>{money(row.old_price)}</td>
                    <td className={`${s.td} ${s.right} ${s.newCell}`}>{money(row.new_price)}</td>
                    <td className={`${s.td} ${s.right}`}>
                      <span
                        className={s.pct}
                        data-dir={
                          row.change_pct === null ? 'flat' : row.change_pct < 0 ? 'down' : 'up'
                        }
                      >
                        {pct(row.change_pct, 2)}
                      </span>
                    </td>
                    <td className={s.td}>
                      <span className={s.decision}>
                        {/* Who decided, and on what basis. The three cases the
                            contract distinguishes read differently here because
                            they mean different things for accountability. */}
                        {row.approval === 'auto' ? (
                          <>
                            <span className={s.who}>Standing policy</span>
                            <span className={s.basis}>
                              {row.agent_version ?? 'agent'} · no human needed
                            </span>
                          </>
                        ) : row.approval === 'override' ? (
                          <>
                            <span className={s.who}>{row.approved_by_name ?? row.approved_by}</span>
                            <span className={s.basis}>edited the agent&rsquo;s price</span>
                          </>
                        ) : row.decision_id === null ? (
                          <>
                            <span className={s.who}>{row.approved_by_name ?? row.approved_by}</span>
                            <span className={s.basis}>set it directly, no proposal</span>
                          </>
                        ) : (
                          <>
                            <span className={s.who}>{row.approved_by_name ?? row.approved_by}</span>
                            <span className={s.basis}>approved as suggested</span>
                          </>
                        )}
                      </span>
                    </td>
                    <td className={`${s.td} ${s.reasonCell}`}>{row.reason_tag}</td>
                    <td className={`${s.td} ${s.labelCell}`}>
                      {row.label_id ? (
                        <Link className={s.labelLink} to={`/labels?q=${row.label_id}`}>
                          {row.label_id}
                        </Link>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className={s.td}>
                      <StatusTag
                        status={row.shelf_result ?? 'unassigned'}
                        label={row.shelf_result === null ? 'No label' : undefined}
                      />
                    </td>
                  </tr>
                ))}
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
              setPage(1);
            }}
          />
        </div>
      )}

      <p className={s.foot}>
        Every row is a price that was published to the shelf network, with the
        name of whoever approved it. &ldquo;Confirmed&rdquo; means the label
        acknowledged the push — it is not a claim about what the glass shows right
        now. Rejections and lapsed proposals changed no price and so are not here;
        they are in the per-product log and the audit trail.
      </p>
    </>
  );
}
