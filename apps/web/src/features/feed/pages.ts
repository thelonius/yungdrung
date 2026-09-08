// Страницы старого интерфейса, пока они не переехали (REFACTOR.md, срезы 2–3).
export const PAGES: { label: string; href: string }[] = [
  { label: 'Новая задача', href: '/новая' },
  { label: 'Все задачи', href: '/задачи' },
  { label: 'Архив', href: '/архив' },
  { label: 'Шаблоны', href: '/шаблоны' },
  { label: 'База знаний', href: '/база' },
  { label: 'Настройки', href: '/настройки' },
  { label: 'Старая лента', href: '/старая' },
];

export type Hit = { source_type: string; source_id: string; title: string; preview?: string };

export function hitHref(h: Hit): string {
  return h.source_type === 'kb_note'
    ? `/база/запись?id=${encodeURIComponent(h.source_id)}`
    : `/задача?name=${encodeURIComponent(h.source_id)}`;
}
