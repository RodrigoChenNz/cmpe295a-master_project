/* The Add product modal.
 *
 * A new product joins the catalogue with no shelf label, which is why the
 * footer says so and the shelf preview is captioned "Written when the label is
 * paired" rather than presented as something a shopper can see. Its location
 * in the store is optional here: a catalogue is usually entered first and
 * linked to the layout afterwards, from Locations or the product page.
 *
 * Three things here are not cosmetic:
 *
 * 1. **The display name auto-derives from the product name until you edit it.**
 *    After that it stops tracking, permanently. Most products want the name
 *    cut to sixteen characters; the ones that do not need a human, and a field
 *    that silently re-derives after you have corrected it is worse than one
 *    that never helped.
 * 2. **The 16-character counter is a hardware limit made visible.** Past
 *    sixteen the label firmware drops the rest — the name does not wrap, it
 *    disappears — so the preview renders exactly what `gate.render_lines` will
 *    publish, via the shared helpers in lib/format.
 * 3. **The opening price is manager-only.** It becomes a price_history row
 *    carrying `approval` and `approved_by`, and a retained message that goes
 *    live the instant a label is paired. Staff see what will happen instead of
 *    a disabled box.
 */

import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '../lib/api';
import { shelfLine1, shelfLine2 } from '../lib/format';
import { useDebounced, useResource } from '../lib/useStream';
import type { LabelKind, NewProductBody, ProductDetail } from '../lib/types';
import { ShelfDisplay } from './ShelfDisplay';
import { LocationPicker } from './LocationPicker';
import { Button, Modal } from './ui';
import s from './AddProductModal.module.css';

/** The handoff's seven. Offered only when the store has no category list of
 *  its own (onboarding or Settings) and the catalogue uses none either, so an
 *  empty store still offers something sensible. */
const CANONICAL_CATEGORIES = [
  'Dairy', 'Bakery', 'Produce', 'Pantry', 'Seafood', 'Electronics', 'Clothing',
];

interface Draft {
  name: string;
  category: string;
  sku: string;
  upc: string;
  brand: string;
  supplier: string;
  price: string;
  cost: string;
  stock: string;
  displayName: string;
}

const EMPTY: Draft = {
  name: '', category: '', sku: '', upc: '', brand: '',
  supplier: '', price: '', cost: '', stock: '', displayName: '',
};

interface Props {
  /** From the facets the Products list already loads. */
  categories: string[];
  /** Manager only. */
  canSetPrice: boolean;
  onClose: () => void;
  onCreated: (detail: ProductDetail) => void;
}

