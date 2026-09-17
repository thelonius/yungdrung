import { describe, expect, it } from 'vitest';
import {
  addMonths, captionOf, fromIso, iso, isoDay, monthGrid, nextMonday, weekdayNames, weekStart,
} from './days';

describe('дни месяца', () => {
  it('iso не съезжает на сутки назад в положительном поясе', () => {
    // Ради этого тест и написан: toISOString отдал бы 2026-09-17 для
    // московского полудня 18-го.
    expect(iso(new Date(2026, 8, 18))).toBe('2026-09-18');
    expect(iso(new Date(2026, 0, 1))).toBe('2026-01-01');
  });

  it('fromIso берёт день и выбрасывает время', () => {
    expect(iso(fromIso('2026-09-18T09:30:00')!)).toBe('2026-09-18');
    expect(fromIso('завтра')).toBeNull();
    expect(fromIso(null)).toBeNull();
  });

  it('месяц прибавляется с прижатием к концу', () => {
    expect(iso(addMonths(new Date(2026, 0, 31), 1))).toBe('2026-02-28');
    expect(iso(addMonths(new Date(2028, 0, 31), 1))).toBe('2028-02-29');
    expect(iso(addMonths(new Date(2026, 2, 31), -1))).toBe('2026-02-28');
  });

  it('в сетке всегда 42 дня, первый — начало недели', () => {
    const дни = monthGrid(new Date(2026, 8, 1), 1);
    expect(дни).toHaveLength(42);
    expect(iso(дни[0])).toBe('2026-08-31');
    expect(isoDay(дни[0])).toBe(1);
    expect(дни.filter((d) => d.getMonth() === 8)).toHaveLength(30);
  });

  it('февраль без хвостов тоже занимает шесть недель', () => {
    expect(monthGrid(new Date(2027, 1, 1), 1)).toHaveLength(42);
  });

  it('понедельник — ближайший ПОСЛЕ сегодня, как у ядра', () => {
    expect(iso(nextMonday(new Date(2026, 8, 17)))).toBe('2026-09-21');
    // С самого понедельника — следующий, а не сегодняшний.
    expect(iso(nextMonday(new Date(2026, 8, 21)))).toBe('2026-09-28');
  });

  it('Home ведёт на понедельник своей недели', () => {
    expect(iso(weekStart(new Date(2026, 8, 20), 1))).toBe('2026-09-14');
  });

  it('подписи по-русски, без «г.» в заголовке', () => {
    expect(captionOf(new Date(2026, 8, 1))).toBe('Сентябрь 2026');
    expect(weekdayNames(1).map((w) => w.short)).toEqual(['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']);
  });
});
