// Чистые функции карточки задачи: дерево черновика редактирования, сборка
// тела `PUT`/`plan`, разбор ошибок по скобочному пути. Без React и без сети —
// чтобы проверяться напрямую (model.test.ts), как `features/feed/model.ts`.
import type { CardStep, FieldError, FieldWarning, LogEntry, StepEditIn } from '@/api/client';
import { parse } from '@/api/fieldPath';

/**
 * Черновик шага — тот же набор полей, что `CardStep`, но `id` может быть
 * `null` у ещё не сохранённого шага (кнопка «+ Шаг»/«+ Подшаг»), а сами
 * поля мутируются локально до «Сохранить» (map-client.md §2.2.6, п.10).
 */
export type DraftStep = Omit<CardStep, 'id' | 'steps'> & { id: number | null; steps: DraftStep[] };

/** Дерево `CardStep` из карточки как есть — редактируется поверх него. */
export function fromCard(steps: CardStep[]): DraftStep[] {
  return steps.map((s) => ({ ...s, steps: fromCard(s.steps) }));
}

export function newLeaf(): DraftStep {
  return {
    id: null, title: '', status: 'pending', start_date: null, control_date: null,
    completed_date: null, note: null, mode: null, closed: false, active: false,
    stalled: 0, state: null, row: null, actions: [], steps: [],
  };
}

/** Узел по цепочке индексов (`[1, 0]` — второй шаг, его первый подшаг). */
export function getNode(nodes: DraftStep[], indices: number[]): DraftStep | undefined {
  const [head, ...rest] = indices;
  const node = nodes[head];
  if (!node) return undefined;
  return rest.length === 0 ? node : getNode(node.steps, rest);
}

/** Неизменяемая правка узла по цепочке индексов. */
export function patchNode(nodes: DraftStep[], indices: number[], patch: Partial<DraftStep>): DraftStep[] {
  const [head, ...rest] = indices;
  return nodes.map((n, i) => {
    if (i !== head) return n;
    return rest.length === 0 ? { ...n, ...patch } : { ...n, steps: patchNode(n.steps, rest, patch) };
  });
}

/** Добавить лист в конец списка — на верхнем уровне («+ Шаг», `indices: []`)
 * или в конец детей группы («+ Подшаг», `indices` группы). */
export function addLeaf(nodes: DraftStep[], indices: number[]): DraftStep[] {
  if (indices.length === 0) return [...nodes, newLeaf()];
  const [head, ...rest] = indices;
  return nodes.map((n, i) => {
    if (i !== head) return n;
    return rest.length === 0 ? { ...n, steps: [...n.steps, newLeaf()] } : { ...n, steps: addLeaf(n.steps, rest) };
  });
}

/** «Разбить на подшаги»: лист становится группой с одним пустым подшагом,
 * своя дата уходит (у группы дат не бывает) — CLAUDE.md, «шаг — лист или
 * группа». Название и id узла остаются: это тот же шаг, сменивший роль. */
export function splitToSubsteps(node: DraftStep): DraftStep {
  return { ...node, mode: 'par', start_date: null, control_date: null, steps: [newLeaf()] };
}

/** Перестановка среди соседей одного уровня (drag-and-drop, п.9 §5.5). */
export function moveSibling(nodes: DraftStep[], parent: number[], from: number, to: number): DraftStep[] {
  if (parent.length === 0) return reorder(nodes, from, to);
  const [head, ...rest] = parent;
  return nodes.map((n, i) => (i === head ? { ...n, steps: moveSibling(n.steps, rest, from, to) } : n));
}

function reorder<T>(list: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || from >= list.length || to < 0 || to >= list.length) return list;
  const next = list.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

/** Лист → `{id, title, start_date, control_date, note}`; группа →
 * `{id, title, mode, note, steps}` (даты группы не отправляются, §5.5 п.10). */
function toEditIn(n: DraftStep): StepEditIn {
  if (n.steps.length > 0) {
    return { id: n.id, title: n.title ?? '', mode: n.mode, note: n.note, steps: n.steps.map(toEditIn) };
  }
  return {
    id: n.id, title: n.title ?? '', start_date: n.start_date, control_date: n.control_date, note: n.note, steps: [],
  };
}

export function buildEditSteps(nodes: DraftStep[]): StepEditIn[] {
  return nodes.map(toEditIn);
}

/** Только листья, в порядке обхода дерева — по ним ходит фокус `j`/`k`
 * (§5.2: «Фокус j/k … по листьям»). */
