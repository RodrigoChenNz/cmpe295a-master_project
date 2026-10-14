/* 07 Shelf labels — the shelf network, as the console believes it to be.
 *
 * Everything here is belief, not observation. The dashboard and the edge nodes
 * are independent MQTT subscribers and the contract carries no acknowledgement,
 * so "online" means "we heard from it inside the stale window" and nothing
 * stronger. The screen is worded accordingly.
 *
 * The donut is a filter, not a chart: clicking a legend row narrows the table
 * to that status and dims the other arcs. Its counts respond to the search and
 * the location, but **not** to the status facet they themselves drive — a count
 * that changes when you click the row beside it is unreadable.
 */

import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import {
  donutSegments, labelDisplayName, plural, relative, rssiBars, isManagerRole,
} from '../lib/format';
import { useDebounced, useResource } from '../lib/useStream';
import { usePageChrome, useShell, useStreamEvents } from '../components/AppShell';
import {
  Battery, Button, Donut, EmptyState, ErrorState, Pagination, SearchInput,
  SkeletonRows, StatusTag,
} from '../components/ui';
import type { LocationNode } from '../lib/types';
import s from './Labels.module.css';

/** The five statuses, in the order the legend reads them. */
const SEGMENTS = [
  { key: 'online', label: 'Online', tone: 'green' },
  { key: 'syncing', label: 'Awaiting sync', tone: 'amber' },
  { key: 'failed', label: 'Failed', tone: 'red' },
  { key: 'offline', label: 'Offline', tone: 'muted' },
  { key: 'unassigned', label: 'Unassigned', tone: 'muted' },
];

type SortKey = 'attention' | 'label_id' | 'product' | 'slot' | 'last_seen' | 'rssi' | 'battery' | 'firmware';

