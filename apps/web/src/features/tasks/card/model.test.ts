import { describe, expect, it } from 'vitest';
import type { CardStep, FieldError } from '@/api/client';
import {
  addLeaf, buildEditSteps, deadlineText, errorAt, errorPaths, fromCard, getNode, groupModeLabel, historyLine,
  leafRows, missingStepsError, moveSibling, newLeaf, patchNode, progressText, shortControlText, splitToSubsteps,
} from './model';

function leaf(id: number, extra: Partial<CardStep> = {}): CardStep {
  return {
    id, title: `Шаг ${id}`, status: 'pending', start_date: null, control_date: '2026-09-08 09:00',
    completed_date: null, note: null, mode: null, closed: false, active: true, stalled: 0, state: 'due',
    row: null, actions: ['done', 'notdone', 'defer', 'fail', 'skip'], steps: [],
    ...extra,
  };
}

function group(id: number, steps: CardStep[], extra: Partial<CardStep> = {}): CardStep {
  return {
    id, title: `Группа ${id}`, status: 'pending', start_date: null, control_date: null, completed_date: null,
    note: null, mode: 'par', closed: false, active: false, stalled: 0, state: null, row: null, actions: [], steps,
    ...extra,
  };
}

describe('fromCard / buildEditSteps', () => {
  it('лист собирается в {id, title, start_date, control_date, note}', () => {
    const draft = fromCard([leaf(1)]);
    expect(buildEditSteps(draft)).toEqual([
      { id: 1, title: 'Шаг 1', start_date: null, control_date: '2026-09-08 09:00', note: null, steps: [] },
    ]);
  });
  it('группа собирается в {id, title, mode, note, steps} — своих дат не отправляет', () => {
    const draft = fromCard([group(1, [leaf(2)])]);
    expect(buildEditSteps(draft)).toEqual([
      { id: 1, title: 'Группа 1', mode: 'par', note: null, steps: [
        { id: 2, title: 'Шаг 2', start_date: null, control_date: '2026-09-08 09:00', note: null, steps: [] },
      ] },
    ]);
  });
  it('null-название не уезжает как null в StepEditIn (title: string)', () => {
    const draft = fromCard([leaf(1, { title: null })]);
    expect(buildEditSteps(draft)[0].title).toBe('');
  });
});

describe('leafRows', () => {
  it('только листья, в порядке обхода — дети группы между соседними верхними листьями', () => {
    const nodes = fromCard([leaf(1), group(2, [leaf(3), leaf(4)]), leaf(5)]);
    expect(leafRows(nodes).map((r) => [r.node.id, r.indices])).toEqual([
      [1, [0]], [3, [1, 0]], [4, [1, 1]], [5, [2]],
    ]);
  });
});

describe('getNode / patchNode', () => {
  it('находит и правит узел по цепочке индексов, не трогая соседей', () => {
    const nodes = fromCard([leaf(1), group(2, [leaf(3)])]);
    expect(getNode(nodes, [1, 0])?.id).toBe(3);
    const patched = patchNode(nodes, [1, 0], { title: 'Новое название' });
    expect(getNode(patched, [1, 0])?.title).toBe('Новое название');
    expect(getNode(patched, [0])?.title).toBe('Шаг 1'); // сосед не тронут
    expect(getNode(nodes, [1, 0])?.title).toBe('Шаг 3'); // исходное дерево неизменно
  });
});

describe('addLeaf', () => {
  it('«+ Шаг» — в конец верхнего уровня', () => {
    const nodes = fromCard([leaf(1)]);
    const next = addLeaf(nodes, []);
    expect(next).toHaveLength(2);
    expect(next[1].id).toBeNull();
  });
  it('«+ Подшаг» — в конец детей группы', () => {
    const nodes = fromCard([group(1, [leaf(2)])]);
    const next = addLeaf(nodes, [0]);
    expect(next[0].steps).toHaveLength(2);
    expect(next[0].steps[1].id).toBeNull();
  });
});

describe('splitToSubsteps', () => {
  it('лист превращается в группу с одним пустым подшагом, свои даты уходят', () => {
    const node = fromCard([leaf(1, { control_date: '2026-09-08 09:00' })])[0];
    const split = splitToSubsteps(node);
    expect(split.id).toBe(1); // тот же шаг, сменивший роль
    expect(split.control_date).toBeNull();
    expect(split.mode).toBe('par');
    expect(split.steps).toEqual([newLeaf()]);
  });
});

