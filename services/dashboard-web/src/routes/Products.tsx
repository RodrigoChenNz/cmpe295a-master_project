/* 05 Products — "most recently revised; build this carefully".
 *
 * The parts that are easy to get wrong, and what this does about them:
 *
 * - **Panel counts are computed against the search-filtered catalogue, not the
 *   panel-filtered one.** A count that changes when you tick the box beside it
 *   makes the panel unreadable. The API has a separate /facets endpoint for
 *   exactly this reason, and it takes only `q`.
 * - **"All …" rows have no checkbox.** They show weight 600 while that group has
 *   nothing selected, and clicking one clears the group.
 * - **The toolbar is sticky at `top: var(--header-h)`**, with negative margins so
 *   rows scroll under it cleanly rather than appearing from behind a gap.
 * - **Bulk selection is a floating island, not an inline bar.** The prototype
 *   had an inline bar; the handoff README is explicit that it is an island, and
 *   ships the islandIn/islandOut keyframes for it.
 * - Escape clears the selection, and so does a click outside the table.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import { int, money, plural } from '../lib/format';
import { useDebounced, useResource } from '../lib/useStream';
import { usePageChrome, useShell, useStreamEvents } from '../components/AppShell';
import {
  Button, CategoryPill, EmptyState, ErrorState, Icon, MarginPill, Modal, MoreMenu,
  Pagination, SearchInput, SkeletonRows, StatusTag, StockCell,
} from '../components/ui';
import type { PriceReview, Product, SavedView, StockHealth } from '../lib/types';
import s from './Products.module.css';

type SortKey = 'name' | 'sku' | 'category' | 'price' | 'margin' | 'stock' | 'supplier';

const SAVED_VIEWS: { key: SavedView; label: string }[] = [
  { key: 'all', label: 'All products' },
  { key: 'low', label: 'Low stock' },
  { key: 'review', label: 'Pricing review' },
];

const STOCK_ROWS: { key: StockHealth; label: string; tone: string }[] = [
  { key: 'in', label: 'In stock', tone: 'green' },
  { key: 'low', label: 'Low stock', tone: 'amber' },
  { key: 'out', label: 'Out of stock', tone: 'red' },
];

const REVIEW_ROWS: { key: PriceReview; label: string; tone: string }[] = [
  { key: 'review', label: 'Needs review', tone: 'amber' },
  { key: 'up', label: 'Price increased', tone: 'green' },
  { key: 'down', label: 'Price decreased', tone: 'red' },
];

export function Products() {
  const navigate = useNavigate();
  const { user, toast } = useShell();
  const canEdit = user.role !== 'viewer';
  const canDelete = user.role === 'manager';

  // Deep links from the dashboard's attention panel land here with filters
  // already applied, so the route is the source of truth for the initial state.
  const [params] = useSearchParams();

  const [q, setQ] = useState('');
  const [catSel, setCatSel] = useState<string[]>([]);
  const [stockSel, setStockSel] = useState<StockHealth[]>(
    () => (params.getAll('stock') as StockHealth[]) ?? [],
  );
  const [reviewSel, setReviewSel] = useState<PriceReview[]>([]);
  const [catQ, setCatQ] = useState('');
  const [view, setView] = useState<SavedView>(
    () => (params.get('view') as SavedView) ?? 'all',
  );
  const [shut, setShut] = useState<Record<string, boolean>>({});
  const [sort, setSort] = useState<SortKey>('name');
  const [dir, setDir] = useState<'asc' | 'desc'>('asc');
  const [mode, setMode] = useState<'table' | 'cards'>('table');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [islandMsg, setIslandMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const query = useDebounced(q);
  const tableRef = useRef<HTMLDivElement>(null);
  const islandRef = useRef<HTMLDivElement>(null);

  const list = useResource(
    () =>
      api.products({
        q: query,
        category: catSel,
        stock: stockSel,
        review: reviewSel,
        view,
        sort,
        dir,
        page,
        page_size: pageSize,
      }),
    [query, catSel.join(','), stockSel.join(','), reviewSel.join(','), view, sort, dir, page, pageSize],
  );

  // Facets depend on the search only, which is the whole point.
  const facets = useResource(() => api.facets(query), [query]);

  const reload = useCallback(() => {
    list.reload();
    facets.reload();
  }, [list.reload, facets.reload]);

  useStreamEvents(['products', 'price', 'proposal', 'suggestions', 'labels'], reload);

  usePageChrome(
    {
      title: 'Products',
      actions: canEdit ? (
        <Button onClick={() => toast('Adding products by hand is not built yet — import a CSV or seed the catalogue.', 'bad')}>
          <span className={s.plus}>+</span> Add product
        </Button>
      ) : undefined,
    },
    [canEdit],
  );

  const rows = list.data?.rows ?? [];

  /* Clearing the selection. Escape and an outside click, per the handoff —
   * "outside" meaning outside both the table and the island, because clicking
   * the island is how you use it. */
  useEffect(() => {
    if (selected.size === 0) return;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelected(new Set());
    };
    const onPointer = (e: PointerEvent) => {
      const target = e.target as Node;
      if (tableRef.current?.contains(target)) return;
      if (islandRef.current?.contains(target)) return;
      setSelected(new Set());
    };

    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onPointer);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onPointer);
    };
  }, [selected.size]);

  const visibleIds = useMemo(() => rows.map((r) => r.product_id), [rows]);
  const selectedVisible = visibleIds.filter((id) => selected.has(id));
  const allSelected = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;

  const toggleRow = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const sortBy = (key: SortKey) => {
    if (sort === key) setDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSort(key);
      setDir('asc');
    }
    setPage(1);
  };

  const flash = (message: string) => {
    setIslandMsg(message);
    window.setTimeout(() => setIslandMsg(null), 2500);
  };

  /* A group's "All" row clears that group. Nothing selected in a group means
   * "no constraint from this group", which is the same thing as all of it. */
  const clearGroup = (group: 'cat' | 'stock' | 'review') => {
    if (group === 'cat') setCatSel([]);
    if (group === 'stock') setStockSel([]);
    if (group === 'review') setReviewSel([]);
    setPage(1);
  };

  const clearAll = () => {
    setQ('');
    setCatQ('');
    setCatSel([]);
    setStockSel([]);
    setReviewSel([]);
    setView('all');
    setPage(1);
  };

  const anyFilter =
    q !== '' || catSel.length > 0 || stockSel.length > 0 || reviewSel.length > 0 || view !== 'all';

  const doArchive = async () => {
    setBusy(true);
    try {
      const result = await api.archiveProducts(selectedVisible);
      flash(`${result.count} ${plural(result.count, 'product')} removed`);
      setSelected(new Set());
      setConfirmDelete(false);
      reload();
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Could not remove them.', 'bad');
    } finally {
      setBusy(false);
    }
  };

  const doResend = async () => {
    const labelIds = rows
      .filter((r) => selected.has(r.product_id) && r.label_id)
      .map((r) => r.label_id!);
    if (labelIds.length === 0) {
      flash('None of those have a label paired');
      return;
    }
    try {
      const result = await api.resendLabels(labelIds);
      flash(`Queued for ${result.count} ${plural(result.count, 'label')}`);
      reload();
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Resend failed.', 'bad');
    }
  };

  const categories = facets.data?.categories ?? [];
  const shownCategories = categories.filter((c) =>
    catQ ? c.category.toLowerCase().includes(catQ.toLowerCase()) : true,
  );

  if (list.error) {
    return (
      <ErrorState
        title="Cannot load the catalogue"
        body={list.error}
        actions={<Button onClick={reload}>Try again</Button>}
      />
    );
  }

  return (
    <div className={s.layout}>
      {/* ── Views & filters ───────────────────────────────────────────── */}
      <aside className={`card ${s.panel}`}>
        <div className={s.panelTitle}>Views &amp; filters</div>

        <div className={s.savedViews}>
          <span className="monoLabel">Saved views</span>
          {SAVED_VIEWS.map((v) => (
            <button
              type="button"
              key={v.key}
              className={s.savedView}
              data-active={view === v.key || undefined}
              onClick={() => {
                setView(v.key);
                setPage(1);
              }}
            >
              {v.label}
              <span className={s.savedCount}>{int(facets.data?.views[v.key] ?? null)}</span>
            </button>
          ))}
        </div>

        <Section
          title="Categories"
          open={!shut.cat}
          onToggle={() => setShut((p) => ({ ...p, cat: !p.cat }))}
        >
          <input
            className={`field ${s.catSearch}`}
            placeholder="Search categories"
            value={catQ}
            onChange={(e) => setCatQ(e.target.value)}
            aria-label="Search categories"
          />
          <AllRow
            label="All categories"
            count={facets.data?.total ?? null}
            active={catSel.length === 0}
            onClick={() => clearGroup('cat')}
          />
          {shownCategories.map((c) => (
            <FilterRow
              key={c.category}
              label={c.category}
              count={c.count}
              checked={catSel.includes(c.category)}
              onChange={() => {
                setCatSel((prev) =>
                  prev.includes(c.category)
                    ? prev.filter((x) => x !== c.category)
                    : [...prev, c.category],
                );
                setPage(1);
              }}
            />
          ))}
          {shownCategories.length === 0 && (
            <span className={s.noMatch}>No category matches &ldquo;{catQ}&rdquo;</span>
          )}
        </Section>

        <Section
          title="Stock health"
          open={!shut.stock}
          onToggle={() => setShut((p) => ({ ...p, stock: !p.stock }))}
        >
          <AllRow
            label="All stock levels"
            count={facets.data?.total ?? null}
            active={stockSel.length === 0}
            onClick={() => clearGroup('stock')}
          />
          {STOCK_ROWS.map((r) => (
            <FilterRow
              key={r.key}
              label={r.label}
              tone={r.tone}
              count={facets.data?.stock[r.key] ?? 0}
              checked={stockSel.includes(r.key)}
              onChange={() => {
                setStockSel((prev) =>
                  prev.includes(r.key) ? prev.filter((x) => x !== r.key) : [...prev, r.key],
                );
                setPage(1);
              }}
            />
          ))}
        </Section>

        <Section
          title="Price review"
          open={!shut.review}
          onToggle={() => setShut((p) => ({ ...p, review: !p.review }))}
        >
          <AllRow
            label="All products"
            count={facets.data?.total ?? null}
            active={reviewSel.length === 0}
            onClick={() => clearGroup('review')}
          />
          {REVIEW_ROWS.map((r) => (
            <FilterRow
              key={r.key}
              label={r.label}
              tone={r.tone}
              count={facets.data?.review[r.key] ?? 0}
              checked={reviewSel.includes(r.key)}
              onChange={() => {
                setReviewSel((prev) =>
                  prev.includes(r.key) ? prev.filter((x) => x !== r.key) : [...prev, r.key],
                );
                setPage(1);
              }}
            />
          ))}
        </Section>

        <div className={s.panelFoot}>
          <Button
            variant="ghost"
            className={s.clearAll}
            disabled={!anyFilter}
            onClick={clearAll}
          >
            Clear all filters
          </Button>
        </div>
      </aside>

      {/* ── Right column ──────────────────────────────────────────────── */}
      <div className={s.rightCol}>
        <div className={s.toolbar}>
          <SearchInput
            value={q}
            onChange={(v) => {
              setQ(v);
              setPage(1);
            }}
            width="220px"
            placeholder="Search name, SKU, UPC, brand"
            aria-label="Search products"
          />

          <span className={s.toolbarRight}>
            <span className={s.segmented} role="group" aria-label="View as">
              <button
                type="button"
                className={s.segment}
                data-active={mode === 'table' || undefined}
                title="Table"
                aria-label="Table view"
                aria-pressed={mode === 'table'}
                onClick={() => setMode('table')}
              >
                <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                  <rect x="2" y="3" width="12" height="3" rx="1" />
                  <rect x="2" y="7.5" width="12" height="2.2" rx="1" />
                  <rect x="2" y="11" width="12" height="2.2" rx="1" />
                </svg>
              </button>
              <button
                type="button"
                className={s.segment}
                data-active={mode === 'cards' || undefined}
                title="Cards"
                aria-label="Card view"
                aria-pressed={mode === 'cards'}
                onClick={() => setMode('cards')}
              >
                <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                  <rect x="2" y="2.5" width="5" height="5" rx="1" />
                  <rect x="9" y="2.5" width="5" height="5" rx="1" />
                  <rect x="2" y="9" width="5" height="5" rx="1" />
                  <rect x="9" y="9" width="5" height="5" rx="1" />
                </svg>
              </button>
            </span>

            <MoreMenu
              title="Product table options"
              items={[
                {
                  label: 'Import CSV…',
                  onSelect: () => toast('CSV import is not built yet.', 'bad'),
                },
                {
                  label: 'Product fields…',
                  onSelect: () => toast('Field packs are configured in Settings.', 'bad'),
                },
                {
                  label: 'Export catalogue as CSV',
                  onSelect: () => toast('Catalogue export is not built yet.', 'bad'),
                },
              ]}
            />
          </span>
        </div>

        {list.loading ? (
          <SkeletonRows rows={8} columns={6} />
        ) : rows.length === 0 ? (
          <EmptyState
            title={q ? 'No products match this search' : 'Nothing matches these filters'}
            body={
              q
                ? 'Try a shorter term, or import your catalogue as a CSV with a name, price, cost and 16-character display name per row.'
                : 'Clear a filter or pick a different saved view.'
            }
            actions={
              <Button variant="secondary" onClick={clearAll}>
                {q ? 'Clear search' : 'Clear all filters'}
              </Button>
            }
          />
        ) : mode === 'table' ? (
          <div className={`card ${s.tableCard}`} ref={tableRef}>
            <div className={s.scroller}>
              <table className={s.table}>
                <thead>
                  <tr>
                    <th className={s.thCheck}>
                      <input
                        type="checkbox"
                        checked={allSelected}
                        onChange={() =>
                          setSelected((prev) => {
                            const next = new Set(prev);
                            if (allSelected) visibleIds.forEach((id) => next.delete(id));
                            else visibleIds.forEach((id) => next.add(id));
                            return next;
                          })
                        }
                        aria-label="Select every product on this page"
                      />
                    </th>
                    <SortTh label="Product" col="name" sort={sort} dir={dir} onSort={sortBy} />
                    <SortTh label="SKU" col="sku" sort={sort} dir={dir} onSort={sortBy} />
                    <SortTh label="Category" col="category" sort={sort} dir={dir} onSort={sortBy} />
                    <SortTh label="Price" col="price" sort={sort} dir={dir} onSort={sortBy} right />
                    <SortTh label="Margin" col="margin" sort={sort} dir={dir} onSort={sortBy} right />
                    <SortTh label="Stock" col="stock" sort={sort} dir={dir} onSort={sortBy} />
                    <SortTh label="Supplier" col="supplier" sort={sort} dir={dir} onSort={sortBy} />
                    <th className={s.th}>Label</th>
                    <th className={s.thMenu}>
                      <span className="srOnly">Row actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p) => (
                    <tr
                      key={p.product_id}
                      data-selected={selected.has(p.product_id) || undefined}
                    >
                      <td className={s.tdCheck}>
                        <input
                          type="checkbox"
                          checked={selected.has(p.product_id)}
                          onChange={() => toggleRow(p.product_id)}
                          aria-label={`Select ${p.name}`}
                        />
                      </td>
                      <td className={s.td} onClick={() => navigate(`/products/${p.product_id}`)}>
                        <span className={s.productCell}>
                          <Photo product={p} size={38} />
                          <span className={s.productName}>{p.name}</span>
                          {p.pending_count > 0 && (
                            <span className={s.reviewDot} title={`${p.pending_count} waiting`} />
                          )}
                        </span>
                      </td>
                      <td className={`${s.td} ${s.skuCell}`} onClick={() => navigate(`/products/${p.product_id}`)}>
                        {p.sku ?? p.product_id}
                      </td>
                      <td className={s.td} onClick={() => navigate(`/products/${p.product_id}`)}>
                        <CategoryPill category={p.category} />
                      </td>
                      <td className={`${s.td} ${s.priceCell}`} onClick={() => navigate(`/products/${p.product_id}`)}>
                        {money(p.current_price)}
                      </td>
                      <td className={`${s.td} ${s.right}`} onClick={() => navigate(`/products/${p.product_id}`)}>
                        <MarginPill marginPct={p.margin_pct} />
                      </td>
                      <td className={s.td} onClick={() => navigate(`/products/${p.product_id}`)}>
                        <StockCell stock={p.stock_on_hand} />
                      </td>
                      <td className={`${s.td} ${s.supplierCell}`} onClick={() => navigate(`/products/${p.product_id}`)}>
                        {p.supplier ?? '—'}
                      </td>
                      <td className={s.td} onClick={() => navigate(`/products/${p.product_id}`)}>
                        {p.label_id ? (
                          <StatusTag status={p.label_status ?? 'unassigned'} />
                        ) : (
                          <span className={s.noLabel}>No label</span>
                        )}
                      </td>
                      <td className={s.tdMenu}>
                        <MoreMenu
                          bordered={false}
                          title={`Actions for ${p.name}`}
                          items={[
                            {
                              label: 'Open product',
                              onSelect: () => navigate(`/products/${p.product_id}`),
                            },
                            {
                              label: selected.has(p.product_id) ? 'Deselect' : 'Select',
                              onSelect: () => toggleRow(p.product_id),
                            },
                            {
                              label: 'Remove from catalogue',
                              destructive: true,
                              disabled: !canDelete,
                              onSelect: () => {
                                setSelected(new Set([p.product_id]));
                                setConfirmDelete(true);
                              },
                            },
                          ]}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <Pagination
              page={list.data!.page}
              pages={list.data!.pages}
              total={list.data!.total}
              pageSize={pageSize}
              onPage={setPage}
              onPageSize={(n) => {
                setPageSize(n);
                setPage(1);
              }}
            />
          </div>
        ) : (
          <>
            <div className={s.cards} ref={tableRef}>
              {rows.map((p) => (
                <button
                  type="button"
                  className={s.productCard}
                  key={p.product_id}
                  data-selected={selected.has(p.product_id) || undefined}
                  onClick={() => navigate(`/products/${p.product_id}`)}
                >
                  <span className={s.cardTop}>
                    <Photo product={p} size={46} />
                    <span className={s.cardHeadText}>
                      <span className={s.cardName}>{p.name}</span>
                      <span className={s.cardSku}>{p.sku ?? p.product_id}</span>
                    </span>
                  </span>
                  <span className={s.cardPrices}>
                    <span className={s.cardStat}>
                      <span className="monoLabel">Price</span>
                      <span className={s.cardPrice}>{money(p.current_price)}</span>
                    </span>
                    <span className={`${s.cardStat} ${s.cardStatEnd}`}>
                      <span className="monoLabel">Stock</span>
                      <span className={s.cardStock} data-low={(p.stock_on_hand ?? 99) <= 10 || undefined}>
                        {p.stock_on_hand ?? '—'}
                      </span>
                    </span>
                  </span>
                  <span className={s.cardRows}>
                    <CardRow label="Category" value={p.category ?? '—'} />
                    <CardRow label="Brand" value={p.brand ?? '—'} />
                    <CardRow label="Cost" value={money(p.unit_cost)} />
                    <CardRow label="Margin" value={p.margin_pct === null ? '—' : `${Math.round(p.margin_pct)}%`} />
                  </span>
                </button>
              ))}
            </div>
            <div className={`card ${s.cardsPagination}`}>
              <Pagination
                page={list.data!.page}
                pages={list.data!.pages}
                total={list.data!.total}
                pageSize={pageSize}
                onPage={setPage}
                onPageSize={(n) => {
                  setPageSize(n);
                  setPage(1);
                }}
              />
            </div>
          </>
        )}
      </div>

      {/* ── Floating island ───────────────────────────────────────────── */}
      {selectedVisible.length > 0 && (
        <div className={s.island} ref={islandRef}>
          {islandMsg ? (
            <span className={s.islandMsg}>
              <span className={s.islandDot} aria-hidden="true" />
              {islandMsg}
            </span>
          ) : (
            <>
              <span className={s.islandCount}>
                {selectedVisible.length} selected
              </span>
              <button
                type="button"
                className={s.islandClear}
                onClick={() => setSelected(new Set())}
              >
                Clear
              </button>
              <span className={s.islandRule} aria-hidden="true" />
              <button
                type="button"
                className={s.islandBtn}
                disabled={!canEdit}
                onClick={() => toast('Bulk price editing is not built yet — open a product to set its price.', 'bad')}
              >
                <Icon.tag /> Edit prices
              </button>
              <button
                type="button"
                className={s.islandBtn}
                disabled={!canEdit}
                onClick={() => toast('Bulk recategorising is not built yet.', 'bad')}
              >
                <Icon.folder /> Category
              </button>
              <button
                type="button"
                className={s.islandBtn}
                disabled={!canDelete}
                onClick={doResend}
              >
                <Icon.send /> Resend
              </button>
              <MoreMenu
                up
                bordered={false}
                title="More bulk actions"
                items={[
                  {
                    label: 'Exclude from the agent',
                    disabled: !canEdit,
                    onSelect: () =>
                      toast('Exclude from the agent is set per product, on its detail page.', 'bad'),
                  },
                  {
                    label: `Remove ${selectedVisible.length} ${plural(selectedVisible.length, 'product')}`,
                    destructive: true,
                    disabled: !canDelete,
                    onSelect: () => setConfirmDelete(true),
                  },
                ]}
              />
            </>
          )}
        </div>
      )}

      {confirmDelete && (
        <Modal
          title={`Remove ${selectedVisible.length} ${plural(selectedVisible.length, 'product')}?`}
          onClose={() => setConfirmDelete(false)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setConfirmDelete(false)}>
                Keep them
              </Button>
              <Button variant="danger" busy={busy} onClick={doArchive}>
                Remove {selectedVisible.length}
              </Button>
            </>
          }
        >
          <p className={s.modalP}>
            They disappear from every list and any paired shelf label is unpaired.
            Pending suggestions for them are dropped.
          </p>
          <p className={s.modalP}>
            {/* Worth saying plainly: this is not a DELETE, and it should not be.
                price_history and the audit log reference these rows, and those
                are append-only evidence. */}
            Their price history and audit trail are kept, because a decision log
            that loses rows when a product is removed is not an audit trail.
          </p>
        </Modal>
      )}
    </div>
  );
}

