// Стенд для выбора пикера: два прототипа с одинаковой обвязкой, разными
// сетками. Страница временная — живёт, пока не выбран вариант (решение
// «пикеры» в PROTOCOL.md), в шапку и палитру команд намеренно не добавлена.
//
// Обвязка у обеих колонок общая и настоящая: то же поле DateField, тот же
// разбор ядром через /api/v1/parse-date, тот же возврат выбора строкой в
// поле. Сравнивается ровно сетка месяца.
import { Suspense, lazy, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { WhenResult } from '@/api/client';
import { DateField } from '@/ui/DateField';
import { MonthGrid } from '@/ui/calendar/MonthGrid';
import { iso } from '@/ui/calendar/days';
import { быстраяПоСобытию } from '@/ui/calendar/keys';
import styles from './PickerLab.module.css';

const DayPickerGrid = lazy(() =>
  import('./DayPickerGrid').then((m) => ({ default: m.DayPickerGrid })));

type GridProps = {
  value: string | null;
  onPick: (date: string) => void;
  onEscape?: () => void;
  containerRef?: React.RefObject<HTMLDivElement | null>;
};

function Колонка({ title, note, cost, grid }: {
  title: string; note: string; cost: string; grid: (p: GridProps) => ReactNode;
}) {
  const [текст, setТекст] = useState('');
  const [разбор, setРазбор] = useState<WhenResult | null>(null);
  const поле = useRef<HTMLInputElement>(null);
  const коробка = useRef<HTMLDivElement>(null);
  const id = useId();

  /** Клавиши всей колонки, а не только сетки.
   *
   *  Стрелка вниз из поля уводит в календарь — обычный приём для поля с
   *  выпадающим списком; обратно оттуда Escape. Быстрые буквы с Alt работают
   *  где угодно, включая само поле: голая буква там — это буква, её нельзя
   *  отнять у ввода «завтра в полдесятого». */
  function клавиши(e: React.KeyboardEvent<HTMLElement>) {
    if (e.altKey && !e.ctrlKey && !e.metaKey) {
      const быстрая = быстраяПоСобытию(e);
      if (быстрая) {
        e.preventDefault();
        setТекст(iso(быстрая.from(new Date())));
      }
      return;
    }
    if (e.key !== 'ArrowDown' || e.target !== поле.current) return;
    e.preventDefault();
    коробка.current?.querySelector<HTMLButtonElement>('button[tabindex="0"]')?.focus();
  }

  return (
    <section className={styles.col} onKeyDownCapture={клавиши}>
      <h2>{title}</h2>
      <p className="note">{note}</p>
      <DateField
        id={id}
        value={текст}
        onChange={setТекст}
        onParsed={setРазбор}
        onSubmit={() => {}}
        withTime={false}
        label="Дата контроля"
        inputRef={поле}
      />
      {grid({
        value: разбор?.date ?? null,
        onPick: setТекст,
        onEscape: () => поле.current?.focus(),
        containerRef: коробка,
      })}
      <p className={styles.out}>
        {разбор?.ok && разбор.label
          ? <>ядро поняло: <b>{разбор.label}</b></>
          : <span className="muted">{разбор?.error ?? 'пока ничего не введено'}</span>}
      </p>
      <p className={styles.cost}>{cost}</p>
    </section>
  );
}

export function PickerLabPage() {
  return (
    <div className="wrap" style={{ maxWidth: 1040 }}>
      <h1>Пикер даты: два прототипа</h1>
      <p className="note">
        Поле, разбор и подпись под ним — общие и настоящие. Отличается только
        сетка месяца. Печатать можно как обычно («+3», «пн», «15 марта»),
        стрелка вниз из поля уводит в календарь, Escape возвращает обратно.
        Выбранный день уходит в поле строкой и разбирается ядром заново.
      </p>
      <div className={styles.cols}>
        <Колонка
          title="Свой на Intl"
          note="250 строк, ноль зависимостей. Названия месяцев и дней — из браузера."
          cost="Цена: наш код. Клавиатуру и роли пишем и чиним сами."
          grid={(p) => <MonthGrid {...p} />}
        />
        <Колонка
          title="react-day-picker 10"
          note="30 млн загрузок в неделю, тянет date-fns и @date-fns/tz."
          cost="Цена: 23 КБ в gzip и ломающий мажор примерно раз в два года."
          grid={(p) => (
            <Suspense fallback={<p className="note">грузится…</p>}>
              <DayPickerGrid {...p} />
            </Suspense>
          )}
        />
      </div>
    </div>
  );
}