describe('moveSibling', () => {
  it('переставляет соседей одного уровня', () => {
    const nodes = fromCard([leaf(1), leaf(2), leaf(3)]);
    const next = moveSibling(nodes, [], 0, 2);
    expect(next.map((n) => n.id)).toEqual([2, 3, 1]);
  });
  it('группа едет с детьми', () => {
    const nodes = fromCard([leaf(1), group(2, [leaf(3)]), leaf(4)]);
    const next = moveSibling(nodes, [], 1, 0);
    expect(next.map((n) => n.id)).toEqual([2, 1, 4]);
    expect(next[0].steps.map((s) => s.id)).toEqual([3]);
  });
  it('не переставляет детей чужой группы', () => {
    const nodes = fromCard([group(1, [leaf(2), leaf(3)])]);
    const next = moveSibling(nodes, [0], 0, 1);
    expect(next[0].steps.map((s) => s.id)).toEqual([3, 2]);
  });
});

describe('errorAt / errorPaths', () => {
  const errors: FieldError[] = [
    { field: 'title', error: 'Занятое название' },
    { field: 'steps[1].control_date', error: 'Не разобрал дату' },
    { field: 'steps[1].steps[0].title', error: 'Пустое название' },
  ];
  it('находит ошибку по точной цепочке индексов и листу', () => {
    expect(errorAt(errors, [], 'title')).toBe('Занятое название');
    expect(errorAt(errors, [1], 'control_date')).toBe('Не разобрал дату');
    expect(errorAt(errors, [1, 0], 'title')).toBe('Пустое название');
    expect(errorAt(errors, [0], 'control_date')).toBeUndefined();
  });
  it('перечисляет узлы, задетые ошибками, без дублей', () => {
    expect(errorPaths(errors).sort()).toEqual(['1', '1,0']);
  });
});

describe('missingStepsError', () => {
  it('склеивает ошибки поля steps через « · »', () => {
    const errors: FieldError[] = [
      { field: 'steps', error: 'Шаг 3 пропал из данных — сначала «снять», не убирать так' },
      { field: 'steps', error: 'Шаг 5 пропал из данных — сначала «снять», не убирать так' },
    ];
    expect(missingStepsError(errors)).toBe(
      'Шаг 3 пропал из данных — сначала «снять», не убирать так · '
      + 'Шаг 5 пропал из данных — сначала «снять», не убирать так',
    );
  });
  it('нет ошибок поля steps — null', () => {
    expect(missingStepsError([{ field: 'title', error: 'x' }])).toBeNull();
  });
});

describe('форматирование', () => {
  it('shortControlText: дата и время, дата без времени, пусто', () => {
    expect(shortControlText('2026-09-08 09:30')).toBe('08.09 09:30');
    expect(shortControlText('2026-09-08')).toBe('08.09');
    expect(shortControlText(null)).toBe('—');
  });
  it('deadlineText: полная дата или «срок не вычислен»', () => {
    expect(deadlineText('2026-09-08 09:30')).toBe('срок: 08.09.2026');
    expect(deadlineText(null)).toBe('срок не вычислен');
  });
  it('progressText: «2/5» → «2 из 5»', () => {
    expect(progressText('2/5')).toBe('2 из 5');
    expect(progressText(null)).toBeNull();
  });
  it('groupModeLabel: seq/par', () => {
    expect(groupModeLabel('seq')).toBe('подшаги по очереди');
    expect(groupModeLabel('par')).toBe('подшаги в любом порядке');
    expect(groupModeLabel(null)).toBe('подшаги в любом порядке');
  });
  it('historyLine: по-русски, с причиной', () => {
    expect(historyLine({ step_id: 1, step_title: 'Позвонить', date: '2026-09-08', event: 'done', reason: null }))
      .toBe('2026-09-08 Позвонить — сделан');
    expect(historyLine({ step_id: 1, step_title: 'Позвонить', date: '2026-09-08', event: 'not_done', reason: 'занят' }))
      .toBe('2026-09-08 Позвонить — не сделан: занят');
  });
});