/* ── Panel pieces ─────────────────────────────────────────────────────── */

function Section({
  title,
  open,
  onToggle,
  children,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className={s.section}>
      <button type="button" className={s.sectionHead} onClick={onToggle} aria-expanded={open}>
        <span className="monoLabel">{title}</span>
        <span className={s.caret} data-closed={!open || undefined} aria-hidden="true">
          ▾
        </span>
      </button>
      {open && <div className={s.sectionBody}>{children}</div>}
    </div>
  );
}

/** No checkbox, per the handoff. Bold while the group has nothing selected, and
 *  clicking it clears the group. */
function AllRow({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number | null;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button type="button" className={s.allRow} data-active={active || undefined} onClick={onClick}>
      <span className={s.filterLabel}>{label}</span>
      <span className={s.filterCount}>{int(count)}</span>
    </button>
  );
}

function FilterRow({
  label,
  count,
  checked,
  onChange,
  tone,
}: {
  label: string;
  count: number;
  checked: boolean;
  onChange: () => void;
  tone?: string;
}) {
  return (
    <label className={s.filterRow}>
      <input type="checkbox" checked={checked} onChange={onChange} />
      {tone && <span className={s.filterDot} data-tone={tone} aria-hidden="true" />}
      <span className={s.filterLabel}>{label}</span>
      <span className={s.filterCount}>{int(count)}</span>
    </label>
  );
}

/* ── Table pieces ─────────────────────────────────────────────────────── */

function SortTh({
  label,
  col,
  sort,
  dir,
  onSort,
  right,
}: {
  label: string;
  col: SortKey;
  sort: SortKey;
  dir: 'asc' | 'desc';
  onSort: (c: SortKey) => void;
  right?: boolean;
}) {
  const active = sort === col;
  return (
    <th
      className={`${s.th} ${right ? s.right : ''}`}
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

/** The photo slot. A neutral tile when there is no image, never a broken-image
 *  icon or a generated placeholder graphic — the product simply has no photo. */
function Photo({ product, size }: { product: Product; size: number }) {
  const [failed, setFailed] = useState(false);
  const style = { width: size, height: size };

  if (!product.image_url || failed) {
    return (
      <span className={s.photo} style={style} aria-hidden="true">
        <span className={s.photoInitial}>{product.name.slice(0, 1).toUpperCase()}</span>
      </span>
    );
  }
  return (
    <img
      className={s.photo}
      style={style}
      src={product.image_url}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

function CardRow({ label, value }: { label: string; value: string }) {
  return (
    <span className={s.cardRow}>
      <span className="monoLabel">{label}</span>
      <span className={s.cardRowValue}>{value}</span>
    </span>
  );
}
