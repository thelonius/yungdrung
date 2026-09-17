// Быстрые клавиши пикера и их подписи. Вынесены отдельно, потому что оба
// прототипа (свой и react-day-picker) обязаны иметь одинаковые: иначе
// сравнение мерило бы обвязку, а не сетку.
//
// Клавиша опознаётся по `code`, то есть по месту на клавиатуре, а не по
// букве: на русской раскладке та же кнопка отдаёт «ы», «я», «з», «ц», и
// список букв пришлось бы держать под каждую раскладку. `key` остаётся
// запасным вариантом — у экранных клавиатур `code` бывает пустым.
//
// Буквы латинские, как везде в приложении (d, n, t, f в ленте и карточке).
import { addDays, nextMonday } from './days';

export type Быстрая = {
  /** Место на клавиатуре. По нему и опознаём: раскладку переключать не надо. */
  code: string;
  keys: string[];
  /** Что написано на кнопке в латинской раскладке и в русской. */
  label: string;
  ru: string;
  hint: string;
  from: (today: Date) => Date;
};

export const БЫСТРЫЕ: Быстрая[] = [
  { code: 'KeyS', keys: ['s', 'ы'], label: 's', ru: 'ы', hint: 'сегодня', from: (t) => t },
  { code: 'KeyZ', keys: ['z', 'я'], label: 'z', ru: 'я', hint: 'завтра', from: (t) => addDays(t, 1) },
  { code: 'KeyP', keys: ['p', 'з'], label: 'p', ru: 'з', hint: 'понедельник', from: nextMonday },
  { code: 'KeyW', keys: ['w', 'ц'], label: 'w', ru: 'ц', hint: 'через неделю', from: (t) => addDays(t, 7) },
];

export function быстраяПоСобытию(e: { code?: string; key: string }): Быстрая | undefined {
  return БЫСТРЫЕ.find((б) => (e.code ? б.code === e.code : б.keys.includes(e.key.toLowerCase())));
}

/** Строки для `useHotkeys`: он сравнивает по `event.code`, поэтому раскладка
 *  роли не играет — это то же правило, по которому работают клавиши ленты. */
export const СТРОКА_БЫСТРЫХ = БЫСТРЫЕ.map((б) => б.label).join(', ');
export const СТРОКА_БЫСТРЫХ_ALT = БЫСТРЫЕ.map((б) => `alt+${б.label}`).join(', ');
