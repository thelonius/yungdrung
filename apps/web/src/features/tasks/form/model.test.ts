import { describe, expect, it } from 'vitest';
import {
  insertSubAfter, insertTopAfter, isGroup, makeGroup, newLeaf, plannedAt, planSignature,
  removeSub, removeTop, stepFieldPath, toPlanIn, toTaskIn, ungroup, updateSub, updateTop,
} from './model';
import type { StepDraft } from './model';

function leaf(title: string, controlDate = ''): StepDraft {
  return { ...newLeaf(), title, controlDate };
}

describe('дерево шагов формы', () => {
  it('toTaskIn собирает лист {title, control_date} и группу {title, mode, steps}', () => {
    const group = makeGroup(leaf('Подготовить'));
    const in_ = toTaskIn('Переезд', 'дом, быт', 'заметка', [leaf('Позвонить', 'завтра'), group]);
    expect(in_).toEqual({
      title: 'Переезд',
      tags: ['дом', 'быт'],
      body: 'заметка',
      steps: [
        { title: 'Позвонить', control_date: 'завтра', steps: [] },
        { title: 'Подготовить', mode: 'par', steps: [{ title: '', control_date: null, steps: [] }] },
      ],
    });
  });

  it('пустая дата контроля уходит как null, а не пустая строка', () => {
    const in_ = toTaskIn('Задача', '', '', [leaf('Шаг', '   ')]);
    expect(in_.steps[0]).toEqual({ title: 'Шаг', control_date: null, steps: [] });
  });

  it('tags разбираются по запятой без пустых элементов', () => {
    const in_ = toTaskIn('Задача', ' финансы,  , отчётность ,', '', [leaf('Шаг')]);
    expect(in_.tags).toEqual(['финансы', 'отчётность']);
  });

  it('toPlanIn ставит id: null всем узлам — шагов ещё нет в сторе', () => {
    const group = makeGroup(leaf('a'));
    const plan = toPlanIn([leaf('x'), group]);
    expect(plan.task_id).toBeNull();
    expect(plan.steps[0]).toEqual({ id: null, title: 'x', control_date: null, steps: [] });
    expect(plan.steps[1]).toMatchObject({ id: null, title: 'a', mode: 'par' });
    expect(plan.steps[1].steps[0]).toEqual({ id: null, title: '', control_date: null, steps: [] });
  });

  it('makeGroup/ungroup переключают лист и группу', () => {
    const l = leaf('Собрать документы', 'пн');
    const g = makeGroup(l);
    expect(isGroup(g)).toBe(true);
    expect(g.mode).toBe('par');
    expect(g.controlDate).toBe(''); // своя дата у группы не нужна
    expect(g.steps).toHaveLength(1);
    const back = ungroup(g);
    expect(isGroup(back)).toBe(false);
    expect(back.steps).toEqual([]);
  });

  it('insertTopAfter вставляет после конкретного шага, а не в конец', () => {
    const a = leaf('a'); const b = leaf('b'); const c = leaf('c');
    const result = insertTopAfter([a, b], a.key, c);
    expect(result.map((s) => s.title)).toEqual(['a', 'c', 'b']);
  });

  it('removeTop убирает ровно один шаг по ключу', () => {
    const a = leaf('a'); const b = leaf('b');
    expect(removeTop([a, b], a.key).map((s) => s.title)).toEqual(['b']);
  });

  it('удаление последнего подшага возвращает группу в лист', () => {
    const child = newLeaf();
    const group = { ...makeGroup(leaf('Группа')), steps: [child] };
    const result = removeSub([group], group.key, child.key);
    expect(isGroup(result[0])).toBe(false);
  });

  it('удаление одного из нескольких подшагов группу не разваливает', () => {
    const first = newLeaf(); const second = newLeaf();
    const group = { ...makeGroup(leaf('Группа')), steps: [first, second] };
    const result = removeSub([group], group.key, first.key);
    expect(isGroup(result[0])).toBe(true);
    expect(result[0].steps).toHaveLength(1);
  });

  it('insertSubAfter вставляет подшаг после указанного, addAfter=null — в конец', () => {
    const child = newLeaf();
    const group = { ...makeGroup(leaf('Группа')), steps: [child] };
    const added = newLeaf();
    const result = insertSubAfter([group], group.key, child.key, added);
    expect(result[0].steps.map((s) => s.key)).toEqual([child.key, added.key]);
  });

  it('updateTop/updateSub меняют поле точечно, не трогая остальное дерево', () => {
    const a = leaf('a'); const b = leaf('b');
    const result = updateTop([a, b], b.key, { title: 'b2' });
    expect(result[0].title).toBe('a');
    expect(result[1].title).toBe('b2');

    const child = newLeaf();
    const group = { ...makeGroup(leaf('g')), steps: [child] };
    const result2 = updateSub([group], group.key, child.key, { title: 'подшаг' });
    expect(result2[0].steps[0].title).toBe('подшаг');
  });
});

describe('plannedAt / stepFieldPath — сопоставление с ответом сервера', () => {
  it('находит вложенный узел по индексам', () => {
    const nodes = [
      { path: 'steps.0', id: null, mode: null, start: '2026-09-16', control: null, explicit_start: false, steps: [] },
      {
        path: 'steps.1', id: null, mode: 'par', start: null, control: null, explicit_start: false,
        steps: [{ path: 'steps.1.steps.0', id: null, mode: null, start: '2026-09-17', control: null, explicit_start: false, steps: [] }],
      },
    ];
    expect(plannedAt(nodes, [0])?.start).toBe('2026-09-16');
    expect(plannedAt(nodes, [1, 0])?.start).toBe('2026-09-17');
    expect(plannedAt(nodes, [5])).toBeUndefined();
  });

  it('stepFieldPath строит тот же скобочный путь, что отдаёт api/errors.py::bracket_path', () => {
    expect(stepFieldPath([], 'title')).toBe('title');
    expect(stepFieldPath([0], 'control_date')).toBe('steps[0].control_date');
    expect(stepFieldPath([1, 0], 'title')).toBe('steps[1].steps[0].title');
    expect(stepFieldPath([1], 'mode')).toBe('steps[1].mode');
  });
});

describe('planSignature', () => {
  it('не меняется от одного факта набора текста — только от разбора и структуры', () => {
    const a = leaf('a');
    const before = planSignature([a]);
    const typedButNotParsed = { ...a, controlDate: 'завтра' }; // parsed ещё null
    expect(planSignature([typedButNotParsed])).toBe(before);
  });

  it('меняется, когда DateField разобрал дату (onParsed)', () => {
    const a = leaf('a');
    const before = planSignature([a]);
    const parsed = { ...a, parsed: { ok: true, date: '2026-09-16', label: 'завтра', past: false } };
    expect(planSignature([parsed])).not.toBe(before);
  });

  it('меняется при смене режима группы и при добавлении подшага', () => {
    const group = makeGroup(leaf('g'));
    const seq = { ...group, mode: 'seq' as const };
    expect(planSignature([group])).not.toBe(planSignature([seq]));

    const withExtra = { ...group, steps: [...group.steps, newLeaf()] };
    expect(planSignature([group])).not.toBe(planSignature([withExtra]));
  });
});
