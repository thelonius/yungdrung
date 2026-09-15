// ЗАГЛУШКА (владелец F3, SLICE2_SPEC.md §5.6). Сигнатура зафиксирована срезом
// 2 §5.1 для `router.tsx` (`/шаблоны`) — тело меняется целиком.
import type { JSX } from 'react';

export function TemplatesPage(): JSX.Element {
  return (
    <main className="wrap">
      <h1>Шаблоны</h1>
      <p className="muted">
        Заглушка F3 (SLICE2_SPEC.md §5.6): список шаблонов, диалог развёртывания
        и форма с предпросмотром и повторением появятся здесь.
      </p>
    </main>
  );
}
