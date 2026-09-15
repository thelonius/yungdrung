// Редактор шагов формы «Новая задача» (SLICE2_SPEC.md §5.4, пп.1-4). Одна
// глубина: подшаг сам разбиться на подшаги не может, кнопки «Подшаги» у него
// нет. Клавиатурная цепочка и структура повторяют `static/app.js`
// (`addStep`/`addSubstep`/`makeGroup`/`ungroup`) — тот же контракт с ядром,
// то же поведение, другой рендер.
import { useEffect, useRef } from 'react';
import type { FieldError, PlanResult } from '@/api/client';
import { errorsFor } from '@/api/errors';
import { DateField } from '@/ui/DateField';
import {
  insertSubAfter, insertTopAfter, isGroup, makeGroup, newLeaf, plannedAt, removeSub, removeTop,
  stepFieldPath, updateSub, updateTop,
} from './model';
import type { StepDraft, StepMode } from './model';
import styles from './StepsEditor.module.css';

export type Props = {
  steps: StepDraft[];
  onChange: (steps: StepDraft[]) => void;
  plan: PlanResult | null;
  errors: FieldError[];
  /** Растёт у родителя, когда нужно перевести фокус в первый шаг (после
   * `Enter` в поле тегов) — без этого сигнала фокус на монтировании никто
   * не крадёт у названия задачи. */
  focusFirstToken: number;
};

type Ref = { current: HTMLInputElement | null };

