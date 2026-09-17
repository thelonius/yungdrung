// Второй прототип: та же обвязка, но сетку рисует react-day-picker 10.
// Грузится отдельным куском (`lazy` в PickerLabPage): пока решение не принято,
// библиотека не должна попадать в основной бандл приложения.
//
// Быстрые клавиши и Escape — общие с нашей сеткой (ui/calendar/quickKeys),
// иначе сравнивались бы обвязки, а не сетки. Всё остальное — её работа.
import { useEffect, useMemo, useState } from 'react';
import { DayPicker } from 'react-day-picker';
import { ru } from 'react-day-picker/locale';
import 'react-day-picker/style.css';
import { dayLabel, fromIso, iso } from '@/ui/calendar/days';
import { QuickKeys } from '@/ui/calendar/QuickKeys';
import styles from './PickerLab.module.css';

type Props = {
  value: string | null;
  onPick: (date: string) => void;
  onEscape?: () => void;
  today?: Date;
  containerRef?: React.RefObject<HTMLDivElement | null>;
};

export function DayPickerGrid({ value, onPick, onEscape, today, containerRef }: Props) {
  const сегодня = useMemo(() => today ?? new Date(), [today]);
  const выбрано = fromIso(value);
  const [месяц, setМесяц] = useState<Date>(() => выбрано ?? сегодня);
  // Строка «какой это день недели» — та же, что у своей сетки, иначе
  // сравнение мерило бы подписи, а не клавиатуру. Своего «где сейчас фокус»
  // библиотека наружу не отдаёт, его приходится ловить через onDayFocus.
  const [подсвечен, setПодсвечен] = useState<Date | null>(null);
  const ключ = выбрано ? iso(выбрано) : null;

  // Месяц только состоянием, а не выводом из выбранной даты: иначе Shift+→
  // и PageDown меняли бы его, а следующий же рендер возвращал обратно к
  // выбранному дню — листание клавишами переставало работать вовсе.
  useEffect(() => {
    const d = fromIso(ключ);
    if (d) setМесяц(d);
  }, [ключ]);

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') onEscape?.();
  }

  return (
    <div className={styles.rdpBox} ref={containerRef} onKeyDown={onKeyDown}>
      <DayPicker
        mode="single"
        required
        locale={ru}
        weekStartsOn={1}
        showOutsideDays
        month={месяц}
        onMonthChange={setМесяц}
        selected={выбрано ?? undefined}
        onSelect={(d) => onPick(iso(d))}
        today={сегодня}
        modifiers={{ выходной: { dayOfWeek: [0, 6] } }}
        modifiersClassNames={{ выходной: styles.rdpWeekend }}
        onDayFocus={(d) => setПодсвечен(d)}
        onDayBlur={() => setПодсвечен(null)}
      />
      <p className={styles.live} role="status">{dayLabel(подсвечен ?? выбрано ?? сегодня)}</p>
      <QuickKeys />
    </div>
  );
}
