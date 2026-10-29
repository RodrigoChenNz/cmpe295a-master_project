/* The store's layout levels, top first — Zone › Aisle › Bay, or with a Floor
 * above, or whatever this store calls them.
 *
 * Used three times: in the setup wizard (`compact`: no explanatory preview, a
 * Back button, the buttons pinned to the bottom of the card), as the first
 * thing a manager does on a store with no levels yet (Locations shows it
 * instead of an empty tree), and later behind "Edit levels". Renaming is
 * always allowed. Adding or removing a level is only possible while the layout
 * is empty, because every existing location sits at a depth that would
 * otherwise start meaning something else; the service enforces the same rule.
 */

import { useState } from 'react';
import { Button } from './ui';
import s from './LevelsEditor.module.css';

export const DEFAULT_LEVELS = ['Zone', 'Aisle', 'Bay'];
const MAX_LEVELS = 6;

export function LevelsEditor({
  initial,
  depthLocked,
  busy,
  error,
  saveLabel,
  onSave,
  onCancel,
  onBack,
  compact = false,
}: {
  initial: string[];
  /** Locations exist, so only renaming is allowed. */
  depthLocked: boolean;
  busy: boolean;
  error: string | null;
  saveLabel: string;
  onSave: (names: string[]) => void;
  onCancel?: () => void;
  /** The wizard's Back. Unlike onCancel it does not change when Save is
   *  allowed: going back is not discarding. */
  onBack?: () => void;
  compact?: boolean;
}) {
  const [names, setNames] = useState<string[]>(initial);
  const trimmed = names.map((n) => n.trim());

  const problem = trimmed.some((n) => !n)
    ? 'Every level needs a name.'
    : new Set(trimmed.map((n) => n.toLowerCase())).size !== trimmed.length
      ? 'Two levels cannot share a name.'
      : null;
  const unchanged = trimmed.join('\u0000') === initial.join('\u0000');
  const canAdd = !depthLocked && names.length < MAX_LEVELS;
  const canRemove = !depthLocked && names.length > 1;

  const insert = (at: number) =>
    setNames((v) => [...v.slice(0, at), '', ...v.slice(at)]);

  return (
    <div className={s.editor} data-compact={compact || undefined}>
      {canAdd && (
        <button type="button" className={s.addLevel} onClick={() => insert(0)}>
          + Add a level above {trimmed[0] || 'the top'}
        </button>
      )}

      <ol className={s.list}>
        {names.map((name, i) => (
          <li key={i} className={s.row}>
            <span className={s.depth} aria-hidden="true">{i + 1}</span>
            <input
              className={`field ${s.input}`}
              value={name}
              maxLength={30}
              aria-label={`Level ${i + 1} name`}
              placeholder={i === 0 ? 'e.g. Floor' : 'e.g. Aisle'}
              onChange={(e) =>
                setNames((v) => v.map((x, j) => (j === i ? e.target.value : x)))
              }
            />
            <span className={s.role}>
              {i === 0 ? 'Top level' : i === names.length - 1 ? 'Positions go here' : ''}
            </span>
            {canRemove && (
              <button
                type="button"
                className={s.remove}
                title={`Remove ${trimmed[i] || 'this level'}`}
                onClick={() => setNames((v) => v.filter((_, j) => j !== i))}
              >
                ✕
              </button>
            )}
          </li>
        ))}
      </ol>

      {canAdd && (
        <button type="button" className={s.addLevel} onClick={() => insert(names.length)}>
          + Add a level below {trimmed[trimmed.length - 1] || 'the last one'}
        </button>
      )}

      {!compact && (
      <div className={s.preview}>
        <span className="monoLabel">Your layout</span>
        <span className={s.previewPath}>{trimmed.map((n) => n || '…').join(' › ')}</span>
        <span className={s.previewNote}>
          Every location sits at one of these levels, each inside the one above
          it. Shelf positions — where a label hangs — go on the lowest level,{' '}
          <strong>{trimmed[trimmed.length - 1] || '…'}</strong>.
        </span>
      </div>
      )}

      {depthLocked && (
        <p className={s.locked}>
          The layout already has locations, so you can rename levels but not add
          or remove them. To change how many levels there are, delete every
          location first.
        </p>
      )}

      {(error || problem) && (
        <p className={s.error} role="alert">{error ?? problem}</p>
      )}

      <div className={s.actions}>
        {onBack && (
          <Button variant="ghost" className={s.back} onClick={onBack}>Back</Button>
        )}
        {onCancel && (
          <Button variant="secondary" onClick={onCancel}>Cancel</Button>
        )}
        <Button
          busy={busy}
          aria-disabled={!!problem || (unchanged && !!onCancel) || undefined}
          onClick={() => {
            if (problem || (unchanged && onCancel)) return;
            onSave(trimmed);
          }}
        >
          {saveLabel}
        </Button>
      </div>
    </div>
  );
}
