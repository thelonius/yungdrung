// Дерево шагов формы «Новая задача» — чистые функции без React и без сети
// (SLICE2_SPEC.md §5.4), проверяются напрямую в model.test.ts (как
// `features/feed/model.ts`). Лист — `mode: null, steps: []`; группа —
// `mode` задан, `steps` непустой. Одна глубина: подшаг сам группой стать не
// может (кнопки «Подшаги» у него в `StepsEditor` просто нет), поэтому
// операции над подшагами не рекурсивны — этого не требуется.
import type { components } from '@/api/schema';
import type { FieldError, PlanIn, PlannedStep, StepEditIn, StepIn, TaskIn } from '@/api/client';

export type WhenResult = components['schemas']['WhenResult'];

export type StepMode = 'par' | 'seq';

export type StepDraft = {
  /** Локальный id для React-ключей и фокуса — на сервер не уходит. */
  key: string;
  title: string;
  /** Сырой ввод даты контроля; пусто у группы. */
  controlDate: string;
  /** Последний разбор `controlDate` через `onParsed` `DateField`; по нему, а
   * не по сырому тексту, перезапускается `plan` (§5.4 п.4: «без своего
   * таймера» — `DateField` уже отладил разбор сам, второй дебаунс не нужен). */
  parsed: WhenResult | null;
  mode: StepMode | null;
  steps: StepDraft[];
};

let seq = 0;
export function newStepKey(): string {
  seq += 1;
  return `s${seq}`;
}

export function newLeaf(): StepDraft {
  return { key: newStepKey(), title: '', controlDate: '', parsed: null, mode: null, steps: [] };
}

export function isGroup(s: StepDraft): boolean {
  return s.mode !== null;
}

/** «Подшаги»: своя дата группе не нужна (контроль у подшагов), режим по
 * умолчанию `par` («в любом порядке»), сразу один пустой подшаг — так же,
 * как `makeGroup` в `static/app.js`. */
export function makeGroup(s: StepDraft): StepDraft {
  return { ...s, controlDate: '', parsed: null, mode: 'par', steps: [newLeaf()] };
}

/** Убрали последний подшаг — группа снова обычный лист. */
export function ungroup(s: StepDraft): StepDraft {
  return { ...s, mode: null, steps: [] };
}

// --- операции над деревом верхнего уровня и подшагами ---------------------

export function updateTop(steps: StepDraft[], key: string, patch: Partial<StepDraft>): StepDraft[] {
  return steps.map((s) => (s.key === key ? { ...s, ...patch } : s));
}

export function updateSub(steps: StepDraft[], groupKey: string, childKey: string, patch: Partial<StepDraft>): StepDraft[] {
  return steps.map((s) => (s.key !== groupKey ? s : {
    ...s,
    steps: s.steps.map((c) => (c.key === childKey ? { ...c, ...patch } : c)),
  }));
}

export function insertTopAfter(steps: StepDraft[], afterKey: string, item: StepDraft): StepDraft[] {
  const i = steps.findIndex((s) => s.key === afterKey);
  if (i < 0) return [...steps, item];
  return [...steps.slice(0, i + 1), item, ...steps.slice(i + 1)];
}

export function insertSubAfter(steps: StepDraft[], groupKey: string, afterChildKey: string | null, item: StepDraft): StepDraft[] {
  return steps.map((s) => {
    if (s.key !== groupKey) return s;
    if (afterChildKey === null) return { ...s, steps: [...s.steps, item] };
    const i = s.steps.findIndex((c) => c.key === afterChildKey);
    if (i < 0) return { ...s, steps: [...s.steps, item] };
    return { ...s, steps: [...s.steps.slice(0, i + 1), item, ...s.steps.slice(i + 1)] };
  });
}

/** Убрать шаг верхнего уровня. Вызывающий обязан не звать это на единственном
 * шаге — кнопка удаления скрыта в `StepsEditor` (`static/app.js::renumber`). */
