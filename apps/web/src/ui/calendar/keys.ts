// Быстрые клавиши пикера и их подписи. Вынесены отдельно, потому что оба
// прототипа (свой и react-day-picker) обязаны иметь одинаковые: иначе
// сравнение мерило бы обвязку, а не сетку.
//
// Буквы латинские — как везде в приложении (d, n, t, f в ленте и карточке):
// на русской раскладке они всё равно приходят в `e.key` кириллицей, поэтому
// принимаем оба написания.
import { addDays, nextMonday } from './days';

export type Быстрая = {
  keys: string[];
  label: string;
  hint: string;
  from: (today: Date) => Date;
};

export const БЫСТРЫЕ: Быстрая[] = [
  { keys: ['s', 'ы'], label: 's', hint: 'сегодня', from: (t) => t },
  { keys: ['z', 'я'], label: 'z', hint: 'завтра', from: (t) => addDays(t, 1) },
  { keys: ['p', 'з'], label: 'p', hint: 'понедельник', from: nextMonday },
  { keys: ['w', 'ц'], label: 'w', hint: 'через неделю', from: (t) => addDays(t, 7) },
];

export function быстраяПоКлавише(key: string): Быстрая | undefined {
  const k = key.toLowerCase();
  return БЫСТРЫЕ.find((б) => б.keys.includes(k));
}
