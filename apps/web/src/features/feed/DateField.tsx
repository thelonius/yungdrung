// Поле даты: текст главный («+3», «завтра в полдесятого»), разбор — только у
// ядра через /api/v1/parse-date, подпись под полем показывает, как поняли.
import { useEffect, useRef, useState } from 'react';
import { api } from '@/api/client';
import type { WhenResult } from '@/api/client';
import { PRESETS } from './model';
import styles from './ControlDialog.module.css';

type Props = {
  value: string;
  onChange: (text: string) => void;
  onParsed: (r: WhenResult | null) => void;
  onSubmit: () => void;
  autoFocus?: boolean;
  inputRef?: React.RefObject<HTMLInputElement | null>;
};

export function DateField({ value, onChange, onParsed, onSubmit, autoFocus, inputRef }: Props) {
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
  return (
    <div className={styles.field}>
      <label htmlFor="c-date">Новая дата контроля</label>
      <div className={styles.presets}>
        {PRESETS.map((p) => (
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
        id="c-date"
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