export function StepsEditor({ steps, onChange, plan, errors, focusFirstToken }: Props) {
  const titleRefs = useRef(new Map<string, HTMLInputElement>());
  const dateRefs = useRef(new Map<string, Ref>());
  const pendingFocus = useRef<string | null>(null);
  const seenToken = useRef(0);

  useEffect(() => {
    if (focusFirstToken > seenToken.current) {
      seenToken.current = focusFirstToken;
      const first = steps[0];
      if (first) titleRefs.current.get(first.key)?.focus();
    }
  }, [focusFirstToken, steps]);

  // Фокус после структурных изменений (новый/удалённый шаг) — на узел,
  // который запросила последняя мутация. Без списка зависимостей: читает и
  // сразу же снимает свою собственную метку, лишних повторов не будет.
  useEffect(() => {
    if (!pendingFocus.current) return;
    const key = pendingFocus.current;
    pendingFocus.current = null;
    titleRefs.current.get(key)?.focus();
  });

  function dateRefFor(key: string): Ref {
    let r = dateRefs.current.get(key);
    if (!r) { r = { current: null }; dateRefs.current.set(key, r); }
    return r;
  }
  function focusTitle(key: string) { titleRefs.current.get(key)?.focus(); }
  function focusDate(key: string) { dateRefs.current.get(key)?.current?.focus(); }

  function addTopAfter(afterKey: string) {
    const item = newLeaf();
    onChange(insertTopAfter(steps, afterKey, item));
    pendingFocus.current = item.key;
  }

  function removeTopStep(key: string) {
    if (steps.length < 2) return;
    const i = steps.findIndex((s) => s.key === key);
    const neighbor = steps[i + 1] ?? steps[i - 1];
    onChange(removeTop(steps, key));
    if (neighbor) pendingFocus.current = neighbor.key;
  }

  function toggleGroupTop(key: string) {
    const target = steps.find((s) => s.key === key);
    if (!target) return;
    const grouped = makeGroup(target);
    onChange(updateTop(steps, key, grouped));
    pendingFocus.current = grouped.steps[0].key;
  }

  function addSubAfter(groupKey: string, afterChildKey: string | null) {
    const item = newLeaf();
    onChange(insertSubAfter(steps, groupKey, afterChildKey, item));
    pendingFocus.current = item.key;
  }

  function removeSubStep(groupKey: string, childKey: string) {
    const group = steps.find((s) => s.key === groupKey);
    if (!group) return;
    const i = group.steps.findIndex((c) => c.key === childKey);
    const neighbor = group.steps[i + 1] ?? group.steps[i - 1];
    onChange(removeSub(steps, groupKey, childKey));
    // Опустевшая группа вернулась в лист — там держать фокус негде,
    // остаётся собственное название шага.
    pendingFocus.current = neighbor ? neighbor.key : groupKey;
  }

  // Живая проверка (§5.4 п.4): ошибка из `plan` ложится под то же поле, что
  // и ошибка сохранения, — «перекрывая подпись даты» означает именно это,
  // не два одновременных сообщения. Свежая ошибка сохранения (`errors`)
  // приоритетнее: пока форма показывает её, она не должна мигать, стоит
  // `plan` пересчитаться на несвязанном соседнем поле.
  const planErrors = plan?.errors ?? [];

  return (
    <ol className={styles.list}>
      {steps.map((s, i) => {
        const path = [i];
        const titleErr = errorsFor(errors, stepFieldPath(path, 'title'));
        const dateErr = errorsFor(errors, stepFieldPath(path, 'control_date'))
          ?? errorsFor(planErrors, stepFieldPath(path, 'control_date'));
        const groupErr = isGroup(s)
          ? errorsFor(errors, stepFieldPath(path, 'steps'))
            ?? errorsFor(errors, stepFieldPath(path, 'mode'))
            ?? errorsFor(planErrors, stepFieldPath(path, 'steps'))
            ?? errorsFor(planErrors, stepFieldPath(path, 'mode'))
          : undefined;
        const planned = plannedAt(plan?.steps ?? [], path);
        const hint = !isGroup(s) && planned && !planned.explicit_start ? `начнётся ${planned.start ?? '—'}` : null;

        return (
          <li key={s.key} className={styles.step} data-testid="form-step" data-step-index={i}>
            <div className={styles.num}>{i + 1}</div>
            <div className={styles.fields}>
              <div className={styles.titleRow}>
                <input
                  type="text"
                  autoComplete="off"
                  className={titleErr ? 'invalid' : ''}
                  placeholder="Что сделать"
                  data-testid="step-title"
                  value={s.title}
                  ref={(el) => { if (el) titleRefs.current.set(s.key, el); else titleRefs.current.delete(s.key); }}
                  onChange={(e) => onChange(updateTop(steps, s.key, { title: e.target.value }))}
                  onKeyDown={(e) => {
                    if (e.key !== 'Enter' || e.ctrlKey || e.metaKey) return;
                    e.preventDefault();
                    if (isGroup(s)) focusTitle(s.steps[0]?.key ?? s.key);
                    else focusDate(s.key);
                  }}
                />
                {!isGroup(s) && (
                  <button type="button" className="ghost" onClick={() => toggleGroupTop(s.key)}>Подшаги</button>
                )}
                {steps.length > 1 && (
                  <button type="button" className={styles.drop} aria-label="Убрать шаг" onClick={() => removeTopStep(s.key)}>×</button>
                )}
              </div>
              {titleErr && <p className="err">{titleErr}</p>}

              {!isGroup(s) && (
                <>
                  <DateField
                    id={`step-${i}-date`}
                    value={s.controlDate}
                    onChange={(t) => onChange(updateTop(steps, s.key, { controlDate: t }))}
                    onParsed={(r) => onChange(updateTop(steps, s.key, { parsed: r }))}
                    onSubmit={() => {
                      const next = steps[i + 1];
                      if (next) focusTitle(next.key); else addTopAfter(s.key);
                    }}
                    inputRef={dateRefFor(s.key)}
                    label="Дата контроля"
                  />
                  {dateErr && <p className="err">{dateErr}</p>}
                  {!dateErr && hint && <p className="note">{hint}</p>}
                </>
              )}

              {isGroup(s) && (
                <div className={styles.subBlock}>
                  <select
                    className={styles.modeSelect}
                    aria-label="Порядок подшагов"
                    value={s.mode ?? 'par'}
                    onChange={(e) => onChange(updateTop(steps, s.key, { mode: e.target.value as StepMode }))}
                  >
                    <option value="par">подшаги в любом порядке</option>
                    <option value="seq">подшаги по очереди</option>
                  </select>
                  <ol className={styles.subList}>
                    {s.steps.map((c, j) => {
                      const subPath = [i, j];
                      const subTitleErr = errorsFor(errors, stepFieldPath(subPath, 'title'));
                      const subDateErr = errorsFor(errors, stepFieldPath(subPath, 'control_date'))
                        ?? errorsFor(planErrors, stepFieldPath(subPath, 'control_date'));
                      const subPlanned = plannedAt(plan?.steps ?? [], subPath);
                      const subHint = subPlanned && !subPlanned.explicit_start ? `начнётся ${subPlanned.start ?? '—'}` : null;
                      return (
                        <li key={c.key} className={styles.step} data-testid="form-substep">
                          <div className={styles.numSub}>{i + 1}.{j + 1}</div>
                          <div className={styles.fields}>
                            <div className={styles.titleRow}>
                              <input
                                type="text"
                                autoComplete="off"
                                className={subTitleErr ? 'invalid' : ''}
                                placeholder="Что сделать"
                                data-testid="step-title"
                                value={c.title}
                                ref={(el) => { if (el) titleRefs.current.set(c.key, el); else titleRefs.current.delete(c.key); }}
                                onChange={(e) => onChange(updateSub(steps, s.key, c.key, { title: e.target.value }))}
                                onKeyDown={(e) => {
                                  if (e.key !== 'Enter' || e.ctrlKey || e.metaKey) return;
                                  e.preventDefault();
                                  focusDate(c.key);
                                }}
                              />
                              {s.steps.length > 1 && (
                                <button type="button" className={styles.drop} aria-label="Убрать подшаг" onClick={() => removeSubStep(s.key, c.key)}>×</button>
                              )}
                            </div>
                            {subTitleErr && <p className="err">{subTitleErr}</p>}
                            <DateField
                              id={`step-${i}-${j}-date`}
                              value={c.controlDate}
                              onChange={(t) => onChange(updateSub(steps, s.key, c.key, { controlDate: t }))}
                              onParsed={(r) => onChange(updateSub(steps, s.key, c.key, { parsed: r }))}
                              onSubmit={() => {
                                const next = s.steps[j + 1];
                                if (next) focusTitle(next.key); else addSubAfter(s.key, c.key);
                              }}
                              inputRef={dateRefFor(c.key)}
                              label="Дата контроля подшага"
                            />
                            {subDateErr && <p className="err">{subDateErr}</p>}
                            {!subDateErr && subHint && <p className="note">{subHint}</p>}
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                  <button type="button" className="ghost" onClick={() => addSubAfter(s.key, s.steps[s.steps.length - 1]?.key ?? null)}>
                    Добавить подшаг
                  </button>
                  {groupErr && <p className="err">{groupErr}</p>}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
