// Чистые функции ленты: что происходит с кэшем при отметке и куда уходит фокус.
// Без React и без сети, чтобы проверяться напрямую (model.test.ts).
import type { FeedResult, FeedRow, MarkOp, MarkResult } from '@/api/client';
import { rowKey } from '@/api/client';

/**
 * Оптимистичное применение отметки к ленте. Любая из пяти отметок убирает
 * строку: сделанный, проваленный и снятый шаг закрыты, перенесённый ушёл в
 * «ждут», а «не сделан» без даты получает завтра и из сегодняшнего горизонта
 * выпадает. Счётчики двигаются по тем же правилам, которые ядро гарантирует
 * (CONTRACT.md); ответ сервера затем подменяет их настоящими.
 */
export function applyMark(feed: FeedResult, key: string, op: MarkOp): FeedResult {
  const row = feed.feed.find((r) => rowKey(r) === key);
  if (!row) return feed;
  const rest = feed.feed.filter((r) => r !== row);
  const counts = { ...feed.counts, today: Math.max(0, feed.counts.today - 1) };
  if (op === 'defer' || op === 'notdone') counts.waiting += 1;
  return {
    ...feed,
    feed: rest,
    counts,
    stalled_count: rest.filter((r) => r.stalled).length,
  };
}

/** Убрать строку из любого списка (лента или завал) по ключу. */
export function withoutRow<T extends FeedRow>(rows: T[], key: string): T[] {
  return rows.filter((r) => rowKey(r) !== key);
}

/**
 * Состояние после ответа сервера: счётчики — как сказало ядро; строка
 * возвращается в ленту, только если ядро говорит, что она всё ещё «сегодня»
 * (перенос на другой час того же дня). Остальное досчитает перезапрос.
 */
export function applyServer(feed: FeedResult, result: MarkResult): FeedResult {
  const rows = withoutRow(feed.feed, `${result.task_id}:${result.step}`);
  if (result.row && result.row.state === 'due') rows.push(result.row);
  rows.sort((a, b) => (a.show_at ?? '9999').localeCompare(b.show_at ?? '9999'));
  return { ...feed, feed: rows, counts: result.counts, overdue_count: result.counts.overdue };
}

/** Фокус по индексу: клампится в границы, на пустом списке — нет фокуса. */
export function clampFocus(index: number, length: number): number {
  if (length === 0) return -1;
  return Math.min(Math.max(index, 0), length - 1);
}

export function moveFocus(index: number, delta: number, length: number): number {
  if (length === 0) return -1;
  const from = index < 0 ? (delta > 0 ? -1 : length) : index;
  return clampFocus(from + delta, length);
}

/** Подпись тоста после отметки — те же слова, что были на старой ленте. */
export function outcomeText(op: MarkOp, r: MarkResult): string {
  switch (op) {
    case 'done':
      return r.task_status === 'done'
        ? `«${r.task}» закрыта целиком`
        : `Сделано. Следующий шаг — ${r.next_step_title ?? '—'}`;
    case 'notdone':
      return r.hint ? `Отмечено. ${r.hint}` : 'Отмечено, вернёмся к нему завтра';
    case 'defer':
      return `Перенесено на ${r.next_check}`;
    case 'fail':
      return 'Шаг закрыт как несостоявшийся, задача идёт дальше';
    case 'skip':
      return 'Шаг снят, задача идёт дальше';
  }
}

export function shortTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
}

/** Пресеты переноса — те же четыре, что были на старой ленте. */
export const PRESETS: { label: string; when: string }[] = [
  { label: 'через час', when: 'через час' },
  { label: 'завтра утром', when: '+1 09:00' },
  { label: 'через 3 дня', when: '+3' },
  { label: 'через неделю', when: '+7' },
];
