// `/задача?name=…` → `/задача/<id>`. Старые ссылки (палитра, поиск, лента)
// адресуют задачу названием — `GET /api/v1/tasks/resolve` меняет его на
// `task_id` первым переходом, дальше карточка живёт по id (SLICE2_SPEC.md
// §5.1, таблица маршрутов; map-client.md, риск 3).
import { useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { api, errorText } from '@/api/client';

export function ResolveTaskRoute() {
  const [params] = useSearchParams();
  const title = params.get('name') ?? '';
  const navigate = useNavigate();

  const { data, error, isLoading } = useQuery({
    queryKey: ['resolve-task', title],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/tasks/resolve', { params: { query: { title } } });
      if (error || data === undefined) throw new Error(errorText(error));
      return data;
    },
    enabled: title.length > 0,
    retry: false,
  });

  useEffect(() => {
    if (data) navigate(`/задача/${data.task_id}`, { replace: true });
  }, [data, navigate]);

  if (!title) {
    return <main className="wrap"><p className="err">Не указана задача — открой карточку из ленты.</p></main>;
  }
  if (isLoading) return <main className="wrap"><p className="muted">Загрузка…</p></main>;
  if (error) return <main className="wrap"><p className="err">Задача не найдена: {title}</p></main>;
  return null;
}
