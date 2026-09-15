// Таблица маршрутов — react-router@7, декларативный режим (`Routes`/`Route`,
// не `createBrowserRouter`: решение среза 1c). Пути по-русски — их видит
// человек (REFACTOR.md). Остальное из `PAGES` — внешние ссылки, обычные
// переходы, пока раздел не переехал на React (SLICE2_SPEC.md §5.1).
import { Route, Routes } from 'react-router';
import { FeedPage } from '@/features/feed/FeedPage';
import { NewTaskPage } from '@/features/tasks/form/NewTaskPage';
import { TaskCardPage } from '@/features/tasks/card/TaskCardPage';
import { TemplatesPage } from '@/features/templates/TemplatesPage';
import { Layout } from './Layout';
import { ResolveTaskRoute } from './ResolveTaskRoute';

export function AppRouter() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<FeedPage />} />
        <Route path="/лента" element={<FeedPage />} />
        <Route path="/новая" element={<NewTaskPage />} />
        {/* Порядок важен: `/задача/:id` — точная адресация по id, `/задача`
            (без сегмента, только `?name=`) — старые ссылки, см. `ResolveTaskRoute`. */}
        <Route path="/задача/:id" element={<TaskCardPage />} />
        <Route path="/задача" element={<ResolveTaskRoute />} />
        <Route path="/шаблоны" element={<TemplatesPage />} />
      </Route>
    </Routes>
  );
}