export function removeTop(steps: StepDraft[], key: string): StepDraft[] {
  return steps.filter((s) => s.key !== key);
}

/** Убрать подшаг; опустевшая группа возвращается в лист (`ungroup`). */
export function removeSub(steps: StepDraft[], groupKey: string, childKey: string): StepDraft[] {
  return steps.map((s) => {
    if (s.key !== groupKey) return s;
    const rest = s.steps.filter((c) => c.key !== childKey);
    return rest.length ? { ...s, steps: rest } : ungroup(s);
  });
}

// --- сбор запроса -----------------------------------------------------

function toStepIn(s: StepDraft): StepIn {
  if (isGroup(s)) return { title: s.title, mode: s.mode, steps: s.steps.map(toStepIn) };
  return { title: s.title, control_date: s.controlDate.trim() || null, steps: [] };
}

/** Лист `{title, control_date}`, группа `{title, mode, steps}`; `tags` через
 * запятую; `start_date` не отправляется — дефолт ядра (§5.4 п.5). */
export function toTaskIn(title: string, tagsText: string, body: string, steps: StepDraft[]): TaskIn {
  return {
    title,
    tags: tagsText.split(',').map((t) => t.trim()).filter(Boolean),
    body,
    steps: steps.map(toStepIn),
  };
}

function toStepEditIn(s: StepDraft): StepEditIn {
  const base = isGroup(s)
    ? { title: s.title, mode: s.mode, steps: s.steps.map(toStepEditIn) }
    : { title: s.title, control_date: s.controlDate.trim() || null, steps: [] };
  return { ...base, id: null };
}

/** `PlanIn` для формы создания: шагов ещё нет в сторе, `task_id: null`,
 * все `id` подшагов тоже `null` — ядро не подставляет сохранённые даты. */
export function toPlanIn(steps: StepDraft[]): PlanIn {
  return { task_id: null, steps: steps.map(toStepEditIn) };
}

// --- сопоставление путей: свой узел ↔ ответ `plan`/`create` ---------------

/** `steps[1].steps[0]` из ошибки поля → те же индексы в дереве черновика и
 * в `PlanResult.steps` (сервер отражает форму запроса 1:1, Р7 SLICE2_SPEC.md:
 * `to_planned` строит дерево в том же порядке, что вход). */
export function plannedAt(nodes: PlannedStep[], indices: number[]): PlannedStep | undefined {
  let cur: PlannedStep | undefined;
  let arr: PlannedStep[] | undefined = nodes;
  for (const i of indices) {
    cur = arr?.[i];
    if (!cur) return undefined;
    arr = cur.steps;
  }
  return cur;
}

/** Путь поля в скобочной форме (`api/errors.py::bracket_path`) по индексам
 * узла — обратное к `fieldPath.parse`. Не в `api/fieldPath.ts`: тот
 * разбирает путь, пришедший от сервера, а здесь путь собирается заранее по
 * известной форме дерева, чтобы найти ошибку под конкретным полем. */
export function stepFieldPath(indices: number[], leaf: string): string {
  const prefix = indices.map((i) => `steps[${i}]`).join('.');
  return prefix ? `${prefix}.${leaf}` : leaf;
}

/** Сигнатура для перезапуска `plan`: структура дерева (число шагов, режимы
 * групп) и то, на чём сошёлся разбор дат — набор текста в поле до того, как
 * `DateField` его разобрал, сигнатуру не меняет. */
export function planSignature(steps: StepDraft[]): string {
  const walk = (s: StepDraft): unknown => (
    isGroup(s) ? { m: s.mode, c: s.steps.map(walk) } : { p: s.parsed }
  );
  return JSON.stringify(steps.map(walk));
}

/** Первая ошибка поля из списка — для фокуса после неуспешного создания.
 * Порядок: имя задачи, затем шаги в порядке обхода дерева (то же самое,
 * что «первое проблемное поле» в `static/app.js::showErrors`). */
export function firstErrorField(errors: FieldError[]): string | null {
  return errors.find((e) => e.field !== null)?.field ?? null;
}
