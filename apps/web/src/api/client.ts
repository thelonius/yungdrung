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

// --- задачи (B1, §2.1 SLICE2_SPEC.md) -------------------------------------
export type FieldError = components['schemas']['FieldError'];
export type FieldWarning = components['schemas']['FieldWarning'];
export type OkResult = components['schemas']['OkResult'];
export type StepIn = components['schemas']['StepIn'];
export type StepEditIn = components['schemas']['StepEditIn'];
export type TaskIn = components['schemas']['TaskIn'];
export type TaskEditIn = components['schemas']['TaskEditIn'];
export type QuickIn = components['schemas']['QuickIn'];
export type CancelIn = components['schemas']['CancelIn'];
export type PlanIn = components['schemas']['PlanIn'];
export type PlannedStep = components['schemas']['PlannedStep'];
export type PlanResult = components['schemas']['PlanResult'];
export type LogEntry = components['schemas']['LogEntry'];
export type CardStep = components['schemas']['CardStep'];
export type TaskCard = components['schemas']['TaskCard'];
export type TaskSaveResult = components['schemas']['TaskSaveResult'];
export type CloseResult = components['schemas']['CloseResult'];
export type TaskDeleteResult = components['schemas']['TaskDeleteResult'];
export type TaskRef = components['schemas']['TaskRef'];

// --- база знаний в форме (обёртки над engine.cmd_kb_*, Р5) ----------------
export type Hypothesis = components['schemas']['Hypothesis'];
export type KbScanIn = components['schemas']['KbScanIn'];
export type KbScanResult = components['schemas']['KbScanResult'];
export type KbRejectIn = components['schemas']['KbRejectIn'];
export type KbConfirmIn = components['schemas']['KbConfirmIn'];
export type KbConfirmResult = components['schemas']['KbConfirmResult'];

// --- шаблоны и повторения (B2, §2.2) --------------------------------------
export type TemplateStepIn = components['schemas']['TemplateStepIn'];
export type RuleIn = components['schemas']['RuleIn'];
export type TemplateIn = components['schemas']['TemplateIn'];
export type TemplateStep = components['schemas']['TemplateStep'];
export type RecurrenceView = components['schemas']['RecurrenceView'];
export type TemplateCard = components['schemas']['TemplateCard'];
export type TemplateList = components['schemas']['TemplateList'];
export type PreviewRow = components['schemas']['PreviewRow'];
export type TemplatePreviewIn = components['schemas']['TemplatePreviewIn'];
export type TemplatePreview = components['schemas']['TemplatePreview'];
export type InstantiateIn = components['schemas']['InstantiateIn'];
export type FromTemplateResult = components['schemas']['FromTemplateResult'];
export type FromTaskIn = components['schemas']['FromTaskIn'];
export type TemplateDeleteResult = components['schemas']['TemplateDeleteResult'];
export type RuleParseResult = components['schemas']['RuleParseResult'];
export type RulePreviewIn = components['schemas']['RulePreviewIn'];
export type RuleDate = components['schemas']['RuleDate'];
export type RulePreviewResult = components['schemas']['RulePreviewResult'];

// --- вложения (B3, §2.3). `AttachmentListResult` — не путать с React-
// компонентом `AttachmentList` из features/attachments: тот описывает виджет,
// это — форма ответа `GET .../attachments`. ------------------------------
export type AttachmentInfo = components['schemas']['AttachmentInfo'];
export type AttachmentListResult = components['schemas']['AttachmentList'];
export type AttachResult = components['schemas']['AttachResult'];
export type AttachmentDeleteResult = components['schemas']['AttachmentDeleteResult'];

/** Тело ошибки по контракту: список `{field, error}` (CONTRACT.md). */
export type ApiErrors = { ok: false; errors: FieldError[] };

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
