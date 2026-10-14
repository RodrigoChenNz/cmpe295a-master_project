/* A select over the store's layout tree, indented by level and labelled with
 * the store's own level names. One component so that every place a product is
 * placed — Add product, Product detail, the Products bulk action, Locations —
 * lists the same tree the same way.
 */

import { api } from '../lib/api';
import { useResource } from '../lib/useStream';
import type { LocationNode } from '../lib/types';

export const UNASSIGNED = '';

export interface PickerOption {
  id: string;
  label: string;
  path: string;
  depth: number;
}

/** The tree flattened in display order, for any select that lists it. */
export function flattenTree(tree: LocationNode[]): PickerOption[] {
  const out: PickerOption[] = [];
  const walk = (n: LocationNode) => {
    // Non-breaking spaces, because a select collapses ordinary ones.
    const indent = '   '.repeat(n.depth - 1);
    out.push({
      id: n.location_id,
      label: `${indent}${n.name} (${n.level_name})`,
      path: n.path,
      depth: n.depth,
    });
    n.children.forEach(walk);
  };
  tree.forEach(walk);
  return out;
}

export function LocationPicker({
  id,
  value,
  onChange,
  unassignedLabel = 'Not linked yet',
  disabled,
  className = 'field',
}: {
  id?: string;
  /** A location id, or null for none. */
  value: string | null;
  onChange: (locationId: string | null) => void;
  unassignedLabel?: string;
  disabled?: boolean;
  className?: string;
}) {
  const res = useResource(() => api.locations(), []);
  const options = res.data ? flattenTree(res.data.tree) : [];

  return (
    <select
      id={id}
      className={className}
      value={value ?? UNASSIGNED}
      disabled={disabled || res.loading}
      onChange={(e) => onChange(e.target.value === UNASSIGNED ? null : e.target.value)}
    >
      <option value={UNASSIGNED}>
        {res.loading ? 'Loading the layout…' : unassignedLabel}
      </option>
      {options.map((o) => (
        <option key={o.id} value={o.id} title={o.path}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
