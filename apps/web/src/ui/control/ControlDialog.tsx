// Окно контроля — раздел 6.4 ТЗ. Крестик и Esc значат «напомнить позже», а не
// ответ: отмахнуться от шага случайно нельзя. Общий компонент (перенесён из
// features/feed в срезе 2, §5.1): лента, завал и карточка задачи используют
// один и тот же диалог, второй самостоятельный экземпляр из task.js не
// заводится (map-client.md §2.2.14).
import * as Dialog from '@radix-ui/react-dialog';
import { useEffect, useRef, useState } from 'react';
import type { FeedRow, MarkOp, WhenResult } from '@/api/client';
import { useReasons } from '@/api/hooks';
import { shortDate, shortTime } from '@/ui/format';
import { DateField } from '@/ui/DateField';
import styles from './ControlDialog.module.css';

export type DialogMode = 'menu' | 'defer' | 'fail' | 'notdone';

type Props = {
  row: FeedRow | null;
  mode: DialogMode;
  progress?: string;
  onClose: () => void;
  onMark: (op: MarkOp, extra?: { reason?: string | null; to?: string | null }) => void;
  /** Префикс id полей — по умолчанию `'c'` (как было). Карточка задачи
   * ставит своё значение (`idPrefix="card"`), чтобы её экземпляр диалога не
   * делил id `c-reason`/`c-date` с лентой на одной странице (Р7 SLICE2_SPEC.md). */
  idPrefix?: string;
};

export function ControlDialog({ row, mode: initialMode, progress, onClose, onMark, idPrefix = 'c' }: Props) {
  const [mode, setMode] = useState<DialogMode>(initialMode);
  const [reason, setReason] = useState('');
  const [to, setTo] = useState('');
  const [parsed, setParsed] = useState<WhenResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const reasons = useReasons();
  const reasonRef = useRef<HTMLSelectElement>(null);
  const dateRef = useRef<HTMLInputElement>(null);
  const reasonId = `${idPrefix}-reason`;
  const dateId = `${idPrefix}-date`;

  useEffect(() => {
    setMode(initialMode);
    setReason('');
    setTo('');
    setParsed(null);
    setErr(null);
  }, [initialMode, row]);

  // Фокус там, куда человек будет печатать: у провала — причина, у переноса —
  // дата. Radix ставит фокус на первый элемент сам, но нам нужен не первый.
  useEffect(() => {
    const t = window.setTimeout(() => {
      if (mode === 'fail' || mode === 'notdone') reasonRef.current?.focus();
      if (mode === 'defer') dateRef.current?.focus();
    }, 30);
    return () => window.clearTimeout(t);
  }, [mode, row]);

  const open = row !== null;
  const needReason = mode === 'fail';
  const needDate = mode === 'defer';

  function submit() {
    if (!row) return;
    if (needReason && !reason) return setErr('Причина обязательна');
    if (needDate && (!to.trim() || !parsed?.ok || !parsed.date)) return setErr('Нужна новая дата');
    setErr(null);
    onMark(mode as MarkOp, { reason: reason || null, to: needDate || mode === 'notdone' ? to || null : null });
  }

  const meta: string[] = [];
  if (row?.show_at) meta.push(`контроль ${shortDate(row.show_at)} ${shortTime(row.show_at)}`);
  if (row && row.postponed > 0) meta.push(`переносов: ${row.postponed}`);

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={styles.content} aria-describedby={undefined}>
          {row && (
            <>
              <header className={styles.head}>
                {progress && <p className={styles.progress}>{progress}</p>}
                <p className={styles.task}>{row.group ? `${row.task} · ${row.group}` : row.task}</p>
                <Dialog.Title className={styles.step}>{row.title}</Dialog.Title>
                {row.note && <p className={styles.note}>{row.note}</p>}
                <p className={styles.meta}>
                  {meta.join(' · ')}
                  {row.stalled && <span className={styles.warn}> — буксует, нужен другой ход</span>}
                </p>
              </header>

              {mode === 'menu' && (
                <div className={styles.actions}>
                  <button type="button" className="primary" onClick={() => onMark('done')}>
                    Сделано <kbd>d</kbd>
                  </button>
                  <button type="button" onClick={() => onMark('notdone')}>
                    Не сделано <kbd>n</kbd>
                  </button>
                  <button type="button" onClick={() => setMode('defer')}>
                    Перенести <kbd>t</kbd>
                  </button>
                </div>
              )}

              {mode !== 'menu' && (
                <div className={styles.form}>
                  <div className={styles.field}>
                    <label htmlFor={reasonId}>
                      Причина {needReason ? '' : <span className="muted">(необязательно)</span>}
                    </label>
                    <select
                      id={reasonId}
                      ref={reasonRef}
                      value={reason}
                      className={err && needReason && !reason ? 'invalid' : ''}
                      onChange={(e) => setReason(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } }}
                    >
                      <option value="">— {needReason ? 'выбери причину' : 'без причины'} —</option>
                      {(reasons.data ?? []).map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </div>
                  {mode !== 'fail' && (
                    <DateField id={dateId} value={to} onChange={setTo} onParsed={setParsed} onSubmit={submit}
                      inputRef={dateRef} />
                  )}
                  {err && <p className="err">{err}</p>}
                  <div className={styles.actions}>
                    <button type="button" className="primary" onClick={submit}>
                      Сохранить <kbd>Enter</kbd>
                    </button>
                    <button type="button" className="ghost" onClick={() => setMode('menu')}>Назад</button>
                  </div>
                </div>
              )}

              <footer className={styles.foot}>
                {mode === 'menu' && (
                  <button type="button" className="quiet" onClick={() => setMode('fail')}>
                    Не будет сделано <kbd>f</kbd>
                  </button>
                )}
                <span className={styles.hint}>Esc — напомнить позже</span>
              </footer>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
