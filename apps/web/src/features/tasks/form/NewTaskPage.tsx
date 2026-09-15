// ЗАГЛУШКА (владелец F1, SLICE2_SPEC.md §5.4). Сигнатура зафиксирована срезом
// 2 §5.1 для `router.tsx` — тело меняется целиком, экспорт остаётся.
import type { JSX } from 'react';

export function NewTaskPage(): JSX.Element {
  return (
    <main className="wrap">
      <h1>Новая задача</h1>
      <p className="muted">
        Заглушка F1 (SLICE2_SPEC.md §5.4): форма с шагами (`StepsEditor`), живой
        проверкой через <code>/api/v1/tasks/plan</code> и подсказками базы
        знаний (`KbHints`) появится здесь.
      </p>
    </main>
  );
}
