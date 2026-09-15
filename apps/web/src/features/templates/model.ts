// Чистые функции шаблонов: без React и без сети, чтобы проверяться напрямую
// (model.test.ts) — тот же приём, что у `features/feed/model.ts`.
import type { FieldError, RecurrenceView, RuleIn, TemplateCard } from '@/api/client';

/**
 * Промежутки → сдвиги от старта накопленной суммой (CLAUDE.md, «сдвиги шагов
 * шаблона вводятся приращениями, хранятся от старта»; перенесено из
 * `static/templates.js::ntСобратьШаги`). Нечисловой промежуток сумму ломает:
 * с этого места и дальше сдвиг — тот же неразобранный текст, что и у
 * сломавшегося шага, а не собственное значение следующего поля. Показывать
 * частично досчитанные даты после опечатки хуже, чем оставить всё как есть —
 * человек чинит одно поле, а не гадает, где именно разошлось.
 */
export function sumOffsets(raw: (number | string)[]): (number | string)[] {
  let acc = 0;
  let broken: number | string | null = null;
  return raw.map((v) => {
    if (broken !== null) return broken;
    const s = String(v).trim();
    if (!/^[+-]?\d+$/.test(s)) {
      broken = v;
      return v;
    }
    acc += Number(s);
    return acc;
  });
}

/**
 * Обратное к `sumOffsets` — раскладка сохранённых сдвигов на промежутки при
 * открытии формы правки (Р4 SLICE2_SPEC.md): первый шаг несёт «дн. от
 * старта» как есть, каждый следующий — разницу с предыдущим.
 */
export function decomposeOffsets(offsets: number[]): number[] {
  return offsets.map((o, i) => (i === 0 ? o : o - offsets[i - 1]));
}

/** Русское склонение по числу (1 — «one», 2–4 — «few», остальное — «many»),
 * с учётом 11–14 (всегда «many»), как того требует язык, а не `n < 5`
 * старой `templates.js` (там 21 шаг ошибочно считался «шагов»). */
