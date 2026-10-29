/* The store's category list: chips in order, an add box, ✕ to remove.
 *
 * Controlled — the parent owns the list and decides when to save it, because
 * the wizard saves on "Continue" and Settings on "Save". The rules match
 * PUT /api/categories, so a list this accepts is one the service accepts.
 */

import { useState } from 'react';
import { plural } from '../lib/format';
import { Button } from './ui';
import s from './CategoryListEditor.module.css';

const MAX_NAME = 60;
const MAX_CATEGORIES = 100;

export function CategoryListEditor({
  names,
  counts,
  disabled,
  onChange,
}: {
  names: string[];
  /** Products currently in each category, shown on its chip when non-zero. */
  counts?: Record<string, number>;
  disabled?: boolean;
  onChange: (names: string[]) => void;
}) {
  const [draft, setDraft] = useState('');
  const [problem, setProblem] = useState<string | null>(null);

  const add = () => {
    const name = draft.trim();
    if (!name) return;
    if (names.some((n) => n.toLowerCase() === name.toLowerCase())) {
      setProblem(`${name} is already on the list.`);
      return;
    }
    if (names.length >= MAX_CATEGORIES) {
      setProblem(`A store can list up to ${MAX_CATEGORIES} categories.`);
      return;
    }
    onChange([...names, name]);
    setDraft('');
    setProblem(null);
  };

  return (
    <div className={s.editor}>
      {names.length > 0 ? (
        <ul className={s.chips}>
          {names.map((name) => {
            const n = counts?.[name] ?? 0;
            return (
              <li key={name} className={s.chip}>
                <span>{name}</span>
                {n > 0 && (
                  <span className={s.count}>
                    {n} {plural(n, 'product')}
                  </span>
                )}
                {!disabled && (
                  <button
                    type="button"
                    className={s.remove}
                    title={
                      n > 0
                        ? `Remove ${name}. Its ${n} ${plural(n, 'product')} keep the category.`
                        : `Remove ${name}`
                    }
                    onClick={() => onChange(names.filter((x) => x !== name))}
                  >
                    ✕
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className={s.empty}>No categories yet.</p>
      )}

      {!disabled && (
        <div className={s.addRow}>
          <input
            className="field"
            value={draft}
            maxLength={MAX_NAME}
            placeholder="Add a category, e.g. Frozen"
            aria-label="New category name"
            onChange={(e) => {
              setDraft(e.target.value);
              setProblem(null);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                add();
              }
            }}
          />
          <Button variant="secondary" size="toolbar" disabled={!draft.trim()} onClick={add}>
            Add
          </Button>
        </div>
      )}
      {problem && <p className={s.problem} role="alert">{problem}</p>}
    </div>
  );
}
