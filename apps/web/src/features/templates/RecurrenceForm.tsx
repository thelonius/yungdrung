// Правило повторения шаблона (SLICE2_SPEC.md §5.6). Скрытые поля
// (`holiday_shift`/`lead_days`/`until`/`paused`) у этой формы нет виджета —
// они едут в состоянии как пришли из `GET` и возвращаются в `PUT` как есть
// (round-trip проверен в model.test.ts, здесь только чтение/запись состояния).
import * as Dialog from '@radix-ui/react-dialog';
import { useEffect, useRef, useState } from 'react';
import { api } from '@/api/client';
import type { RuleDate, RuleParseResult, TemplateCard } from '@/api/client';
import { ApiError, errorsFor, generalErrors } from '@/api/errors';
import { useToaster } from '@/ui/toasterContext';
import { DateField } from '@/ui/DateField';
import { shortDate } from '@/ui/format';
import {
  monthdayListText, parseMonthdayList, ruleFromView, toggleWeekday, WEEKDAYS,
} from './model';
import { useClearRecurrence, useSetRecurrence } from './useTemplates';
import styles from '@/ui/control/ControlDialog.module.css';
import own from './TemplatesPage.module.css';

type Props = {
  template: TemplateCard | null;
  onClose: () => void;
};

const FREQ_OPTIONS: { value: string; label: string }[] = [
  { value: 'daily', label: 'каждый день' },
  { value: 'weekly', label: 'по неделям' },
  { value: 'monthly', label: 'по месяцам' },
  { value: 'yearly', label: 'по годам' },
];

