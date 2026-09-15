// ЗАГЛУШКА (владелец F2, SLICE2_SPEC.md §5.5). Сигнатура зафиксирована срезом
// 2 §5.1 для `router.tsx` (`/задача/:id`) — тело меняется целиком, `useParams`
// здесь уже настоящий (маршрут читает `id`), остальное дорисует F2.
import type { JSX } from 'react';
import { useParams } from 'react-router';

export function TaskCardPage(): JSX.Element {
  const { id } = useParams();
  return (
    <main className="wrap">
      <h1>Задача №{id}</h1>
      <p className="muted">
        Заглушка F2 (SLICE2_SPEC.md §5.5): карточка (<code>GET /api/v1/tasks/{'{id}'}</code>),
        чеклист шагов и общий <code>ControlDialog</code> (<code>idPrefix=&quot;card&quot;</code>) появятся здесь.
      </p>
    </main>
  );
}