export function Labels() {
  const navigate = useNavigate();
  const { user, toast } = useShell();
  const canResend = isManagerRole(user.role);
  const [params] = useSearchParams();

  const [q, setQ] = useState('');
  const [statusFilter, setStatusFilter] = useState<string | null>(
    () => params.get('status'),
  );
  const [location, setLocation] = useState('');
  const [sort, setSort] = useState<SortKey>('attention');
  const [dir, setDir] = useState<'asc' | 'desc'>('asc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [resending, setResending] = useState(false);

  const query = useDebounced(q);

  const res = useResource(
    () =>
      api.labels({
        q: query,
        status: statusFilter ? [statusFilter] : [],
        location: location || undefined,
        sort,
        dir,
        page,
        page_size: pageSize,
      }),
    [query, statusFilter, location, sort, dir, page, pageSize],
  );
  const places = useResource(() => api.locations(), []);

  useStreamEvents(['labels', 'price', 'broker', 'locations'], res.reload);

  usePageChrome({ title: 'Shelf labels' }, []);

  const rows = res.data?.rows ?? [];
  const counts = res.data?.by_status ?? {};
  const network = res.data?.network;

  const { segments, total } = useMemo(
    () => donutSegments(counts, SEGMENTS),
    [counts],
  );

  // Every level but the lowest (or the only one): a single bay has too few
  // labels to be a useful filter, and the select would run to dozens of entries.
  const placeOptions = useMemo(() => {
    const lowest = places.data?.levels.length ?? 0;
    const out: { id: string; label: string }[] = [];
    const walk = (nodes: LocationNode[]) => {
      for (const n of nodes) {
        if (n.depth === lowest && lowest > 1) continue;
        out.push({
          id: n.location_id,
          // Non-breaking spaces, because a select collapses ordinary ones.
          label: `${'\u00a0\u00a0\u00a0'.repeat(n.depth - 1)}${n.name} (${n.level_name})`,
        });
        walk(n.children);
      }
    };
    walk(places.data?.tree ?? []);
    return out;
  }, [places.data]);

  const sortBy = (key: SortKey) => {
    if (sort === key) setDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSort(key);
      setDir(key === 'battery' || key === 'rssi' ? 'asc' : 'asc');
    }
    setPage(1);
  };

  const resendAll = async () => {
    const ids = rows.filter((r) => r.product_id).map((r) => r.label_id);
    if (ids.length === 0) {
      toast('Nothing on this page has a product paired.', 'bad');
      return;
    }
    setResending(true);
    try {
      const result = await api.resendLabels(ids);
      toast(
        result.skipped.length === 0
          ? `Re-published the current price to ${result.count} ${plural(result.count, 'label')}.`
          : `${result.count} sent, ${result.skipped.length} skipped: ${result.skipped[0]?.reason ?? ''}`,
        result.skipped.length === 0 ? 'ok' : 'bad',
      );
      res.reload();
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Resend failed.', 'bad');
    } finally {
      setResending(false);
    }
  };

  if (res.error) {
    return (
      <ErrorState
        title="Cannot reach the shelf network"
        body={res.error}
        actions={<Button onClick={res.reload}>Try again</Button>}
      />
    );
  }

  return (
    <>
      <div className={s.topRow}>
        {/* ── Operational health ─────────────────────────────────────── */}
        <section className={`card ${s.health}`}>
          <div className={s.healthHead}>
            <h2 className={s.cardTitle}>Operational health</h2>
            {statusFilter && (
              <button
                type="button"
                className={s.clearFilter}
                onClick={() => {
                  setStatusFilter(null);
                  setPage(1);
                }}
              >
                Clear filter
              </button>
            )}
          </div>

          <div className={s.healthBody}>
            <Donut
              segments={segments}
              total={total}
              centreLabel="Total devices"
              activeKey={statusFilter}
            />

            <div className={s.legend}>
              {SEGMENTS.map((seg) => {
                const value = counts[seg.key] ?? 0;
                const pct = total ? Math.round((value / total) * 100) : 0;
                const active = statusFilter === seg.key;
                return (
                  <button
                    type="button"
                    key={seg.key}
                    className={s.legendRow}
                    data-active={active || undefined}
                    aria-pressed={active}
                    onClick={() => {
                      setStatusFilter(active ? null : seg.key);
                      setPage(1);
                    }}
                  >
                    <span className={s.legendDot} data-tone={seg.tone} aria-hidden="true" />
                    <span className={s.legendLabel}>{seg.label}</span>
                    <span className={`${s.legendCount} tnum`} data-tone={seg.tone}>
                      {value}
                    </span>
                    <span className={`${s.legendPct} tnum`}>{pct}%</span>
                  </button>
                );
              })}
            </div>
          </div>
        </section>

        {/* ── Shelf network ──────────────────────────────────────────── */}
        <section className={`card ${s.network}`} data-down={network && !network.connected ? '' : undefined}>
          <div className={s.networkHead}>
            <h2 className={s.cardTitle}>Shelf network</h2>
            <StatusTag
              status={network?.connected ? 'connected' : 'disconnected'}
              label={network?.connected ? 'Connected' : 'Disconnected'}
            />
          </div>

          <dl className={s.networkRows}>
            <Row label="Broker">
              {/* What the running bridge is using, not what Settings stores —
                  they differ until the service restarts. */}
              <span className={s.mono}>
                {network?.host ?? '—'}:{network?.port ?? '—'}
              </span>
            </Row>
            <Row label="Protocol">{network?.tls ? 'MQTT over TLS' : 'MQTT, no TLS'}</Row>
            <Row label="Last message">
              {network?.last_message_at ? relative(network.last_message_at) : 'Nothing yet'}
            </Row>
          </dl>

          {network && network.queued > 0 && (
            <p className={s.queued}>
              {network.queued} {plural(network.queued, 'price')} approved and queued —
              nothing reaches a shelf until the link returns.
            </p>
          )}

          <div className={s.networkFoot}>
            <Button size="toolbar" variant="ghost" onClick={() => navigate('/settings')}>
              Broker settings
            </Button>
          </div>
        </section>
      </div>

      {/* ── Toolbar ───────────────────────────────────────────────────── */}
      <div className={s.toolbar}>
        <SearchInput
          value={q}
          onChange={(v) => {
            setQ(v);
            setPage(1);
          }}
          width="220px"
          placeholder="Label, product or position"
          aria-label="Search labels"
        />

        <select
          className="field"
          value={location}
          onChange={(e) => {
            setLocation(e.target.value);
            setPage(1);
          }}
          aria-label="Filter by location"
        >
          <option value="">All locations</option>
          {placeOptions.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>

        <span className={s.count}>
          {res.data
            ? `${res.data.total} of ${total} ${plural(total, 'label')}`
            : ' '}
        </span>

        <span className={s.toolbarRight}>
          <Button
            size="toolbar"
            variant="secondary"
            busy={resending}
            disabled={!canResend}
            title={canResend ? undefined : 'Only a manager can publish a price'}
            onClick={resendAll}
          >
            Resend all
          </Button>
        </span>
      </div>

      {/* ── Table ─────────────────────────────────────────────────────── */}
      {res.loading ? (
        <SkeletonRows rows={8} columns={6} />
      ) : rows.length === 0 ? (
        <EmptyState
          title={statusFilter || query || location ? 'No label matches that' : 'No shelf labels yet'}
          body={
            statusFilter || query || location
              ? 'Clear the filters to see the whole network.'
              : 'Labels appear here once the edge gateway reports them.'
          }
          actions={
            statusFilter || query || location ? (
              <Button
                variant="secondary"
                onClick={() => {
                  setQ('');
                  setStatusFilter(null);
                  setLocation('');
                  setPage(1);
                }}
              >
                Clear filters
              </Button>
            ) : undefined
          }
        />
      ) : (
        <div className={`card ${s.tableCard}`}>
          <div className={s.scroller}>
            <table className={s.table}>
              <thead>
                <tr>
                  <Th label="Label ID" col="label_id" sort={sort} dir={dir} onSort={sortBy} first />
                  <Th label="Assigned product" col="product" sort={sort} dir={dir} onSort={sortBy} />
                  <Th label="Shelf" col="slot" sort={sort} dir={dir} onSort={sortBy} />
                  <Th label="Last seen" col="last_seen" sort={sort} dir={dir} onSort={sortBy} />
                  <Th label="Signal" col="rssi" sort={sort} dir={dir} onSort={sortBy} />
                  <Th label="Battery" col="battery" sort={sort} dir={dir} onSort={sortBy} right />
                  <th className={s.th}>Display</th>
                  <Th label="Firmware" col="firmware" sort={sort} dir={dir} onSort={sortBy} last />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const tone =
                    row.status === 'failed' ? 'failed' : row.status === 'offline' ? 'offline' : undefined;
                  const bars = rssiBars(row.rssi_dbm);
                  return (
                    <tr
                      key={row.label_id}
                      className={s.row}
                      data-tone={tone}
                      onClick={() => navigate(`/labels/${row.label_id}`)}
                    >
                      <td className={`${s.td} ${s.idCell}`}>
                        <span className={s.idInner} title={row.status}>
                          <span
                            className={s.statusDot}
                            data-status={row.status}
                            aria-hidden="true"
                          />
                          <span className={s.labelId}>{row.label_id}</span>
                        </span>
                      </td>
                      <td className={s.td}>
                        {row.product_name ?? <span className={s.none}>Not paired</span>}
                      </td>
                      <td className={s.td} title={row.location_path ?? undefined}>
                        <span className={s.mono}>{row.slot_code ?? '—'}</span>
                        {row.location_path && (
                          <span className={s.none}> · {row.location_path}</span>
                        )}
                      </td>
                      <td className={s.td}>{relative(row.last_seen_at)}</td>
                      <td className={s.td}>
                        <span className={s.signal}>
                          <span className={s.bars} aria-hidden="true">
                            {[1, 2, 3].map((n) => (
                              <span
                                key={n}
                                className={s.bar}
                                data-h={n}
                                data-on={n <= bars || undefined}
                              />
                            ))}
                          </span>
                          <span className={s.mono}>
                            {row.rssi_dbm === null ? '—' : `${String(row.rssi_dbm).replace('-', '−')} dBm`}
                          </span>
                        </span>
                      </td>
                      <td className={`${s.td} ${s.right}`}>
                        <Battery pct={row.battery_pct} />
                      </td>
                      <td className={`${s.td} ${s.nowrap}`}>{labelDisplayName(row.kind)}</td>
                      <td className={`${s.td} ${s.mono}`}>{row.firmware ?? '—'}</td>
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
              setPage(1);
            }}
          />
        </div>
      )}

      <p className={s.foot}>
        A label&rsquo;s status is what this console last heard, not what the glass
        is showing. No label acknowledges a price, so &ldquo;online&rdquo; means it
        reported in recently — a device can be online and still displaying a
        stale price from flash.
      </p>
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={s.networkRow}>
      <dt className="monoLabel">{label}</dt>
      <dd className={s.networkValue}>{children}</dd>
    </div>
  );
}

function Th({
  label,
  col,
  sort,
  dir,
  onSort,
  right,
  first,
  last,
}: {
  label: string;
  col: SortKey;
  sort: SortKey;
  dir: 'asc' | 'desc';
  onSort: (c: SortKey) => void;
  right?: boolean;
  first?: boolean;
  last?: boolean;
}) {
  const active = sort === col;
  return (
    <th
      className={`${s.th} ${right ? s.right : ''} ${first ? s.thFirst : ''} ${last ? s.thLast : ''}`}
      aria-sort={active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      <button type="button" className={s.sortBtn} data-active={active || undefined} onClick={() => onSort(col)}>
        {label}
        <span className={s.sortArrow} data-active={active || undefined} aria-hidden="true">
          {active ? (dir === 'asc' ? '↑' : '↓') : '↕'}
        </span>
      </button>
    </th>
  );
}
