// Дни месяца без библиотек: названия месяцев и дней недели даёт Intl, он в
// браузере уже есть. Всё здесь — про раскладку сетки. Смысловые признаки
// («выходной у заказчика», «просрочено») сюда не переезжают: их считает ядро,
// и почему именно оно — записано в core/templates.py:147.

/** Дата → «2026-09-18». `toISOString` не годится: он переводит в UTC, и
 *  18 сентября в московском поясе превращается в 17-е. */
export function iso(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** «2026-09-18» и «2026-09-18T09:30» → дата. Время отбрасывается: сетка
 *  работает по дням, час живёт в текстовом поле рядом. */
export function fromIso(s: string | null | undefined): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s ?? '');
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}

export function addDays(d: Date, n: number): Date {
  const r = new Date(d);
  r.setDate(r.getDate() + n);
  return r;
}

export function daysInMonth(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
}

/** Месяц прибавляется с прижатием к концу: 31 января плюс месяц — 28 февраля,
 *  а не 3 марта, как вышло бы у голого `setMonth`. Правило то же, что у
 *  повторений в domain/recurrence.py (клампинг «31 февраля»). */
export function addMonths(d: Date, n: number): Date {
  const r = new Date(d.getFullYear(), d.getMonth() + n, 1);
  r.setDate(Math.min(d.getDate(), daysInMonth(r)));
  return r;
}

/** День недели по ISO: 1 — понедельник, 7 — воскресенье. У `getDay`
 *  воскресенье нулевое, и арифметика начала недели с ним путается. */
export function isoDay(d: Date): number {
  return d.getDay() === 0 ? 7 : d.getDay();
}

export type Week = { firstDay: number; weekend: number[] };

/** С какого дня начинается неделя и какие дни календарно выходные — из
 *  локали: для ru это понедельник и [6, 7]. `getWeekInfo` есть не везде,
 *  поэтому запасной вариант зашит. Настоящие выходные заказчика знает только
 *  ядро — эти придут отдельным полем, когда появится маршрут календаря. */
export function weekInfo(locale = 'ru'): Week {
  type Расширенная = Intl.Locale & { getWeekInfo?: () => Week; weekInfo?: Week };
  const l = new Intl.Locale(locale) as Расширенная;
  const w = l.getWeekInfo?.() ?? l.weekInfo;
  return { firstDay: w?.firstDay ?? 1, weekend: w?.weekend ?? [6, 7] };
}

/** Начало недели, в которую попал день (клавиша Home). */
export function weekStart(d: Date, firstDay: number): Date {
  return addDays(d, -((isoDay(d) - firstDay + 7) % 7));
}

/** Шесть недель подряд от начала той недели, куда попало первое число.
 *  Всегда шесть, а не пять-шесть по месяцу: иначе сетка прыгает по высоте
 *  при листании и глаз каждый раз заново ищет нужную строку. */
export function monthGrid(month: Date, firstDay: number): Date[] {
  const первое = new Date(month.getFullYear(), month.getMonth(), 1);
  const начало = weekStart(первое, firstDay);
  return Array.from({ length: 42 }, (_, i) => addDays(начало, i));
}

/** Ближайший понедельник после сегодня — та же семантика, что у «пн» в
 *  разборе ядра (domain/ru_dates.py: ближайший такой день ПОСЛЕ сегодня). */
export function nextMonday(today: Date): Date {
  return addDays(today, ((8 - isoDay(today)) % 7) || 7);
}

const МЕСЯЦ_ГОД = new Intl.DateTimeFormat('ru', { month: 'long', year: 'numeric' });
const ДЕНЬ_ПОЛНЫЙ = new Intl.DateTimeFormat('ru', {
  weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
});
const ДЕНЬ_КОРОТКО = new Intl.DateTimeFormat('ru', { weekday: 'short' });
const ДЕНЬ_ДЛИННО = new Intl.DateTimeFormat('ru', { weekday: 'long' });

/** «Сентябрь 2026»: Intl дописывает «г.», в заголовке сетки оно лишнее. */
export function captionOf(month: Date): string {
  const s = МЕСЯЦ_ГОД.format(month).replace(/\s*г\.$/, '');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** «пятница, 18 сентября 2026 г.» — подпись дня для чтения и для читалки. */
export function dayLabel(d: Date): string {
  return ДЕНЬ_ПОЛНЫЙ.format(d);
}

/** Шапка сетки в порядке, с которого начинается неделя. */
export function weekdayNames(firstDay: number): { short: string; long: string }[] {
  const понедельник = new Date(2026, 8, 14);
  return Array.from({ length: 7 }, (_, i) => {
    const d = addDays(понедельник, (firstDay - 1 + i + 7) % 7);
    return { short: ДЕНЬ_КОРОТКО.format(d), long: ДЕНЬ_ДЛИННО.format(d) };
  });
}
