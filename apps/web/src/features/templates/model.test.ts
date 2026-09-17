import { describe, expect, it, vi } from 'vitest';
import {
  decomposeOffsets, emptyRule, filesWord, monthdayListText, parseMonthdayList,
  pluralRu, recurrencePayload, ruleFromView, saveThenUpload, stepErrors, stepsWord,
  sumOffsets, templateMeta, toggleWeekday, WEEKDAYS,
} from './model';
import type { FieldError, RecurrenceView } from '@/api/client';

describe('sumOffsets', () => {
  it('складывает промежутки накопленной суммой', () => {
    expect(sumOffsets([0, 3, 2])).toEqual([0, 3, 5]);
  });
  it('нечисловое значение ломает сумму, дальше — тот же нетронутый текст', () => {
    expect(sumOffsets([0, 3, 'abc', 2])).toEqual([0, 3, 'abc', 'abc']);
  });
  it('пустой список', () => {
    expect(sumOffsets([])).toEqual([]);
  });
});

describe('decomposeOffsets', () => {
  it('обратно к промежуткам: первый как есть, дальше — разница с предыдущим', () => {
    expect(decomposeOffsets([0, 3, 5])).toEqual([0, 3, 2]);
  });
  it('туда и обратно даёт исходные промежутки', () => {
    const increments = [0, 3, 2, 7];
    expect(decomposeOffsets(sumOffsets(increments) as number[])).toEqual(increments);
  });
});

describe('pluralRu', () => {
  it('11–14 всегда «много», а не по последней цифре', () => {
    expect(pluralRu(11, 'a', 'b', 'c')).toBe('c');
    expect(pluralRu(21, 'a', 'b', 'c')).toBe('a');
    expect(pluralRu(2, 'a', 'b', 'c')).toBe('b');
    expect(stepsWord(11)).toBe('шагов');
    expect(stepsWord(21)).toBe('шаг');
    expect(stepsWord(2)).toBe('шага');
    expect(stepsWord(5)).toBe('шагов');
    expect(filesWord(1)).toBe('файл');
  });
});

describe('templateMeta', () => {
  it('собирает строку метаданных карточки', () => {
    expect(templateMeta({ steps_count: 3, attachments_count: 2, tags: ['grant', 'q4'] }))
      .toBe('3 шага · файлов: 2 · grant, q4');
  });
  it('без вложений и тегов — только шаги', () => {
    expect(templateMeta({ steps_count: 1, attachments_count: 0, tags: [] })).toBe('1 шаг');
  });
});

describe('parseMonthdayList / monthdayListText', () => {
  it('разбирает список чисел через запятую, отбрасывая мусор', () => {
    expect(parseMonthdayList('1, 15, -1, abc, ')).toEqual([1, 15, -1]);
  });
  it('пустая строка — пустой список', () => {
    expect(parseMonthdayList('')).toEqual([]);
  });
  it('обратно в текст', () => {
    expect(monthdayListText([1, 15, -1])).toBe('1, 15, -1');
    expect(monthdayListText(null)).toBe('');
  });
});

describe('toggleWeekday', () => {
  it('добавляет и убирает день, список остаётся по порядку', () => {
    expect(toggleWeekday([0, 2], 1)).toEqual([0, 1, 2]);
    expect(toggleWeekday([0, 1, 2], 1)).toEqual([0, 2]);
  });
  it('WEEKDAYS начинается с понедельника (0)', () => {
    expect(WEEKDAYS[0]).toEqual({ value: 0, label: 'пн' });
    expect(WEEKDAYS).toHaveLength(7);
  });
});

const VIEW: RecurrenceView = {
  anchor: '2026-09-15', freq: 'monthly', interval: 1, byweekday: [], bymonthday: [15],
  bysetpos: [], bymonth: [], holiday_shift: 'before', lead_days: 2, until: '2027-01-01',
  paused: true, description: 'раз в месяц, числа: 15',
};

describe('ruleFromView / recurrencePayload — round-trip скрытых полей (§5.6)', () => {
  it('ruleFromView переносит все поля кроме description в состояние формы', () => {
    const rule = ruleFromView(VIEW);
    expect(rule.holiday_shift).toBe('before');
    expect(rule.lead_days).toBe(2);
    expect(rule.until).toBe('2027-01-01');
    expect(rule.paused).toBe(true);
    expect('description' in rule).toBe(false);
  });
  it('нет сохранённого правила — пустая форма с рабочими дефолтами', () => {
    expect(ruleFromView(null)).toEqual(emptyRule());
  });
  it('правка одного виджета (freq) не задевает скрытые поля — спред состояния', () => {
    const rule = ruleFromView(VIEW);
    const patched = { ...rule, freq: 'weekly' };
    expect(patched.holiday_shift).toBe('before');
    expect(patched.lead_days).toBe(2);
    expect(patched.until).toBe('2027-01-01');
    expect(patched.paused).toBe(true);
  });
  it('recurrencePayload шлёт сохранённое повторение шаблона нетронутым при правке TemplateIn', () => {
    expect(recurrencePayload(VIEW)).toBe(VIEW);
    expect(recurrencePayload(null)).toBeNull();
  });
});

describe('stepErrors', () => {
  it('раскладывает ошибки шагов по индексу и полю, не по названию', () => {
    const errors: FieldError[] = [
      { field: 'steps[0].offset_days', error: 'Число' },
      { field: 'steps[1].time_of_day', error: 'Неверное время' },
      { field: 'name', error: 'Занято' },
    ];
    expect(stepErrors(errors)).toEqual({
      0: { offset_days: 'Число' },
      1: { time_of_day: 'Неверное время' },
    });
  });
  it('без ошибок шагов — пустой объект', () => {
    expect(stepErrors([{ field: 'name', error: 'x' }])).toEqual({});
  });
});

describe('saveThenUpload — файлы уходят после записи, не до', () => {
  it('save вызывается раньше upload, и upload получает результат save', async () => {
    const order: string[] = [];
    const save = vi.fn(async () => { order.push('save'); return { name: 'grant' }; });
    const upload = vi.fn(async (saved: { name: string }) => { order.push(`upload:${saved.name}`); });
    const result = await saveThenUpload(save, upload);
    expect(order).toEqual(['save', 'upload:grant']);
    expect(result).toEqual({ name: 'grant' });
  });
});
