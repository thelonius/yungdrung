// Страницы шапки и палитры команд (срез 2, §5.1). Внутренние — уже на
// React-роутере (`to`, переход без перезагрузки), внешние — пока прежние
// страницы `static/*.html` (`href`, обычная ссылка) до своих срезов.
export type Page =
  | { label: string; to: string }
  | { label: string; href: string };

export const PAGES: Page[] = [
  { label: 'Новая задача', to: '/новая' },
  { label: 'Все задачи', href: '/задачи' },
  { label: 'Архив', href: '/архив' },
  { label: 'Шаблоны', to: '/шаблоны' },
  { label: 'База знаний', href: '/база' },
  { label: 'Настройки', href: '/настройки' },
  { label: 'Старая лента', href: '/старая' },
];

export type Hit = { source_type: string; source_id: string; title: string; preview?: string };

/**
 * Куда ведёт находка палитры. Карточка задачи адресуется `task_id`
 * (`/задача/:id`), а `/api/v1/search` пока отдаёт `source_id` названием
 * (риск 3 map-client.md, «SearchResult нетипизирован») — поэтому здесь
 * `/задача?name=…`, тот же путь, что и старые ссылки, и `ResolveTaskRoute`
 * меняет его на `/задача/<id>` первым же переходом.
 */
export function hitHref(h: Hit): string {
  return h.source_type === 'kb_note'
    ? `/база/запись?id=${encodeURIComponent(h.source_id)}`
    : `/задача?name=${encodeURIComponent(h.source_id)}`;
}

/** Пути, которые понимает React-роутер: используются, чтобы решить —
 * `navigate()` (без перезагрузки) или обычный переход по `href`. */
export function isInternalPath(path: string): boolean {
  return path === '/' || path === '/лента' || path === '/новая' || path === '/шаблоны'
    || path.startsWith('/задача/') || path.startsWith('/задача?') || path === '/задача';
}
