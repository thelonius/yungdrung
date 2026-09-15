// Ошибки контракта как структура, а не строка. `client.ts::errorText` годится
// для тоста («сделать» / «не сделать»), но форма обязана подсветить поле —
// и для этого ей нужен исходный `errors[]`, не склейка через «; »
// (SLICE2_SPEC.md §5.1, риск 4 map-client: «unwrap теряет errors[]»).
import { errorText } from './client';
import type { ApiErrors, FieldError } from './client';

export type { FieldError };

/** Брошенное исключение несёт список ошибок целиком; `message` — для тостов
 * и мест, которым не нужны поля (та же склейка, что `errorText`). */
export class ApiError extends Error {
  errors: FieldError[];

  constructor(errors: FieldError[]) {
    super(errors.map((e) => e.error).join('; ') || 'не получилось');
    this.name = 'ApiError';
    this.errors = errors;
  }
}

/** Как `unwrap` в `useFeed.ts`, но бросает `ApiError` с полями, а не голый
 * `Error`: форме нужно разложить ошибки под свои поля, а не показать одну
 * строку. Сетевой сбой (нет `errors[]` вовсе) заворачивается в одну запись
 * с `field: null`, чтобы вызывающему не пришлось разбирать два случая. */
export async function unwrapErrors<T>(p: Promise<{ data?: T; error?: unknown }>): Promise<T> {
  const { data, error } = await p;
  if (error !== undefined || data === undefined) {
    const e = error as Partial<ApiErrors> | undefined;
    const errors = Array.isArray(e?.errors) && e.errors.length
      ? e.errors
      : [{ field: null, error: errorText(error) }];
    throw new ApiError(errors);
  }
  return data;
}

/** Первая ошибка поля по точному скобочному пути (`steps[1].control_date`),
 * или `undefined`, если поле чистое. Путь сравнивается как есть — разбор на
 * индексы/лист см. `fieldPath.ts`, он нужен там, где путь надо адресовать
 * программно (раскрыть группу с ошибкой), а не только показать под полем. */
export function errorsFor(errors: FieldError[], path: string): string | undefined {
  return errors.find((e) => e.field === path)?.error;
}

/** Ошибки без привязки к конкретному полю (`field: null`) — общий блок формы. */
export function generalErrors(errors: FieldError[]): string[] {
  return errors.filter((e) => e.field === null).map((e) => e.error);
}