export function RecurrenceForm({ template, onClose }: Props) {
  const [words, setWords] = useState('');
  const [rule, setRule] = useState(() => ruleFromView(template?.recurrence ?? null));
  const [description, setDescription] = useState<string | null>(null);
  const [dates, setDates] = useState<RuleDate[]>([]);
  const [errors, setErrors] = useState<{ field: string | null; error: string }[]>([]);
  const setRecurrence = useSetRecurrence(template?.name ?? '');
  const clearRecurrence = useClearRecurrence(template?.name ?? '');
  const toaster = useToaster();
  const wordsTimer = useRef<number | undefined>(undefined);
  const previewTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    setWords('');
    setRule(ruleFromView(template?.recurrence ?? null));
    setDescription(template?.recurrence?.description ?? null);
    setErrors([]);
  }, [template]);

  // «Словами» — POST /recurrence/parse, дебаунс 300 (§5.6): разбирает, но не
  // сохраняет сам по себе — виджеты остаются источником правды до «Сохранить»,
  // человек может поправить руками то, что не так поняли.
  useEffect(() => {
    window.clearTimeout(wordsTimer.current);
    if (!words.trim()) return;
    wordsTimer.current = window.setTimeout(async () => {
      const { data } = await api.POST('/api/v1/recurrence/parse', { body: { text: words } });
      applyParsed(data);
    }, 300);
    return () => window.clearTimeout(wordsTimer.current);
  }, [words]);

  function applyParsed(data: RuleParseResult | undefined) {
    if (!data) return;
    if (data.ok && data.rule) {
      setRule((r) => ({ ...r, ...data.rule }));
    }
  }

  // Живая подпись — POST /recurrence/preview на любое изменение правила.
  useEffect(() => {
    if (!template) return;
    window.clearTimeout(previewTimer.current);
    previewTimer.current = window.setTimeout(async () => {
      const { data } = await api.POST('/api/v1/recurrence/preview', {
        body: { anchor: rule.anchor, rule },
      });
      if (!data) return;
      setDescription(data.ok ? data.description ?? null : null);
      setDates(data.ok ? data.preview ?? [] : []);
      setErrors(data.errors ?? []);
    }, 200);
    return () => window.clearTimeout(previewTimer.current);
  }, [template, rule]);

  function submit() {
    if (!template) return;
    setRecurrence.mutate(rule, {
      onSuccess: () => { toaster.push({ title: 'Повторение сохранено' }); onClose(); },
      onError: (e) => setErrors(e instanceof ApiError ? e.errors : [{ field: null, error: e.message }]),
    });
  }

  function clear() {
    if (!template) return;
    clearRecurrence.mutate(undefined, {
      onSuccess: () => { toaster.push({ title: 'Повторение убрано' }); onClose(); },
      onError: (e) => toaster.push({ title: e.message, tone: 'error' }),
    });
  }

  return (
    <Dialog.Root open={template !== null} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={styles.content} aria-describedby={undefined}>
          {template && (
            <>
              <Dialog.Title className={styles.step}>Повторение — «{template.name}»</Dialog.Title>

              <div className={styles.form}>
                <div className={styles.field}>
                  <label htmlFor="rec-words">Словами</label>
                  <input
                    id="rec-words"
                    type="text"
                    placeholder="каждый второй вторник"
                    value={words}
                    onChange={(e) => setWords(e.target.value)}
                  />
                </div>

                <div className={own.ruleGrid}>
                  <div className={styles.field}>
                    <label htmlFor="rec-freq">Частота</label>
                    <select
                      id="rec-freq"
                      value={rule.freq ?? 'weekly'}
                      onChange={(e) => setRule((r) => ({ ...r, freq: e.target.value }))}
                    >
                      {FREQ_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                  <div className={styles.field}>
                    <label htmlFor="rec-interval">Каждые</label>
                    <input
                      id="rec-interval"
                      type="text"
                      inputMode="numeric"
                      value={rule.interval ?? 1}
                      onChange={(e) => setRule((r) => ({ ...r, interval: Number(e.target.value) || 1 }))}
                    />
                  </div>
                </div>

                <div className={styles.field}>
                  <label>Дни недели</label>
                  <div className={own.weekdays}>
                    {WEEKDAYS.map((d) => (
                      <button
                        key={d.value}
                        type="button"
                        className={`small ${(rule.byweekday ?? []).includes(d.value) ? own.weekdayOn : ''}`}
                        onClick={() => setRule((r) => ({ ...r, byweekday: toggleWeekday(r.byweekday ?? [], d.value) }))}
                      >
                        {d.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className={styles.field}>
                  <label htmlFor="rec-monthday">Числа месяца, через запятую</label>
                  <input
                    id="rec-monthday"
                    type="text"
                    placeholder="1, 15, -1"
                    value={monthdayListText(rule.bymonthday)}
                    onChange={(e) => setRule((r) => ({ ...r, bymonthday: parseMonthdayList(e.target.value) }))}
                  />
                </div>

                <DateField
                  id="rec-anchor"
                  value={rule.anchor ?? ''}
                  onChange={(text) => setRule((r) => ({ ...r, anchor: text }))}
                  onParsed={() => {}}
                  onSubmit={submit}
                  withTime={false}
                  label="Якорь — дата первого цикла"
                />
                {errorsFor(errors, 'recurrence.anchor') && <p className="err">{errorsFor(errors, 'recurrence.anchor')}</p>}

                {description && (
                  <p className={own.recurDescription}>
                    {description}
                    {dates.length > 0 && ` — ближайшие: ${dates.map((d) => shortDate(d.date)).join(', ')}`}
                  </p>
                )}
                {generalErrors(errors).map((e, i) => <p key={i} className="err">{e}</p>)}

                <div className={styles.actions}>
                  <button type="button" className="primary" disabled={setRecurrence.isPending} onClick={submit}>
                    Сохранить
                  </button>
                  {template.recurrence && (
                    <button type="button" className="ghost" disabled={clearRecurrence.isPending} onClick={clear}>
                      Убрать повторение
                    </button>
                  )}
                  <button type="button" className="ghost" onClick={onClose}>Отмена <kbd>Esc</kbd></button>
                </div>
              </div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
