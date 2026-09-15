// Состояние диалога быстрого ввода (SLICE2_SPEC.md §5.3): разбор даты на
// лету через `/api/v1/extract-when` (дебаунс 150 мс — своё значение, не
// 200 мс `DateField`: строка короче, дёргать сервер можно чаще) и создание
// через `/api/v1/tasks/quick`. Без React-query: результат нужен диалогу
// целиком и сразу, а не как кэшируемый список.
import { useEffect, useRef, useState } from 'react';
import { api } from '@/api/client';
import type { components } from '@/api/schema';
import { ApiError, unwrapErrors } from '@/api/errors';

export type ExtractResult = components['schemas']['ExtractResult'];

export type QuickDone = { task: string; task_id: number; label: string };

const EXTRACT_DEBOUNCE_MS = 150;

export function useQuickAdd(active: boolean) {
  const [text, setText] = useState('');
  const [extract, setExtract] = useState<ExtractResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [done, setDone] = useState<QuickDone | null>(null);
  const [pending, setPending] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  // Каждое открытие — с чистого листа: предыдущий результат («Создана
  // «…»») не должен пережить закрытие диалога (компонент-хозяин, `Layout`,
  // держит `QuickAdd` смонтированным постоянно, состояние сбрасывать
  // приходится явно).
  useEffect(() => {
    if (!active) return;
    setText('');
    setExtract(null);
    setError(null);
    setDone(null);
    setPending(false);
  }, [active]);

  useEffect(() => {
    window.clearTimeout(timer.current);
    if (!text.trim()) {
      setExtract(null);
      return;
    }
    timer.current = window.setTimeout(async () => {
      const { data } = await api.POST('/api/v1/extract-when', { body: { text } });
      setExtract(data ?? null);
    }, EXTRACT_DEBOUNCE_MS);
    return () => window.clearTimeout(timer.current);
  }, [text]);

  // Без распознанной даты контроль — сегодня (Р8 SLICE2_SPEC.md): подпись
  // предупреждает об этом заранее, а не только после создания.
  const label = extract?.date ? (extract.label ?? '') : 'дата контроля — сегодня';

  function onChange(next: string) {
    setText(next);
    setError(null);
  }

  async function create(): Promise<QuickDone | null> {
    if (!text.trim()) return null;
    setPending(true);
    setError(null);
    try {
      const result = await unwrapErrors(api.POST('/api/v1/tasks/quick', { body: { text } }));
      const d: QuickDone = { task: result.task, task_id: result.task_id, label };
      setDone(d);
      setText('');
      setExtract(null);
      return d;
    } catch (e) {
      if (e instanceof ApiError) setError(e);
      return null;
    } finally {
      setPending(false);
    }
  }

  return { text, onChange, extract, label, error, done, pending, create };
}