export function pluralRu(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

export function stepsWord(n: number): string {
  return pluralRu(n, 'шаг', 'шага', 'шагов');
}

export function filesWord(n: number): string {
  return pluralRu(n, 'файл', 'файла', 'файлов');
}

/** Строка метаданных карточки шаблона: «3 шага · файлов: 2 · тег1, тег2». */
export function templateMeta(t: Pick<TemplateCard, 'steps_count' | 'attachments_count' | 'tags'>): string {
  const parts = [`${t.steps_count} ${stepsWord(t.steps_count)}`];
  if (t.attachments_count) parts.push(`файлов: ${t.attachments_count}`);
  if (t.tags.length) parts.push(t.tags.join(', '));
  return parts.join(' · ');
}

/** Подпись повторения на карточке: «↻ {description}» или «без повторения». */
export function recurrenceSummary(recurrence: RecurrenceView | null): string {
  return recurrence ? `↻ ${recurrence.description}` : 'без повторения';
}

/**
 * Тело `recurrence` для `PUT /templates/{name}` (Р4): «то, что пришло в
 * `GET`, нетронутое» — сохранённое повторение переживает правку остальных
 * полей шаблона, PUT замещает объект целиком. `RecurrenceView` — надмножество
 * `RuleIn` (лишнее поле `description` серверу известно и игнорируется,
 * `RuleIn.extra="ignore"` — см. комментарий схемы), поэтому объект уходит как
 * есть, без пересборки по полям.
 */
export function recurrencePayload(recurrence: RecurrenceView | null | undefined): RuleIn | null {
  return recurrence ?? null;
}

/** Числа месяца из строки через запятую («1, 15, -1») в список для
 * `bymonthday`; мусор и пустые куски отбрасываются — поле необязательное,
 * а не проверка формы (проверяет ядро при сохранении). */
export function parseMonthdayList(text: string): number[] {
  return text
    .split(',')
    .map((s) => s.trim())
    .filter((s) => /^-?\d+$/.test(s))
    .map(Number);
}

export function monthdayListText(nums: number[] | null | undefined): string {
  return (nums ?? []).join(', ');
}

/** Порядок дней недели формы — понедельник первым (`byweekday`: пн=0, как
 * у `date.weekday()` в ядре, решение §5.6 SLICE2_SPEC.md). */
export const WEEKDAYS: { value: number; label: string }[] = [
  { value: 0, label: 'пн' },
  { value: 1, label: 'вт' },
  { value: 2, label: 'ср' },
  { value: 3, label: 'чт' },
  { value: 4, label: 'пт' },
  { value: 5, label: 'сб' },
  { value: 6, label: 'вс' },
];

export function toggleWeekday(days: number[], value: number): number[] {
  return days.includes(value) ? days.filter((d) => d !== value) : [...days, value].sort((a, b) => a - b);
}

/**
 * Пустое правило для новой настройки повторения: `freq`/`interval` получают
 * рабочие дефолты (виджет всегда должен показывать что-то валидное), скрытые
 * поля — нейтральные значения, которые и так уходят как есть (§5.6:
 * «хранятся в состоянии как пришли и возвращаются в `RuleIn` как есть»).
 */
export function emptyRule(): RuleIn {
  return {
    anchor: null,
    freq: 'weekly',
    interval: 1,
    byweekday: [],
    bymonthday: [],
    bysetpos: [],
    bymonth: [],
    holiday_shift: 'none',
    lead_days: 0,
    until: null,
    paused: false,
  };
}

/**
 * Загрузка формы правила: сохранённое (`RecurrenceView` из карточки) или
 * пустое. Разница с `recurrencePayload` — назначение: здесь результат идёт в
 * состояние формы (обязательно все поля определены, виджетам нечего читать
 * из `undefined`), там — в тело запроса как получено.
 */
export function ruleFromView(recurrence: RecurrenceView | null): RuleIn {
  if (!recurrence) return emptyRule();
  const { anchor, freq, interval, byweekday, bymonthday, bysetpos, bymonth,
    holiday_shift, lead_days, until, paused } = recurrence;
  return { anchor, freq, interval, byweekday, bymonthday, bysetpos, bymonth, holiday_shift, lead_days, until, paused };
}

/**
 * Ошибки шагов формы (`steps[1].offset_days`, Р1 SLICE2_SPEC.md — скобочный
 * путь) разложены по индексу шага и имени поля, чтобы `TemplateForm` не
 * разбирала путь сама в JSX. Название шага сюда не попадает никогда: живой
 * предпросмотр шлёт заглушку вместо пустого названия (см. `TemplateForm`,
 * тот же приём, что был в `static/templates.js::ntОбновитьДаты`), а на
 * сохранении пустое название — обычная ошибка поля, просто без своей строки
 * здесь не нужна (форма покажет `title` тем же способом, что `offset_days`).
 */
export function stepErrors(errors: FieldError[]): Record<number, Record<string, string>> {
  const out: Record<number, Record<string, string>> = {};
  for (const e of errors) {
    const m = e.field && /^steps\[(\d+)]\.(\w+)$/.exec(e.field);
    if (!m) continue;
    const idx = Number(m[1]);
    out[idx] = { ...(out[idx] ?? {}), [m[2]]: e.error };
  }
  return out;
}

/**
 * Очередь файлов нового шаблона грузится **после** записи, не до (§5.6:
 * «файлы нового шаблона копятся в `PendingFiles`... после успеха уходят
 * через `uploadPending`»): пока шаблона нет, `Owner` для них не существует.
 * Порядок вызовов — то, что проверяет тест; сама функция дженерик над
 * `save`/`upload`, чтобы не тянуть в model.ts зависимость на API и React Query.
 */
export async function saveThenUpload<TSaved>(
  save: () => Promise<TSaved>,
  upload: (saved: TSaved) => Promise<void>,
): Promise<TSaved> {
  const saved = await save();
  await upload(saved);
  return saved;
}
