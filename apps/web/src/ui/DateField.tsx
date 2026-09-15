// Поле даты: текст главный («+3», «завтра в полдесятого»), разбор — только у
// ядра через /api/v1/parse-date, подпись под полем показывает, как поняли.
// Общий компонент (перенесён из features/feed в срезе 2, §5.1): карточка и
// форма новой задачи ставят по несколько полей дат на одном экране, поэтому
// `id` обязателен — раньше он был жёстко зашит ("c-date"), и второй экземпляр
// поля на странице дал бы дубль id (map-client.md, риск 2).
import { useEffect, useRef, useState } from 'react';
import { api } from '@/api/client';
import type { WhenResult } from '@/api/client';
import styles from './control/ControlDialog.module.css';

// Пресеты меняются `withTime`: там, где времени у даты не бывает (старт
// шаблона, дата старта задачи), «через час» и «полдесятого» не к месту —
// решение CLAUDE.md («сдвиги приращениями», Yd.DateField.withTime).
const PRESETS_TIME: { label: string; when: string }[] = [
  { label: 'через час', when: 'через час' },
  { label: 'завтра утром', when: '+1 09:00' },
  { label: 'через 3 дня', when: '+3' },
  { label: 'через неделю', when: '+7' },
];

const PRESETS_DATE: { label: string; when: string }[] = [
  { label: 'сегодня', when: 'сегодня' },
  { label: 'завтра', when: 'завтра' },
  { label: 'пн', when: 'пн' },
];

type Props = {
  id: string;
  value: string;
  onChange: (text: string) => void;
  onParsed: (r: WhenResult | null) => void;
  onSubmit: () => void;
  autoFocus?: boolean;
  inputRef?: React.RefObject<HTMLInputElement | null>;
  /** По умолчанию `true` (пресеты с часами). `false` — только по дню
   * (SLICE2_SPEC.md §5.1: `DateField withTime?: boolean`, решение Р16). */
  withTime?: boolean;
  /** Подпись поля — по умолчанию как у окна контроля; карточка и форма
   * задают свою («дата начала», «дата контроля N-го шага»). Необязательный
   * проп не входит в буквальный список §5.1, добавлен аддитивно: без него
   * каждое поле на карточке подписывалось бы одинаково (см. deviations). */
  label?: string;
};

export function DateField({
  id, value, onChange, onParsed, onSubmit, autoFocus, inputRef,
  withTime = true, label = 'Новая дата контроля',
}: Props) {
  const [parsed, setParsed] = useState<WhenResult | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    window.clearTimeout(timer.current);
    if (!value.trim()) {
      setParsed(null);
      onParsed(null);
      return;
    }
    timer.current = window.setTimeout(async () => {
      const { data } = await api.POST('/api/v1/parse-date', { body: { text: value } });
      setParsed(data ?? null);
      onParsed(data ?? null);
    }, 200);
    return () => window.clearTimeout(timer.current);
    // onParsed намеренно не в зависимостях: это колбэк родителя, он меняется
    // на каждом рендере, а перезапускать разбор от этого не нужно.
  }, [value]);

  const bad = parsed && !parsed.ok;
  const presets = withTime ? PRESETS_TIME : PRESETS_DATE;
  return (
    <div className={styles.field}>
      <label htmlFor={id}>{label}</label>
      <div className={styles.presets}>
        {presets.map((p) => (
          <button
            type="button"
            key={p.when}
            className={`small ${value === p.when ? styles.presetOn : ''}`}
            onClick={() => onChange(p.when)}
          >
            {p.label}
          </button>
        ))}
      </div>
      <input
        id={id}
        ref={inputRef}
        type="text"
        autoComplete="off"
        autoFocus={autoFocus}
        className={bad ? 'invalid' : ''}
        placeholder="или свой вариант: завтра в 9, 18.08, пн, полдесятого"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onSubmit(); } }}
      />
      <span className={`${styles.preview} ${parsed?.past ? styles.past : ''}`}>
        {parsed?.ok ? parsed.label : parsed?.error ?? ''}
      </span>
    </div>
  );
}
