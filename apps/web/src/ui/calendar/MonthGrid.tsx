// Свой календарь: сетка месяца с клавиатурой по стандарту ARIA APG (таблица
// role=grid, один день в порядке обхода Tab, остальные — стрелками).
// Карта клавиш взята из того же стандарта, что и у react-day-picker, чтобы
// прототипы отличались реализацией, а не поведением.
//
// Текстовое поле остаётся главным: сетка едет за тем, что разобрало ядро, и
// возвращает выбор строкой обратно в поле — разбор в приложении один.
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  addDays, addMonths, captionOf, dayLabel, fromIso, iso, isoDay,
  monthGrid, weekdayNames, weekInfo, weekStart,
} from './days';
import { QuickKeys } from './QuickKeys';
import styles from './MonthGrid.module.css';

type Props = {
  /** Что сейчас разобрало ядро, «2026-09-18» или с временем. */
  value: string | null;
  onPick: (date: string) => void;
  onEscape?: () => void;
  today?: Date;
  containerRef?: React.RefObject<HTMLDivElement | null>;
};

/** Куда уводит клавиша. Стрелки — по дням и неделям, с Shift — по месяцам и
 *  годам, PageUp/PageDown — месяцы, Home/End — границы недели. */
function шаг(e: React.KeyboardEvent, от: Date, firstDay: number): Date | null {
  switch (e.key) {
    case 'ArrowLeft': return e.shiftKey ? addMonths(от, -1) : addDays(от, -1);
    case 'ArrowRight': return e.shiftKey ? addMonths(от, 1) : addDays(от, 1);
    case 'ArrowUp': return e.shiftKey ? addMonths(от, -12) : addDays(от, -7);
    case 'ArrowDown': return e.shiftKey ? addMonths(от, 12) : addDays(от, 7);
    case 'PageUp': return addMonths(от, e.shiftKey ? -12 : -1);
    case 'PageDown': return addMonths(от, e.shiftKey ? 12 : 1);
    case 'Home': return weekStart(от, firstDay);
    case 'End': return addDays(weekStart(от, firstDay), 6);
    default: return null;
  }
}

export function MonthGrid({ value, onPick, onEscape, today, containerRef }: Props) {
  const week = useMemo(() => weekInfo('ru'), []);
  const сегодня = useMemo(() => today ?? new Date(), [today]);
  const выбрано = fromIso(value);
  const [фокус, setФокус] = useState<Date>(() => выбрано ?? сегодня);
  const вести = useRef(false);
  const свой = useRef<HTMLDivElement>(null);
  const коробка = containerRef ?? свой;
  const capId = useId();

  // Напечатали в поле — сетка едет туда же. Сравнение по строке, а не по
  // объекту: два разных Date на один день — это одна и та же дата.
  const дата = выбрано ? iso(выбрано) : null;
  useEffect(() => {
    const d = fromIso(дата);
    if (!d) return;
    setФокус(d);
    // Дату могли выбрать снаружи — быстрой клавишей, которую ловит обвязка.
    // Если курсор при этом стоял в сетке, он обязан переехать на новый день:
    // иначе стрелки поедут от прежнего, а подсвечен будет новый.
    if (коробка.current?.contains(document.activeElement)) вести.current = true;
  }, [дата, коробка]);

  // Фокус в DOM переносим только после клавиши. Иначе сетка отбирала бы его
  // у текстового поля на каждое нажатие, пока человек печатает дату.
  useEffect(() => {
    if (!вести.current) return;
    вести.current = false;
    коробка.current
      ?.querySelector<HTMLButtonElement>(`button[data-day="${iso(фокус)}"]`)
      ?.focus();
  }, [фокус, коробка]);

  function перейти(d: Date) {
    вести.current = true;
    setФокус(d);
  }

  /** Выбор удерживает фокус там, где он был. Внутри сетки — остаётся в
   *  сетке, иначе после первой же быстрой клавиши курсор улетал бы в поле и
   *  следующая буква уходила в текст («2026-09-18p»). Снаружи (Alt+буква из
   *  поля) фокус не трогаем вовсе: человек продолжает печатать. */
  function выбрать(d: Date) {
    if (коробка.current?.contains(document.activeElement)) перейти(d);
    else setФокус(d);
    onPick(iso(d));
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLElement>) {
    // Alt-сочетания ловит родитель: они работают и в поле, где голая буква —
    // это буква. Здесь они не нужны и не должны сработать дважды.
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const цель = шаг(e, фокус, week.firstDay);
    if (цель) {
      e.preventDefault();
      перейти(цель);
      return;
    }
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      выбрать(фокус);
      return;
    }
    if (e.key === 'Escape') onEscape?.();
  }

  const дни = monthGrid(фокус, week.firstDay);
  const недели = Array.from({ length: 6 }, (_, i) => дни.slice(i * 7, i * 7 + 7));

  return (
    <div className={styles.box} ref={коробка} onKeyDown={onKeyDown}>
      <div className={styles.head}>
        <button type="button" className="quiet" aria-label="предыдущий месяц"
                onClick={() => перейти(addMonths(фокус, -1))}>‹</button>
        <span id={capId} className={styles.caption}>{captionOf(фокус)}</span>
        <button type="button" className="quiet" aria-label="следующий месяц"
                onClick={() => перейти(addMonths(фокус, 1))}>›</button>
      </div>

      <table role="grid" aria-labelledby={capId} className={styles.grid}>
        <thead>
          <tr>
            {weekdayNames(week.firstDay).map((w) => (
              <th key={w.short} scope="col" abbr={w.long}>{w.short}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {недели.map((строка) => (
            <tr key={iso(строка[0])}>
              {строка.map((d) => {
                const ключ = iso(d);
                const выбран = ключ === дата;
                const нынешний = ключ === iso(сегодня);
                const класс = [
                  styles.day,
                  d.getMonth() === фокус.getMonth() ? '' : styles.outside,
                  week.weekend.includes(isoDay(d)) ? styles.weekend : '',
                  нынешний ? styles.today : '',
                  выбран ? styles.picked : '',
                ].filter(Boolean).join(' ');
                return (
                  <td key={ключ} role="gridcell" aria-selected={выбран}>
                    <button
                      type="button"
                      data-day={ключ}
                      className={класс}
                      tabIndex={ключ === iso(фокус) ? 0 : -1}
                      aria-label={dayLabel(d)}
                      aria-current={нынешний ? 'date' : undefined}
                      onClick={() => выбрать(d)}
                    >
                      {d.getDate()}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>

      <p className={styles.live} role="status">{dayLabel(фокус)}</p>
      <QuickKeys />
    </div>
  );
}
