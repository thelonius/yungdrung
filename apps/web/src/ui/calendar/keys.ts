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
  code: string;
  keys: string[];
  label: string;
  hint: string;
  from: (today: Date) => Date;
};

export const БЫСТРЫЕ: Быстрая[] = [
  { code: 'KeyS', keys: ['s', 'ы'], label: 's', hint: 'сегодня', from: (t) => t },
  { code: 'KeyZ', keys: ['z', 'я'], label: 'z', hint: 'завтра', from: (t) => addDays(t, 1) },
  { code: 'KeyP', keys: ['p', 'з'], label: 'p', hint: 'понедельник', from: nextMonday },
  { code: 'KeyW', keys: ['w', 'ц'], label: 'w', hint: 'через неделю', from: (t) => addDays(t, 7) },
];

export function быстраяПоСобытию(e: { code?: string; key: string }): Быстрая | undefined {
  return БЫСТРЫЕ.find((б) => (e.code ? б.code === e.code : б.keys.includes(e.key.toLowerCase())));
}
