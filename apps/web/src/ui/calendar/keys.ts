// Быстрые клавиши пикера. Общие для обоих прототипов: иначе сравнение мерило
// бы обвязку, а не сетку.
//
// Буквы русские по смыслу: «с» — сегодня, «з» — завтра, «п» — понедельник,
// «н» — плюс неделя, «м» — плюс месяц. Опознаётся при этом место на
// клавиатуре (`event.code`), поэтому переключать раскладку не нужно: та же
// кнопка в латинской отдаёт c, p, g, y, v. На легенде подписаны обе крышки.
//
// Ни одна из пяти не пересекается с клавишами ленты и карточки (d, n, t, f,
// a, u, e, r, j, k) — проверено по их кодам.
import { addDays, addMonths, nextMonday } from './days';

export type Быстрая = {
  /** Как эту кнопку зовёт `useHotkeys` — латинской буквой того же места. */
  hotkey: string;
  /** Что на ней написано в русской раскладке и в латинской. */
  ru: string;
  en: string;
  hint: string;
  /** Якорь считается от сегодня, шаг — от того места, где стоит курсор. */
  шаг: boolean;
  from: (today: Date, cursor: Date) => Date;
};

export const БЫСТРЫЕ: Быстрая[] = [
  { hotkey: 'c', ru: 'с', en: 'c', hint: 'сегодня', шаг: false, from: (t) => t },
  { hotkey: 'p', ru: 'з', en: 'p', hint: 'завтра', шаг: false, from: (t) => addDays(t, 1) },
  { hotkey: 'g', ru: 'п', en: 'g', hint: 'понедельник', шаг: false, from: (t) => nextMonday(t) },
  { hotkey: 'y', ru: 'н', en: 'y', hint: '+неделя', шаг: true, from: (_, c) => addDays(c, 7) },
  { hotkey: 'v', ru: 'м', en: 'v', hint: '+месяц', шаг: true, from: (_, c) => addMonths(c, 1) },
];

export function быстраяПоКнопке(hotkey: string): Быстрая | undefined {
  return БЫСТРЫЕ.find((б) => б.hotkey === hotkey.toLowerCase());
}

/** Строки для `useHotkeys`: он сравнивает `event.code`, то есть место на
 *  клавиатуре — это то же правило, по которому работают клавиши ленты. */
export const СТРОКА_БЫСТРЫХ = БЫСТРЫЕ.map((б) => б.hotkey).join(', ');
export const СТРОКА_БЫСТРЫХ_ALT = БЫСТРЫЕ.map((б) => `alt+${б.hotkey}`).join(', ');
