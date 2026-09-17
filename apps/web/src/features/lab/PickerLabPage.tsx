// Стенд для выбора пикера: два прототипа с одинаковой обвязкой, разными
// сетками. Страница временная — живёт, пока не выбран вариант (решение
// «пикеры» в PROTOCOL.md), в шапку и палитру команд намеренно не добавлена.
//
// Обвязка у обеих колонок общая и настоящая: то же поле DateField, тот же
// разбор ядром через /api/v1/parse-date, тот же возврат выбора строкой в
// поле. Сравнивается ровно сетка месяца.
import { Suspense, lazy, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useHotkeys } from 'react-hotkeys-hook';
import type { WhenResult } from '@/api/client';
import { DateField } from '@/ui/DateField';
import { MonthGrid } from '@/ui/calendar/MonthGrid';
import { fromIso, iso } from '@/ui/calendar/days';
import { СТРОКА_БЫСТРЫХ, СТРОКА_БЫСТРЫХ_ALT, быстраяПоКнопке } from '@/ui/calendar/keys';
import styles from './PickerLab.module.css';

const DayPickerGrid = lazy(() =>
  import('./DayPickerGrid').then((m) => ({ default: m.DayPickerGrid })));

type GridProps = {
  value: string | null;
  onPick: (date: string) => void;
  onEscape?: () => void;
  onCursor?: (date: string) => void;
  containerRef?: React.RefObject<HTMLDivElement | null>;
};

function Колонка({ title, note, cost, grid, активна, onActivate }: {
  title: string; note: string; cost: string; grid: (p: GridProps) => ReactNode;
  активна: boolean; onActivate: () => void;
}) {
  const [текст, setТекст] = useState('');
  const [разбор, setРазбор] = useState<WhenResult | null>(null);
  // Курсор в ref, а не в состоянии: он нужен только следующему нажатию, и
  // ждать ради него перерисовки (а с ней и ответа сервера) нельзя — два
  // быстрых «+неделя» подряд считались бы от одного и того же дня.
  const курсор = useRef<string | null>(null);
  const поле = useRef<HTMLInputElement>(null);
  const коробка = useRef<HTMLDivElement>(null);
  const id = useId();

  /** Быстрые клавиши ловим на документе, как ленты и карточка ловят свои:
   *  раньше они висели на календаре, и пока фокус не побывал внутри него,
   *  ловить нажатие было некому — открыл страницу, жмёшь буквы, тишина.
   *
   *  `useHotkeys` сравнивает `event.code`, то есть место на клавиатуре:
   *  раскладку переключать не нужно ни здесь, ни в остальном приложении.
   *  Голая буква молчит, пока курсор в текстовом поле (там она буква — ввод
   *  «завтра в полдесятого» важнее), и для этого случая есть Alt. */
  const выбратьБыструю = (кнопка: string) => {
    const быстрая = быстраяПоКнопке(кнопка);
    if (!быстрая) return;
    // Шаговые клавиши считаются от курсора — от того дня, где он стоит в
    // сетке. Без курсора (в календарь ещё не заходили) шагаем от разобранной
    // даты, а если нет и её — от сегодня.
    const сегодня = new Date();
    const откуда = fromIso(курсор.current) ?? fromIso(разбор?.date ?? null) ?? сегодня;
    const куда = iso(быстрая.from(сегодня, откуда));
    курсор.current = куда;
    setТекст(куда);
  };
  useHotkeys(СТРОКА_БЫСТРЫХ, (_, h) => выбратьБыструю(String(h.keys?.[0] ?? '')),
             { enabled: активна, preventDefault: true }, [активна, разбор?.date]);
  useHotkeys(СТРОКА_БЫСТРЫХ_ALT, (_, h) => выбратьБыструю(String(h.keys?.[0] ?? '')),
             { enabled: активна, preventDefault: true, enableOnFormTags: true },
             [активна, разбор?.date]);

  /** Стрелка вниз из поля уводит в календарь — обычный приём для поля с
   *  выпадающим списком; обратно оттуда Escape. */
  function вниз(e: React.KeyboardEvent<HTMLElement>) {
    if (e.key !== 'ArrowDown' || e.target !== поле.current) return;
    e.preventDefault();
    коробка.current?.querySelector<HTMLButtonElement>('button[tabindex="0"]')?.focus();
  }

  return (
    <section
      className={styles.col}
      onKeyDownCapture={вниз}
      onFocusCapture={onActivate}
      onMouseDown={onActivate}
    >
      <h2>{title} {активна && <span className={styles.badge}>клавиши сюда</span>}</h2>
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
        onCursor: (d) => { курсор.current = d; },
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
  // На стенде два пикера сразу, а в бою он один. Чтобы буквы не срабатывали
  // в обеих колонках разом, они уходят в ту, которой последний раз касались.
  const [активная, setАктивная] = useState(0);
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
          активна={активная === 0}
          onActivate={() => setАктивная(0)}
          title="Свой на Intl"
          note="250 строк, ноль зависимостей. Названия месяцев и дней — из браузера."
          cost="Цена: наш код. Клавиатуру и роли пишем и чиним сами."
          grid={(p) => <MonthGrid {...p} />}
        />
        <Колонка
          активна={активная === 1}
          onActivate={() => setАктивная(1)}
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
