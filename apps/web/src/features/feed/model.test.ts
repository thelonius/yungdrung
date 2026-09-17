import { describe, expect, it } from 'vitest';
import type { FeedResult, FeedRow, MarkResult } from '@/api/client';
import { applyMark, applyServer, moveFocus, outcomeText } from './model';

function row(task_id: number, step: number, extra: Partial<FeedRow> = {}): FeedRow {
  return {
    task_id, step, task: `Задача ${task_id}`, title: `Шаг ${step}`, group: null, note: null,
    control_at: '2026-09-08', show_at: '2026-09-08T09:00:00', state: 'due', postponed: 0,
    stalled: false, tags: [], last_reason: null, actions: ['done', 'notdone', 'defer', 'skip'],
    ...extra,
  };
}

const feed: FeedResult = {
  now: '2026-09-08T11:00:00',
  feed: [row(1, 1), row(2, 3, { stalled: true, postponed: 3 })],
  overdue_count: 1,
  counts: { overdue: 1, today: 2, waiting: 4 },
  next_ahead: null,
  stalled_count: 1,
  broken: [],
};

describe('applyMark', () => {
  it('сделано убирает строку и уменьшает «сегодня»', () => {
    const r = applyMark(feed, '1:1', 'done');
    expect(r.feed.map((x) => x.task_id)).toEqual([2]);
    expect(r.counts).toEqual({ overdue: 1, today: 1, waiting: 4 });
  });
  it('перенос и «не сделано» уводят строку в «ждут»', () => {
    expect(applyMark(feed, '1:1', 'defer').counts.waiting).toBe(5);
    expect(applyMark(feed, '1:1', 'notdone').counts.waiting).toBe(5);
  });
  it('счётчик буксующих пересчитывается по оставшимся', () => {
    expect(applyMark(feed, '2:3', 'fail').stalled_count).toBe(0);
  });
  it('чужой ключ ничего не меняет', () => {
    expect(applyMark(feed, '9:9', 'done')).toBe(feed);
  });
});

describe('applyServer', () => {
  const result: MarkResult = {
    ok: true, op: 'defer', task_id: 1, task: 'Задача 1', step: 1, status: 'pending',
    task_status: 'due', next_step_id: null, next_step_title: null, dates_assigned: [],
    next_check: '2026-09-08 15:00', stalled: 0, hint: null, undone: null, control_date: null,
    row: row(1, 1, { show_at: '2026-09-08T15:00:00' }),
    counts: { overdue: 1, today: 2, waiting: 4 },
  };
  it('строка возвращается, если ядро говорит «сегодня», и встаёт по времени', () => {
    const r = applyServer(applyMark(feed, '1:1', 'defer'), result);
    expect(r.feed.map((x) => x.task_id)).toEqual([2, 1]);
    expect(r.counts.today).toBe(2);
  });
  it('строка не возвращается, если она ушла в «ждут»', () => {
    const r = applyServer(applyMark(feed, '1:1', 'defer'),
      { ...result, row: { ...result.row!, state: 'waiting' } });
    expect(r.feed.map((x) => x.task_id)).toEqual([2]);
  });
});

describe('moveFocus', () => {
  it('ходит по строкам и упирается в края', () => {
    expect(moveFocus(0, 1, 3)).toBe(1);
    expect(moveFocus(2, 1, 3)).toBe(2);
    expect(moveFocus(0, -1, 3)).toBe(0);
  });
  it('без фокуса вниз даёт первую, вверх — последнюю', () => {
    expect(moveFocus(-1, 1, 3)).toBe(0);
    expect(moveFocus(-1, -1, 3)).toBe(2);
  });
  it('пустой список — нет фокуса', () => {
    expect(moveFocus(0, 1, 0)).toBe(-1);
  });
});

describe('outcomeText', () => {
  const base: MarkResult = {
    ok: true, op: 'done', task_id: 1, task: 'Грант', step: 1, status: 'done', task_status: 'waiting',
    next_step_id: 2, next_step_title: 'Отправить', dates_assigned: [2], next_check: null,
    stalled: 0, hint: null, undone: null, control_date: null, row: null,
    counts: { overdue: 0, today: 0, waiting: 1 },
  };
  it('называет следующий шаг или закрытие задачи', () => {
    expect(outcomeText('done', base)).toBe('Сделано. Следующий шаг — Отправить');
    expect(outcomeText('done', { ...base, task_status: 'done' })).toBe('«Грант» закрыта целиком');
  });
});
