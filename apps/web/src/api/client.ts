// Типизированный клиент к `/api/v1`. Типы — из `schema.d.ts`, который
// генерирует `make types` из `api/openapi.json`: поле, добавленное в Pydantic-
// модель и забытое здесь, ловит `tsc`, а не заказчик (REFACTOR.md, таблица).
import createClient from 'openapi-fetch';
import type { components, paths } from './schema';

export type FeedRow = components['schemas']['FeedRow'];
export type FeedResult = components['schemas']['FeedResult'];
export type BacklogResult = components['schemas']['BacklogResult'];
export type MarkResult = components['schemas']['MarkResult'];
export type Counts = components['schemas']['Counts'];
export type WhenResult = components['schemas']['WhenResult'];
export type MarkOp = 'done' | 'notdone' | 'defer' | 'fail' | 'skip';

/** Тело ошибки по контракту: список `{field, error}` (CONTRACT.md). */
export type ApiErrors = { ok: false; errors: { field: string | null; error: string }[] };

// База — origin страницы: в браузере это тот же сервер, а вне браузера (тесты)
// относительный URL в Request не разбирается.
export const api = createClient<paths>({
  baseUrl: typeof window !== 'undefined' ? window.location.origin : '',
  // Ленивая обёртка, а не `globalThis.fetch` напрямую: клиент создаётся при
  // импорте модуля, и тесты подменяют fetch уже после этого.
  fetch: (req) => globalThis.fetch(req),
});

export function errorText(err: unknown): string {
  const e = err as Partial<ApiErrors> | undefined;
  if (e && Array.isArray(e.errors) && e.errors.length) {
    return e.errors.map((x) => x.error).join('; ');
  }
  return 'не получилось';
}

/** Ключ строки: стабилен между перерисовками, чем бы задача ни называлась. */
export function rowKey(r: { task_id: number; step: number }): string {
  return `${r.task_id}:${r.step}`;
}
