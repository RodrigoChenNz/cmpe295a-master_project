/* 12 Locations — the store's layout, what sits in each position, and which
 * products live where.
 *
 * **The levels are the store's own.** On a new store this screen is the first
 * stop: a manager names the levels (Zone › Aisle › Bay by default, a Floor on
 * top for a bigger store), and until they have, nothing else — locations or
 * products — can be added. Every label on this screen reads those names; none
 * is hard-coded. Positions (the slot a label hangs in, coded like "A3-04",
 * which is what staff say out loud) go on the lowest level.
 *
 * Products are linked here too: a catalogue is usually entered first and then
 * placed, so "Products here" shows what lives under the selection and lets an
 * editor add products to it in bulk.
 *
 * **Deleting is the dangerous action.** One zone takes its aisles, its bays,
 * every position under them, and the position off every label in them. The
 * confirmation shows the real figures and echoes them back; the server
 * recomputes and refuses if they have moved, so a stale confirmation cannot
 * act on numbers nobody saw.
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import {
  isManagerRole, labelDisplayName, levelCount, levelName, levelPath, money, plural,
  relative, withArticle,
} from '../lib/format';
import { useResource } from '../lib/useStream';
import { usePageChrome, useShell, useStreamEvents } from '../components/AppShell';
import {
  Button, CategoryPill, ConfirmModal, EmptyState, ErrorState, Icon, Modal, SearchInput,
  SkeletonRows, StatusTag, Tree,
} from '../components/ui';
import { DEFAULT_LEVELS, LevelsEditor } from '../components/LevelsEditor';
import type { LocationImpact, LocationNode, LocationSlot, Product } from '../lib/types';
import type { TreeNode } from '../components/ui';
import s from './Locations.module.css';

export function Locations() {
  const navigate = useNavigate();
  const { user, toast, reloadMeta } = useShell();
  const canEdit = isManagerRole(user.role);
  // Linking products is a catalogue edit, which staff may make.
  const canLink = user.role !== 'viewer';

  const [selected, setSelected] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [treeQ, setTreeQ] = useState('');
  const [slotQ, setSlotQ] = useState('');
  const [busy, setBusy] = useState(false);

  // `addFor === null` legitimately means "add a zone at the top", so the dialog
  // needs its own open flag rather than being inferred from the target.
  const [addVisible, setAddVisible] = useState(false);
  const [addFor, setAddFor] = useState<LocationNode | null>(null);
  const [addName, setAddName] = useState('');
  const [addSlotFor, setAddSlotFor] = useState<LocationNode | null>(null);
  const [slotCode, setSlotCode] = useState('');
  const [renaming, setRenaming] = useState<LocationNode | null>(null);
  const [renameTo, setRenameTo] = useState('');
  const [deleting, setDeleting] = useState<LocationNode | null>(null);
  const [impact, setImpact] = useState<LocationImpact | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [editingLevels, setEditingLevels] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);

  const res = useResource(() => api.locations(), []);
  useStreamEvents(['locations', 'labels', 'price', 'products'], res.reload);

  const levels = res.data?.levels ?? [];
  const configured = levels.length > 0;
  const lowest = levels.length;
  /** The level a new child of `node` would be at, or null under the lowest. */
  const childLevel = (node: LocationNode | null): string | null => {
    const depth = node ? node.depth + 1 : 1;
    return depth <= lowest ? levelName(levels, depth) : null;
  };

  usePageChrome({ title: 'Locations' }, []);

  const saveLevels = (names: string[], after: () => void) =>
    run(async () => {
      await api.saveLevels(names);
      reloadMeta();
      after();
    }).catch(() => undefined);

  const tree = res.data?.tree ?? [];
  const allSlots = res.data?.slots ?? [];

  // Open every zone the first time the tree arrives, so the screen does not
  // land on a column of collapsed rows.
  useEffect(() => {
    if (tree.length && expanded.size === 0) {
      setExpanded(new Set(tree.map((z) => z.location_id)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tree.length]);

  const byId = useMemo(() => {
    const map = new Map<string, LocationNode>();
    const walk = (ns: LocationNode[]) => ns.forEach((n) => { map.set(n.location_id, n); walk(n.children); });
    walk(tree);
    return map;
  }, [tree]);

  const selectedNode = selected ? byId.get(selected) ?? null : null;

  /** The ids under the selection, so the slot table can show a whole branch. */
  const subtreeIds = useMemo(() => {
    const ids = new Set<string>();
    const walk = (n: LocationNode) => { ids.add(n.location_id); n.children.forEach(walk); };
    if (selectedNode) walk(selectedNode);
    return ids;
  }, [selectedNode]);

  const slots = useMemo(() => {
    const scoped = selectedNode
      ? allSlots.filter((x) => subtreeIds.has(x.location_id))
      : allSlots;
    const needle = slotQ.trim().toLowerCase();
    if (!needle) return scoped;
    return scoped.filter((x) =>
      [x.slot_code, x.product_name, x.label_id, x.brand, x.path]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(needle),
    );
  }, [allSlots, selectedNode, subtreeIds, slotQ]);

  // Filtering the tree expands everything, because a match three levels down is
  // invisible otherwise.
  const treeNodes: TreeNode[] = useMemo(() => {
    const needle = treeQ.trim().toLowerCase();
    const convert = (n: LocationNode): TreeNode | null => {
      const kids = n.children.map(convert).filter((x): x is TreeNode => x !== null);
      const hit = !needle || n.name.toLowerCase().includes(needle);
      if (!hit && kids.length === 0) return null;
      return {
        id: n.location_id,
        label: n.name,
        meta:
          n.descendant_slots === 0
            ? 'Empty'
            : `${n.descendant_filled}/${n.descendant_slots}`,
        children: kids,
      };
    };
    return tree.map(convert).filter((x): x is TreeNode => x !== null);
  }, [tree, treeQ]);

  useEffect(() => {
    if (treeQ.trim()) setExpanded(new Set(byId.keys()));
  }, [treeQ, byId]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setFormError(null);
    try {
      await fn();
      res.reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'Something went wrong.');
      throw err;
    } finally {
      setBusy(false);
    }
  };

  const openDelete = async (node: LocationNode) => {
    setDeleting(node);
    setImpact(null);
    setFormError(null);
    try {
      setImpact(await api.locationImpact(node.location_id));
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'Could not read what this holds.');
    }
  };

  if (res.error) {
    return (
      <ErrorState
        title="Cannot load the store layout"
        body={res.error}
        actions={<Button onClick={res.reload}>Try again</Button>}
      />
    );
  }
  if (res.loading || !res.data) return <SkeletonRows rows={6} columns={4} />;

  // A new store: the levels come before anything else.
  if (!configured) {
    if (!canEdit) {
      return (
        <EmptyState
          title="This store's layout is not set up yet"
          body="A manager or super admin names the layout's levels — for example Zone › Aisle › Bay — before locations and products can be added. Check back once they have."
        />
      );
    }
    return (
      <section className={`card ${s.setupCard}`}>
        <span className="monoLabel">Step 1 of setting up the store</span>
        <h2 className={s.setupTitle}>How is your store laid out?</h2>
        <p className={s.setupLede}>
          Name the levels of your layout, from the top down. A store on one floor
          is usually <strong>Zone › Aisle › Bay</strong>; a store with several
          floors adds <strong>Floor</strong> above. Call them whatever your staff
          call them. Products can be added once this is saved.
        </p>
        <LevelsEditor
          initial={DEFAULT_LEVELS}
          depthLocked={false}
          busy={busy}
          error={formError}
          saveLabel="Save layout"
          onSave={(names) =>
            saveLevels(names, () =>
              toast(`Layout saved: ${names.join(' › ')}. Add your first ${names[0].toLowerCase()} next.`),
            )
          }
        />
      </section>
    );
  }

  const totals = res.data.totals;
  const childKind = childLevel(selectedNode);
  const topLevel = levelName(levels, 1);

  return (
    <div className={s.layout}>
      {/* ── The tree ───────────────────────────────────────────────────── */}
      <aside className={`card ${s.treeCard}`}>
        <div className={s.treeHead}>
          <h2 className={s.treeTitle}>Store layout</h2>
          {canEdit && (
            <span className={s.treeHeadActions}>
              {/* The levels live beside the tree they shape, not in the page
                  header: they are a property of this layout, edited rarely. */}
              <button
                type="button"
                className={s.treeAction}
                title="Edit levels"
                aria-label="Edit levels"
                onClick={() => { setFormError(null); setEditingLevels(true); }}
              >
                {/* Lucide "settings-2". */}
                <svg
                  viewBox="0 0 24 24"
                  width="16"
                  height="16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M14 17H5" />
                  <path d="M19 7h-9" />
                  <circle cx="17" cy="17" r="3" />
                  <circle cx="7" cy="7" r="3" />
                </svg>
              </button>
              <Button
                size="sm"
                onClick={() => {
                  setAddFor(null);
                  setAddName('');
                  setFormError(null);
                  setAddVisible(true);
                }}
                title={`Add ${withArticle(topLevel.toLowerCase())}`}
              >
                + {topLevel}
              </Button>
            </span>
          )}
        </div>

        <div className={s.treeSearch}>
          <SearchInput
            value={treeQ}
            onChange={setTreeQ}
            placeholder="Search the layout"
            aria-label="Search the layout"
          />
        </div>

        <div className={s.treeBody}>
          <button
            type="button"
            className={s.wholeStore}
            data-active={selected === null || undefined}
            onClick={() => setSelected(null)}
          >
            Whole store
            <span className={s.wholeStoreMeta}>
              {totals.slots - totals.empty_slots}/{totals.slots}
            </span>
          </button>

          <Tree
            nodes={treeNodes}
            selectedId={selected}
            onSelect={setSelected}
            expandedIds={expanded}
            onToggleExpand={(id, open) =>
              setExpanded((prev) => {
                const next = new Set(prev);
                open ? next.add(id) : next.delete(id);
                return next;
              })
            }
            emptyMessage={
              treeQ
                ? 'Nothing in the layout matches that.'
                : `No ${levelCount(2, topLevel).slice(2)} yet. Add the first one above.`
            }
            renderActions={
              canEdit
                ? (node) => {
                    const real = byId.get(node.id);
                    if (!real) return null;
                    const kid = childLevel(real);
                    return (
                      <>
                        {kid && (
                          <button
                            type="button"
                            className={s.treeAction}
                            title={`Add ${withArticle(kid.toLowerCase())}`}
                            onClick={() => {
                              setAddFor(real);
                              setAddName('');
                              setFormError(null);
                              setAddVisible(true);
                            }}
                          >
                            +
                          </button>
                        )}
                        {real.depth === lowest && (
                          <button
                            type="button"
                            className={s.treeAction}
                            title="Add position"
                            onClick={() => {
                              setAddSlotFor(real);
                              setSlotCode('');
                              setFormError(null);
                            }}
                          >
                            ▦
                          </button>
                        )}
                        <button
                          type="button"
                          className={s.treeAction}
                          title="Rename"
                          onClick={() => {
                            setRenaming(real);
                            setRenameTo(real.name);
                            setFormError(null);
                          }}
                        >
                          ✎
                        </button>
                        <button
                          type="button"
                          className={`${s.treeAction} ${s.treeDelete}`}
                          title="Delete"
                          onClick={() => openDelete(real)}
                        >
                          <Icon.cross />
                        </button>
                      </>
                    );
                  }
                : undefined
            }
          />
        </div>

        <div className={s.treeFoot}>
          <span className={s.treeFootPath} title="The store's levels, top first">
            {levelPath(levels)}
          </span>
          {levels
            .map((l) => levelCount(totals.by_depth[String(l.depth)] ?? 0, l.name))
            .concat(`${totals.slots} ${plural(totals.slots, 'position')}`)
            .join(' · ')}
        </div>
      </aside>

      {/* ── The selection ──────────────────────────────────────────────── */}
      <div className={s.rightCol}>
        <section className={`card ${s.selHead}`}>
          <div className={s.selText}>
            <span className="monoLabel">{selectedNode ? selectedNode.path : 'Whole store'}</span>
            <h2 className={s.selName}>{selectedNode ? selectedNode.name : 'Whole store'}</h2>
            <span className={s.selSub}>
              {selectedNode ? selectedNode.level_name : 'Every position'} ·{' '}
              {slots.length} {plural(slots.length, 'position')}
              {selectedNode && selectedNode.descendant_slots !== slots.length && !slotQ
                ? ` of ${selectedNode.descendant_slots}`
                : ''}
            </span>
          </div>

          {canEdit && (
            <div className={s.selActions}>
              {childKind && (
                <Button
                  size="toolbar"
                  variant="ghost"
                  onClick={() => {
                    setAddFor(selectedNode);
                    setAddName('');
                    setFormError(null);
                    setAddVisible(true);
                  }}
                >
                  Add {childKind.toLowerCase()}
                </Button>
              )}
              {selectedNode && selectedNode.depth === lowest && (
                <Button
                  size="toolbar"
                  onClick={() => {
                    setAddSlotFor(selectedNode);
                    setSlotCode('');
                    setFormError(null);
                  }}
                >
                  Add position
                </Button>
              )}
            </div>
          )}
        </section>

        <section className={`card ${s.slotCard}`}>
          <div className={s.slotToolbar}>
            <SearchInput
              value={slotQ}
              onChange={setSlotQ}
              placeholder={selectedNode ? `Search inside ${selectedNode.name}` : 'Search every position'}
              width="280px"
              aria-label="Search positions"
            />
            <span className={s.slotCount}>
              {slots.length} {plural(slots.length, 'position')}
            </span>
          </div>

          {slots.length === 0 ? (
            <div className={s.slotEmpty}>
              <p className={s.slotEmptyTitle}>
                {slotQ ? 'No position here matches that.' : 'No positions here yet'}
              </p>
              {!slotQ && (
                <p className={s.slotEmptyBody}>
                  {selectedNode && selectedNode.depth !== lowest
                    ? `Positions go on ${withArticle(levelName(levels, lowest).toLowerCase())}. Select one, or add one inside ${selectedNode.name}.`
                    : 'Add one, and a label can be paired to it from the Shelf labels screen.'}
                </p>
              )}
            </div>
          ) : (
            <div className={s.scroller}>
              <table className={s.table}>
                <thead>
                  <tr>
                    <th className={`${s.th} ${s.thFirst}`}>Position</th>
                    {!selectedNode || selectedNode.depth !== lowest ? (
                      <th className={s.th}>Where</th>
                    ) : null}
                    <th className={s.th}>Product</th>
                    <th className={s.th}>Category</th>
                    <th className={`${s.th} ${s.right}`}>Price</th>
                    <th className={s.th}>Label</th>
                    <th className={`${s.th} ${s.thLast}`}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {slots.map((slot) => (
                    <SlotRow
                      key={slot.slot_code}
                      slot={slot}
                      showWhere={!selectedNode || selectedNode.depth !== lowest}
                      onOpen={() => slot.label_id && navigate(`/labels/${slot.label_id}`)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <ProductsHere
          node={selectedNode}
          canLink={canLink}
          onAdd={() => setLinkOpen(true)}
        />
      </div>

      {addModal()}
      {slotModal()}
      {renameModal()}
      {deleteModal()}
      {levelsModal()}
      {linkOpen && selectedNode && (
        <LinkProductsModal
          node={selectedNode}
          onClose={() => setLinkOpen(false)}
          onLinked={(n) => {
            setLinkOpen(false);
            toast(`${n} ${plural(n, 'product')} linked to ${selectedNode.name}.`);
            res.reload();
            reloadMeta();
          }}
        />
      )}
    </div>
  );

  function levelsModal() {
    if (!editingLevels) return null;
    const hasLocations = Object.values(totals.by_depth).some((n) => n > 0);
    return (
      <Modal
        title="Layout levels"
        subtitle="Renaming is always allowed. Adding or removing a level needs an empty layout."
        width={560}
        onClose={() => { setEditingLevels(false); setFormError(null); }}
      >
        <LevelsEditor
          initial={levels.map((l) => l.name)}
          depthLocked={hasLocations}
          busy={busy}
          error={formError}
          saveLabel="Save levels"
          onCancel={() => { setEditingLevels(false); setFormError(null); }}
          onSave={(names) =>
            saveLevels(names, () => {
              setEditingLevels(false);
              toast(`Levels saved: ${names.join(' › ')}.`);
            })
          }
        />
      </Modal>
    );
  }

  /* The modals are declared as closures so they can read the state above
     without a dozen props each. */

  function addModal() {
    if (!addVisible) return null;
    const kind = childLevel(addFor);
    if (!kind) return null;
    return (
      <Modal
        title={`Add ${withArticle(kind.toLowerCase())}`}
        subtitle={addFor ? `Inside ${addFor.path}` : 'At the top of the store'}
        onClose={() => { setAddVisible(false); setFormError(null); }}
        footer={
          <>
            <Button variant="secondary" onClick={() => setAddVisible(false)}>Cancel</Button>
            <Button
              busy={busy}
              aria-disabled={!addName.trim() || undefined}
              onClick={() =>
                run(async () => {
                  const created = await api.createLocation({
                    parent_id: addFor?.location_id ?? null,
                    name: addName.trim(),
                  });
                  if (addFor) setExpanded((p) => new Set(p).add(addFor.location_id));
                  setSelected(created.location_id);
                  setAddVisible(false);
                  toast(`${addName.trim()} added.`);
                }).catch(() => undefined)
              }
            >
              Add {kind.toLowerCase()}
            </Button>
          </>
        }
      >
        <div className={s.modalBody}>
          <label className={s.fieldLabel} htmlFor="loc-name">Name</label>
          <input
            id="loc-name"
            data-autofocus
            className="field"
            value={addName}
            placeholder={`e.g. ${kind} 1`}
            onChange={(e) => setAddName(e.target.value)}
          />
          <span className={s.fieldHint}>
            Two things in the same place cannot share a name.
          </span>
          {formError && <p className={s.formError} role="alert">{formError}</p>}
        </div>
      </Modal>
    );
  }

  function slotModal() {
    if (!addSlotFor) return null;
    return (
      <Modal
        title="Add a position"
        subtitle={`On ${addSlotFor.path}`}
        onClose={() => { setAddSlotFor(null); setFormError(null); }}
        footer={
          <>
            <Button variant="secondary" onClick={() => setAddSlotFor(null)}>Cancel</Button>
            <Button
              busy={busy}
              aria-disabled={!slotCode.trim() || undefined}
              onClick={() =>
                run(async () => {
                  await api.createSlot(addSlotFor.location_id, slotCode.trim().toUpperCase());
                  toast(`${slotCode.trim().toUpperCase()} added to ${addSlotFor.name}.`);
                  setAddSlotFor(null);
                }).catch(() => undefined)
              }
            >
              Add position
            </Button>
          </>
        }
      >
        <div className={s.modalBody}>
          <label className={s.fieldLabel} htmlFor="slot-code">Position code</label>
          <input
            id="slot-code"
            data-autofocus
            className={`field ${s.monoInput}`}
            value={slotCode}
            placeholder="A3-04"
            onChange={(e) => setSlotCode(e.target.value.toUpperCase())}
          />
          <span className={s.fieldHint}>
            What staff say out loud. It has to be unique across the whole store,
            because it is what a label is paired to.
          </span>
          {formError && <p className={s.formError} role="alert">{formError}</p>}
        </div>
      </Modal>
    );
  }

  function renameModal() {
    if (!renaming) return null;
    return (
      <Modal
        title={`Rename ${renaming.name}`}
        onClose={() => { setRenaming(null); setFormError(null); }}
        footer={
          <>
            <Button variant="secondary" onClick={() => setRenaming(null)}>Cancel</Button>
            <Button
              busy={busy}
              aria-disabled={!renameTo.trim() || renameTo.trim() === renaming.name || undefined}
              onClick={() =>
                run(async () => {
                  await api.renameLocation(renaming.location_id, renameTo.trim());
                  toast('Renamed.');
                  setRenaming(null);
                }).catch(() => undefined)
              }
            >
              Rename
            </Button>
          </>
        }
      >
        <div className={s.modalBody}>
          <label className={s.fieldLabel} htmlFor="rename-to">Name</label>
          <input
            id="rename-to"
            data-autofocus
            className="field"
            value={renameTo}
            onChange={(e) => setRenameTo(e.target.value)}
          />
          {formError && <p className={s.formError} role="alert">{formError}</p>}
        </div>
      </Modal>
    );
  }

  function deleteModal() {
    if (!deleting) return null;
    return (
      <ConfirmModal
        title={`Delete ${deleting.name}?`}
        confirmLabel={impact ? `Delete ${deleting.name}` : 'Checking…'}
        busy={busy}
        error={formError}
        onClose={() => { setDeleting(null); setImpact(null); setFormError(null); }}
        onConfirm={() => {
          if (!impact) return;
          run(async () => {
            const result = await api.deleteLocation(deleting.location_id, {
              slots: impact.slots,
              labels: impact.labels,
              products: impact.products,
            });
            if (selected === deleting.location_id) setSelected(null);
            toast(
              result.labels_cleared.length
                ? `${deleting.name} deleted. ${result.labels_cleared.length} ${plural(result.labels_cleared.length, 'label')} no longer has a position.`
                : `${deleting.name} deleted.`,
              result.labels_cleared.length ? 'bad' : 'ok',
            );
            setDeleting(null);
            setImpact(null);
          }).catch(() => undefined);
        }}
        body={
          impact === null ? (
            <p>Working out what this holds…</p>
          ) : (
            <>
              <p>
                This removes <strong>{impact.locations - 1}</strong>{' '}
                {plural(impact.locations - 1, 'location')} underneath it and{' '}
                <strong>{impact.slots}</strong> {plural(impact.slots, 'position')}.
              </p>
              {impact.labels > 0 ? (
                <p>
                  <strong>{impact.labels}</strong>{' '}
                  {plural(impact.labels, 'shelf label')} will lose its position.
                  The {impact.labels === 1 ? 'label keeps' : 'labels keep'} showing
                  whatever {impact.labels === 1 ? 'it was' : 'they were'} last sent
                  — nothing is published, and the pairing to a product is not
                  touched.
                </p>
              ) : (
                <p>No shelf label is affected.</p>
              )}
              {impact.products > 0 && (
                <p>
                  <strong>{impact.products}</strong>{' '}
                  {plural(impact.products, 'product')} linked here will become{' '}
                  <strong>unassigned</strong>. They stay in the catalogue and can
                  be linked somewhere else.
                </p>
              )}
              <p className={s.confirmNote}>
                These figures are rechecked when you confirm. If somebody changes
                the layout in the meantime, the delete is refused rather than
                acting on numbers you did not see.
              </p>
            </>
          )
        }
      />
    );
  }
}

function SlotRow({
  slot,
  showWhere,
  onOpen,
}: {
  slot: LocationSlot;
  showWhere: boolean;
  onOpen: () => void;
}) {
  const filled = !!slot.label_id;
  return (
    <tr className={s.row} data-clickable={filled || undefined} onClick={onOpen}>
      <td className={`${s.td} ${s.codeCell}`}>{slot.slot_code}</td>
      {showWhere && <td className={`${s.td} ${s.whereCell}`}>{slot.path}</td>}
      <td className={s.td}>
        {slot.product_name ?? <span className={s.none}>No product yet</span>}
      </td>
      <td className={s.td}>
        {slot.category ? <CategoryPill category={slot.category} /> : <span className={s.none}>—</span>}
      </td>
      <td className={`${s.td} ${s.right} ${s.priceCell}`}>{money(slot.current_price)}</td>
      <td className={s.td}>
        {filled ? (
          <span className={s.labelCell}>
            <span className={s.mono}>{slot.label_id}</span>
            <span className={s.labelKind}>{labelDisplayName(slot.label_kind)}</span>
          </span>
        ) : (
          <span className={s.none}>Not paired</span>
        )}
      </td>
      <td className={s.td}>
        {filled ? (
          <span className={s.statusCell}>
            <StatusTag status={slot.label_status ?? 'unassigned'} />
            <span className={s.seen}>{relative(slot.last_seen_at)}</span>
          </span>
        ) : (
          <StatusTag status="unassigned" label="Empty" />
        )}
      </td>
    </tr>
  );
}

/** The products linked to the selection and everything under it — or, with
 *  the whole store selected, the ones not linked anywhere yet. */
function ProductsHere({
  node,
  canLink,
  onAdd,
}: {
  node: LocationNode | null;
  canLink: boolean;
  onAdd: () => void;
}) {
  const navigate = useNavigate();
  const { toast, reloadMeta } = useShell();
  const location = node ? node.location_id : 'unassigned';
  const res = useResource(
    () => api.products({ location, page_size: 200, sort: 'name' }),
    [location],
  );
  useStreamEvents(['products', 'locations'], res.reload);
  const rows = res.data?.rows ?? [];
  const total = res.data?.total ?? 0;

  const unlink = async (p: Product) => {
    try {
      await api.setProductsLocation([p.product_id], null);
      toast(`${p.name} is no longer linked to a location.`);
      res.reload();
      reloadMeta();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not unlink it.', 'bad');
    }
  };

  return (
    <section className={`card ${s.slotCard}`}>
      <div className={s.productsHead}>
        <div>
          <h3 className={s.productsTitle}>
            {node ? `Products in ${node.name}` : 'Products not linked to a location'}
          </h3>
          <p className={s.productsSub}>
            {node
              ? `${total} ${plural(total, 'product')}, including everything inside it.`
              : total
                ? 'Select a location on the left, then add products to it.'
                : 'Every product is linked to a location.'}
          </p>
        </div>
        {node && canLink && (
          <Button size="toolbar" onClick={onAdd}>
            Add products here
          </Button>
        )}
      </div>

      {res.error ? (
        <p className={s.slotEmptyBody}>{res.error}</p>
      ) : res.loading ? (
        <SkeletonRows rows={3} columns={3} />
      ) : rows.length === 0 ? (
        <div className={s.slotEmpty}>
          <p className={s.slotEmptyTitle}>
            {node ? 'No products here yet' : 'Nothing waiting to be linked'}
          </p>
        </div>
      ) : (
        <div className={s.scroller}>
          <table className={s.table}>
            <thead>
              <tr>
                <th className={`${s.th} ${s.thFirst}`}>Product</th>
                <th className={s.th}>SKU</th>
                <th className={s.th}>Category</th>
                {node && <th className={s.th}>Where</th>}
                <th className={`${s.th} ${s.thLast}`} />
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr
                  key={p.product_id}
                  className={s.row}
                  data-clickable
                  onClick={() => navigate(`/products/${p.product_id}`)}
                >
                  <td className={s.td}>{p.name}</td>
                  <td className={`${s.td} ${s.mono}`}>{p.sku ?? '—'}</td>
                  <td className={s.td}>
                    {p.category ? <CategoryPill category={p.category} /> : <span className={s.none}>—</span>}
                  </td>
                  {node && <td className={`${s.td} ${s.whereCell}`}>{p.location_path}</td>}
                  <td className={`${s.td} ${s.right}`}>
                    {node && canLink && (
                      <button
                        type="button"
                        className={s.unlinkBtn}
                        onClick={(e) => { e.stopPropagation(); unlink(p); }}
                      >
                        Unlink
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** Pick products and link them all to one location. Defaults to the ones not
 *  linked anywhere, which is the common case right after a catalogue import. */
function LinkProductsModal({
  node,
  onClose,
  onLinked,
}: {
  node: LocationNode;
  onClose: () => void;
  onLinked: (count: number) => void;
}) {
  const [q, setQ] = useState('');
  const [onlyUnlinked, setOnlyUnlinked] = useState(true);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const res = useResource(
    () =>
      api.products({
        q: q.trim() || undefined,
        location: onlyUnlinked ? 'unassigned' : undefined,
        page_size: 100,
        sort: 'name',
      }),
    [q, onlyUnlinked],
  );
  const rows = res.data?.rows ?? [];

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const link = async () => {
    if (picked.size === 0) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.setProductsLocation([...picked], node.location_id);
      onLinked(result.count);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not link them.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={`Add products to ${node.name}`}
      subtitle={node.path}
      width={600}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button busy={busy} aria-disabled={picked.size === 0 || undefined} onClick={link}>
            {picked.size ? `Link ${picked.size} ${plural(picked.size, 'product')}` : 'Link products'}
          </Button>
        </>
      }
    >
      <div className={s.linkBody}>
        <div className={s.linkTools}>
          <SearchInput
            value={q}
            onChange={setQ}
            placeholder="Search by name, SKU or brand"
            aria-label="Search products"
          />
          <label className={s.linkToggle}>
            <input
              type="checkbox"
              checked={onlyUnlinked}
              onChange={(e) => setOnlyUnlinked(e.target.checked)}
            />
            Only products not linked yet
          </label>
        </div>

        <div className={s.linkList}>
          {res.loading ? (
            <SkeletonRows rows={4} columns={2} />
          ) : rows.length === 0 ? (
            <p className={s.slotEmptyBody}>
              {onlyUnlinked
                ? 'Every product is already linked somewhere. Untick the box to move one here.'
                : 'No product matches that.'}
            </p>
          ) : (
            rows.map((p) => (
              <label key={p.product_id} className={s.linkRow}>
                <input
                  type="checkbox"
                  checked={picked.has(p.product_id)}
                  onChange={() => toggle(p.product_id)}
                />
                <span className={s.linkName}>{p.name}</span>
                <span className={s.linkWhere}>
                  {p.location_path ?? 'Not linked'}
                </span>
              </label>
            ))
          )}
        </div>
        {error && <p className={s.formError} role="alert">{error}</p>}
      </div>
    </Modal>
  );
}