export function leafRows(nodes: DraftStep[], prefix: number[] = []): { node: DraftStep; indices: number[] }[] {
  const out: { node: DraftStep; indices: number[] }[] = [];
  nodes.forEach((n, i) => {
    const indices = [...prefix, i];
    if (n.steps.length === 0) out.push({ node: n, indices });
    else out.push(...leafRows(n.steps, indices));
  });
  return out;
}

/** Путь узла в скобочном виде — как отдаёт HTTP (Р1 SLICE2_SPEC.md),
 * пригоден для сравнения с `FieldError.field`. */
export function pathOf(indices: number[]): string {
  return indices.reduce((acc, i, k) => (k === 0 ? `steps[${i}]` : `${acc}.steps[${i}]`), '');
}

/** Ошибка на конкретное поле узла по цепочке индексов, если она там есть. */
export function errorAt(errors: FieldError[], indices: number[], leaf: string): string | undefined {
  return errors.find((e) => {
    if (e.field === null) return false;
    const p = parse(e.field);
    return p.leaf === leaf && p.indices.length === indices.length && p.indices.every((v, i) => v === indices[i]);
  })?.error;
}

/** Индексы узлов, задетых path-ошибками (`steps[…]…`) — их редакторы
 * раскрываются автоматически, чтобы ошибка была видна без клика (§5.5 п.11,
 * тест «422 steps[1].control_date раскрывает редактор второго шага»). */
export function errorPaths(errors: FieldError[]): string[] {
  const out = new Set<string>();
  for (const e of errors) {
    if (!e.field || !e.field.startsWith('steps[')) continue;
    const p = parse(e.field);
    if (p.indices.length) out.add(p.indices.join(','));
  }
  return [...out];
}

/** Ошибки поля `steps` без индекса — «шаг пропал» (Р3.1 §1.1), склеиваются
 * через « · » как в старой карточке (map-client.md §2.2.11). */
export function missingStepsError(errors: FieldError[]): string | null {
  const texts = errors.filter((e) => e.field === 'steps').map((e) => e.error);
  return texts.length ? texts.join(' · ') : null;
}

export function generalWarningsText(warnings: FieldWarning[]): string[] {
  return warnings.map((w) => w.warning);
}

const EVENT_RU: Record<string, string> = {
  done: 'сделан', not_done: 'не сделан', defer: 'перенесён', failed: 'провален',
  skipped: 'снят', reopened: 'переоткрыт', mass_defer: 'массовый перенос',
};

/** «{дата} {шаг} — {событие}: {причина}» — как в старой карточке
 * (map-client.md §2.2.12); история уже отсортирована ядром. */
export function historyLine(e: LogEntry): string {
  const event = EVENT_RU[e.event] ?? e.event;
  const step = e.step_title ?? `шаг ${e.step_id}`;
  return `${e.date} ${step} — ${event}${e.reason ? `: ${e.reason}` : ''}`;
}

export function groupModeLabel(mode: string | null): string {
  return mode === 'seq' ? 'подшаги по очереди' : 'подшаги в любом порядке';
}

/** «срок: dd.mm.yyyy» из `control_date` (полная дата — это шапка задачи, не
 * строка шага) или «срок не вычислен» (map-client.md §2.2.1). */
export function deadlineText(controlDate: string | null): string {
  if (!controlDate) return 'срок не вычислен';
  const [datePart] = controlDate.split(' ');
  const [y, m, d] = datePart.split('-');
  if (!y || !m || !d) return `срок: ${controlDate}`;
  return `срок: ${d}.${m}.${y}`;
}

/** «2/5» из ядра → «2 из 5» (map-client.md §2.2.1). */
export function progressText(progress: string | null): string | null {
  if (!progress) return null;
  const [done, total] = progress.split('/');
  return done && total ? `${done} из ${total}` : progress;
}

/** `«2026-09-08 10:00»`/`«2026-09-08»` (format_control, Р2) → `«08.09 10:00»`.
 * Разбор руками, не `Date`: формат отдаёт пробел, не `T` (Р2 SLICE2_SPEC.md,
 * `new Date('YYYY-MM-DD HH:MM')` — поведение не специфицировано в разных JS). */
export function shortControlText(text: string | null): string {
  if (!text) return '—';
  const [datePart, timePart] = text.split(' ');
  const [y, m, d] = datePart.split('-');
  if (!y || !m || !d) return text;
  return timePart ? `${d}.${m} ${timePart}` : `${d}.${m}`;
}