export function AddProductModal({ categories, canSetPrice, onClose, onCreated }: Props) {
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [locationId, setLocationId] = useState<string | null>(null);
  // One-way: once the display name has been edited by hand it never resumes
  // tracking the product name, even if cleared.
  const [displayTouched, setDisplayTouched] = useState(false);
  const [kind, setKind] = useState<LabelKind>('lcd');
  const [submitted, setSubmitted] = useState(false);
  const [upcError, setUpcError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const displayName = displayTouched ? draft.displayName : shelfLine1(draft.name);

  // The preview device is a way of looking at the product, not a property of
  // it, so switching it does not make the form dirty.
  const dirty = useMemo(
    () => (Object.keys(EMPTY) as (keyof Draft)[]).some((k) => draft[k] !== EMPTY[k]),
    [draft],
  );

  const nameError = submitted && !draft.name.trim() ? 'A product name is required' : null;
  const costError =
    submitted && !draft.cost.trim()
      ? 'A unit cost is required'
      : draft.cost.trim() && !/^\d+(\.\d{1,2})?$/.test(draft.cost.trim())
        ? 'Use a number like 2.41'
        : null;
  const priceError =
    draft.price.trim() && !/^\d+(\.\d{1,2})?$/.test(draft.price.trim())
      ? 'Use a number like 3.49'
      : null;
  const upcLengthError =
    draft.upc.length > 0 && draft.upc.length < 8 ? 'A UPC is 8 to 13 digits' : null;

  const valid =
    draft.name.trim() !== '' &&
    draft.cost.trim() !== '' &&
    !costError &&
    !priceError &&
    !upcError &&
    !upcLengthError;

  /* Live duplicate check. No new endpoint: the products search already matches
   * `upc ILIKE`, so this reuses it and filters for an exact hit.
   *
   * The view behind that search filters `archived_at IS NULL`, so an archived
   * product holding this UPC will NOT be found here — the server's 409 is the
   * authority, and the submit path below still has to handle it. */
  const debouncedUpc = useDebounced(draft.upc, 400);
  useEffect(() => {
    if (debouncedUpc.length < 8) {
      setUpcError(null);
      return;
    }
    let live = true;
    api
      .products({ q: debouncedUpc, page_size: 5 })
      .then((page) => {
        if (!live) return;
        const clash = page.rows.find((r) => r.upc === debouncedUpc);
        setUpcError(clash ? `That UPC already belongs to ${clash.name}.` : null);
      })
      .catch(() => {
        // A failed lookup is not a duplicate. Stay quiet and let the server
        // decide on submit.
      });
    return () => {
      live = false;
    };
  }, [debouncedUpc]);

  /** Every route out of this modal, including Escape and the outside click,
   *  which `Modal` routes through `onClose`. */
  const requestClose = () => {
    // While the confirmation is up it owns the keyboard and the pointer. Without
    // this, Escape reaches both dialogs' listeners and a click on "Keep editing"
    // lands outside this modal's ref and closes the form underneath it.
    if (confirmDiscard) return;
    if (dirty) {
      setConfirmDiscard(true);
      return;
    }
    onClose();
  };

  const submit = async () => {
    setSubmitted(true);
    if (!valid || saving) return;
    setSaving(true);
    setFormError(null);
    try {
      const body: NewProductBody = {
        name: draft.name.trim(),
        display_name: displayName || null,
        sku: draft.sku.trim() || null,
        upc: draft.upc || null,
        brand: draft.brand.trim() || null,
        supplier: draft.supplier.trim() || null,
        category: draft.category || null,
        unit_cost: draft.cost.trim(),
        stock_on_hand: draft.stock.trim() === '' ? null : Number(draft.stock),
        price: canSetPrice && draft.price.trim() ? draft.price.trim() : null,
        agent_excluded: false,
        location_id: locationId,
      };
      onCreated(await api.createProduct(body));
    } catch (err) {
      // A conflict goes in the form, not a toast: the fix is in a field the
      // person is still looking at.
      setFormError(
        err instanceof ApiError ? err.message : 'Could not add the product.',
      );
    } finally {
      setSaving(false);
    }
  };

  // The store's own list first, in its order; then anything the catalogue
  // uses that the list does not, so an invented category is kept.
  const storeList = useResource(() => api.categories(), []);
  const options = useMemo(() => {
    const own = storeList.data?.categories.map((c) => c.name) ?? [];
    const extra = categories.filter((c) => !own.includes(c)).sort();
    const all = [...own, ...extra];
    return all.length ? all : CANONICAL_CATEGORIES;
  }, [storeList.data, categories]);

  return (
    <>
      <Modal
        title="Add a product"
        subtitle="Only the name and cost are required. Location and shelf label can come later."
        width={620}
        onClose={requestClose}
        footer={
          <>
            <span className={s.hint}>No shelf shows it until you pair a label to it.</span>
            <Button variant="secondary" onClick={requestClose}>
              Cancel
            </Button>
            {/* aria-disabled, not disabled: greyed while invalid, per the
                handoff, but still clickable — clicking is what reveals the
                inline errors that say what is missing. */}
            <Button
              busy={saving}
              aria-disabled={!valid || undefined}
              onClick={submit}
            >
              Add product
            </Button>
          </>
        }
      >
        <div className={s.body}>
          <div className={s.row2}>
            <Field label="Product name" error={nameError}>
              <input
                data-autofocus
                className={`field ${s.input}`}
                placeholder="Organic Whole Milk 1L"
                value={draft.name}
                data-invalid={nameError ? '' : undefined}
                onChange={(e) => set('name', e.target.value)}
              />
            </Field>
            <Field label="Category" hint="Drives the pricing rule that applies">
              <select
                className={`field ${s.input}`}
                value={draft.category}
                onChange={(e) => set('category', e.target.value)}
              >
                <option value="">No category</option>
                {options.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <div className={s.row2}>
            <Field label="SKU" hint="Generated from the category if left blank">
              <input
                className={`field ${s.mono}`}
                placeholder="DA-1007"
                value={draft.sku}
                onChange={(e) => set('sku', e.target.value.toUpperCase().slice(0, 14))}
              />
            </Field>
            <Field label="UPC / GTIN" error={upcError ?? upcLengthError}>
              <input
                className={`field ${s.mono}`}
                placeholder="5010024000000"
                inputMode="numeric"
                value={draft.upc}
                data-invalid={upcError || upcLengthError ? '' : undefined}
                // Digits only: a barcode with a letter in it is not a barcode,
                // and the API's pattern would reject it anyway.
                onChange={(e) => set('upc', e.target.value.replace(/\D/g, '').slice(0, 13))}
              />
            </Field>
          </div>

          <div className={s.row2}>
            <Field label="Brand">
              <input
                className={`field ${s.input}`}
                placeholder="Meadowfield"
                value={draft.brand}
                onChange={(e) => set('brand', e.target.value)}
              />
            </Field>
            <Field label="Supplier">
              <input
                className={`field ${s.input}`}
                placeholder="Meadowfield Dairy Co"
                value={draft.supplier}
                onChange={(e) => set('supplier', e.target.value)}
              />
            </Field>
          </div>

          <Field
            label="Location"
            hint="Optional. You can link it later from Locations or the product page."
          >
            <LocationPicker
              className={`field ${s.input}`}
              value={locationId}
              onChange={setLocationId}
            />
          </Field>

          <div className={s.row3}>
            {canSetPrice ? (
              <Field label="Opening price" error={priceError}>
                <input
                  className={`field ${s.mono} ${s.priceInput}`}
                  placeholder="3.49"
                  inputMode="decimal"
                  value={draft.price}
                  data-invalid={priceError ? '' : undefined}
                  onChange={(e) => set('price', e.target.value)}
                />
              </Field>
            ) : (
              <Field label="Opening price" hint="A manager sets the opening price">
                {/* An em dash and a sentence, not a disabled box: this says what
                    will happen rather than inviting a shrug at the browser. */}
                <span className={s.readOnlyValue}>—</span>
              </Field>
            )}
            <Field label="Unit cost" error={costError}>
              <input
                className={`field ${s.mono}`}
                placeholder="2.41"
                inputMode="decimal"
                value={draft.cost}
                data-invalid={costError ? '' : undefined}
                onChange={(e) => set('cost', e.target.value)}
              />
            </Field>
            <Field label="Stock on hand">
              <input
                className={`field ${s.mono}`}
                placeholder="18"
                inputMode="numeric"
                value={draft.stock}
                onChange={(e) => set('stock', e.target.value.replace(/\D/g, '').slice(0, 7))}
              />
            </Field>
          </div>

          {/* ── The shelf display ─────────────────────────────────────────
              Set apart because this is where a hardware limit becomes the
              user's problem, and it should not read as one more text field. */}
          <div className={s.shelfBlock}>
            <div className={s.shelfHead}>
              <span className={s.shelfLabelRow}>
                <label className={s.label} htmlFor="add-display-name">
                  Shelf display
                </label>
                <span className={s.kindSwitch} role="group" aria-label="Preview as">
                  {(['lcd', 'eink'] as const).map((k) => (
                    <button
                      key={k}
                      type="button"
                      className={s.kindOption}
                      data-active={kind === k || undefined}
                      aria-pressed={kind === k}
                      onClick={() => setKind(k)}
                    >
                      {k === 'lcd' ? '16×2 LCD' : 'E-ink'}
                    </button>
                  ))}
                </span>
              </span>
              <span className={s.counter} data-warn={displayName.length > 14 || undefined}>
                {displayName.length} / 16
              </span>
            </div>

            <input
              id="add-display-name"
              className={`field ${s.displayInput}`}
              maxLength={16}
              value={displayName}
              onChange={(e) => {
                setDisplayTouched(true);
                set('displayName', e.target.value.toUpperCase().slice(0, 16));
              }}
            />

            <ShelfDisplay
              kind={kind}
              line1={shelfLine1(displayName)}
              line2={shelfLine2(draft.price || null, 'New product')}
              fullName={draft.name}
              price={draft.price || null}
              brand={draft.brand || null}
              size="sm"
              caption="Written when the label is paired"
              // The block above already shows N / 16, and two counters
              // disagreeing on their thresholds would be worse than one.
              showCounter={false}
            />
          </div>

          {formError && (
            <p className={s.formError} role="alert">
              {formError}
            </p>
          )}
        </div>
      </Modal>

      {confirmDiscard && (
        <Modal
          title="Discard this product?"
          onClose={() => setConfirmDiscard(false)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setConfirmDiscard(false)}>
                Keep editing
              </Button>
              <Button variant="danger" onClick={onClose}>
                Discard
              </Button>
            </>
          }
        >
          <p className={s.discardBody}>What you have typed so far will be lost.</p>
        </Modal>
      )}
    </>
  );
}

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  children: React.ReactNode;
}) {
  return (
    <div className={s.field}>
      <label className={s.label}>{label}</label>
      {children}
      {/* The error replaces the hint rather than stacking with it, so the row
          height does not jump as you type. */}
      {error ? (
        <span className={s.error}>{error}</span>
      ) : hint ? (
        <span className={s.fieldHint}>{hint}</span>
      ) : null}
    </div>
  );
}
