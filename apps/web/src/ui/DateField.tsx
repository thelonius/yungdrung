// Поле даты: текст главный («+3», «завтра в полдесятого»), разбор — только у
// ядра через /api/v1/parse-date, подпись под полем показывает, как поняли.
// Общий компонент (перенесён из features/feed в срезе 2, §5.1): карточка и
// форма новой задачи ставят по несколько полей дат на одном экране, поэтому
// `id` обязателен — раньше он был жёстко зашит ("c-date"), и второй экземпляр
// поля на странице дал бы дубль id (map-client.md, риск 2).
//
// Календарь живёт здесь же и открывается стрелкой вниз. Он вторичен: даты
// вводятся текстом, а сетка отвечает на вопрос «какой это день недели» и даёт
// листать мышью или стрелками. Выбранный день возвращается в поле строкой и
// разбирается ядром заново — разбор в приложении один.
import { useEffect, useId, useRef, useState } from 'react';
import { useHotkeys } from 'react-hotkeys-hook';
import { api } from '@/api/client';
import type { WhenResult } from '@/api/client';
import { useCalendar } from '@/api/hooks';
import { MonthGrid } from './calendar/MonthGrid';
import { fromIso, iso } from './calendar/days';
import { СТРОКА_БЫСТРЫХ, СТРОКА_БЫСТРЫХ_ALT, быстраяПоКнопке } from './calendar/keys';
import styles from './control/ControlDialog.module.css';

// Пресеты меняются `withTime`: там, где времени у даты не бывает (старт
// шаблона, дата старта задачи), «через час» и «полдесятого» не к месту —
// решение CLAUDE.md («сдвиги приращениями», Yd.DateField.withTime).
// У тех, что совпадают с быстрой клавишей, подписана и клавиша.
const PRESETS_TIME: { label: string; when: string; key?: string }[] = [
  { label: 'через час', when: 'через час' },
  { label: 'завтра утром', when: '+1 09:00' },
  { label: 'через 3 дня', when: '+3' },
  { label: 'через неделю', when: '+7' },
];

const PRESETS_DATE: { label: string; when: string; key?: string }[] = [
  { label: 'сегодня', when: 'сегодня', key: 'с' },
  { label: 'завтра', when: 'завтра', key: 'з' },
  { label: 'пн', when: 'пн', key: 'п' },
];

type Props = {
  id: string;
  value: string;
  onChange: (text: string) => void;
  onParsed: (r: WhenResult | null) => void;
  onSubmit: () => void;
  autoFocus?: boolean;
  inputRef?: React.RefObject<HTMLInputElement | null>;
  /** По умолчанию `true` (пресеты с часами). `false` — только по дню
   * (SLICE2_SPEC.md §5.1: `DateField withTime?: boolean`, решение Р16). */
  withTime?: boolean;
  /** Подпись поля — по умолчанию как у окна контроля; карточка и форма
   * задают свою («дата начала», «дата контроля N-го шага»). Необязательный
   * проп не входит в буквальный список §5.1, добавлен аддитивно: без него
   * каждое поле на карточке подписывалось бы одинаково (см. deviations). */
  label?: string;
};

export function DateField({
  id, value, onChange, onParsed, onSubmit, autoFocus, inputRef,
  withTime = true, label = 'Новая дата контроля',
}: Props) {
  const [parsed, setParsed] = useState<WhenResult | null>(null);
  const [открыт, setОткрыт] = useState(false);
  const [вПоле, setВПоле] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  // Номер последнего запроса. Снятия таймера мало: запрос, который успел
  // уйти, продолжает лететь, и его ответ приходит уже после ответа на более
  // свежий текст — под полем оставалась подпись от позапрошлого ввода.
  const запрос = useRef(0);
  const свой = useRef<HTMLInputElement>(null);
  const поле = inputRef ?? свой;
  const коробка = useRef<HTMLDivElement>(null);
  const сетка = useRef<HTMLDivElement>(null);
  // Где стоит курсор в сетке: от него считаются шаговые клавиши. В ref, а не
  // в состоянии — иначе следующее нажатие ждало бы перерисовки, а с ней и
  // ответа сервера на предыдущее, и два «+неделя» подряд дали бы один день.
  const курсор = useRef<string | null>(null);
  const подсказкаId = useId();
  // Какие дни показывает сетка: по ним спрашиваем у ядра выходные заказчика и
  // занятость. Пока календарь закрыт, не спрашиваем вовсе.
  const [диапазон, setДиапазон] = useState<{ от: string; до: string } | null>(null);
  const раскраска = useCalendar(открыт ? диапазон?.от ?? null : null,
                                открыт ? диапазон?.до ?? null : null);

  useEffect(() => {
    window.clearTimeout(timer.current);
    if (!value.trim()) {
      запрос.current += 1;  // и висящий ответ уже не воскресит подпись
      setParsed(null);
      onParsed(null);
      return;
    }
    timer.current = window.setTimeout(async () => {
      const мой = ++запрос.current;
      const { data } = await api.POST('/api/v1/parse-date', { body: { text: value } });
      if (мой !== запрос.current) return;
      setParsed(data ?? null);
      onParsed(data ?? null);
    }, 200);
    return () => window.clearTimeout(timer.current);
    // onParsed намеренно не в зависимостях: это колбэк родителя, он меняется
    // на каждом рендере, а перезапускать разбор от этого не нужно.
  }, [value]);

  const разобрано = parsed?.ok ? parsed.date ?? null : null;
  // Час, если он был во вводе: выбор дня в сетке не должен его стирать —
  // человек написал «завтра в 9», а потом передумал про день, а не про час.
  const время = разобрано?.includes('T') ? разобрано.slice(11, 16) : null;

  function поставить(день: Date) {
    const текст = iso(день);
    курсор.current = текст;
    onChange(время ? `${текст} ${время}` : текст);
  }

  const активно = вПоле || открыт;
  function быстрая(кнопка: string) {
    const б = быстраяПоКнопке(кнопка);
    if (!б) return;
    // Якорные клавиши считаются от сегодня, шаговые — от того дня, где стоит
    // курсор в сетке; без него шагаем от разобранной даты, а если нет и её —
    // от сегодня.
    const сегодня = new Date();
    поставить(б.from(сегодня, fromIso(курсор.current) ?? fromIso(разобрано) ?? сегодня));
  }
  // `useHotkeys` сравнивает `event.code`, то есть место на клавиатуре:
  // раскладку переключать не нужно. Голые буквы включены, только когда
  // курсор в сетке или поле в работе; пока печатают в поле, буква остаётся
  // буквой, и для этого случая есть Alt.
  useHotkeys(СТРОКА_БЫСТРЫХ, (_, h) => быстрая(String(h.keys?.[0] ?? '')),
             { enabled: активно, preventDefault: true }, [активно, разобрано, время]);
  useHotkeys(СТРОКА_БЫСТРЫХ_ALT, (_, h) => быстрая(String(h.keys?.[0] ?? '')),
             { enabled: активно, preventDefault: true, enableOnFormTags: true },
             [активно, разобрано, время]);

  // Сетки в разметке ещё нет в тот момент, когда её просят открыть, поэтому
  // курсор переносим следующим эффектом — после того, как она отрисована.
  const надоВСетку = useRef(false);
  useEffect(() => {
    if (!открыт || !надоВСетку.current) return;
    надоВСетку.current = false;
    сетка.current?.querySelector<HTMLButtonElement>('button[tabindex="0"]')?.focus();
  }, [открыт]);

  function открыть() {
    надоВСетку.current = true;
    setОткрыт(true);
  }

  // Escape при открытой сетке закрывает её, а не диалог, внутри которого поле
  // живёт. Слушаем окно в фазе перехвата: Radix ставит свой обработчик на
  // документ, а до документа событие доходит уже после окна, и погашенное
  // здесь он не увидит. Через его проп `onEscapeKeyDown` это не решается —
  // тогда о календаре пришлось бы знать каждому диалогу.
  useEffect(() => {
    if (!открыт) return;
    const наEscape = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopPropagation();
      закрыть();
    };
    window.addEventListener('keydown', наEscape, true);
    return () => window.removeEventListener('keydown', наEscape, true);
  }, [открыт]);

  function закрыть() {
    setОткрыт(false);
    поле.current?.focus();
  }

  const bad = parsed && !parsed.ok;
  const presets = withTime ? PRESETS_TIME : PRESETS_DATE;
  return (
    <div
      className={styles.field}
      ref={коробка}
      onBlur={(e) => {
        if (открыт && !коробка.current?.contains(e.relatedTarget)) setОткрыт(false);
      }}
    >
      <label htmlFor={id}>{label}</label>
      <div className={styles.presets}>
        {presets.map((p) => (
          <button
            type="button"
            key={p.when}
            className={`small ${value === p.when ? styles.presetOn : ''}`}
            onClick={() => onChange(p.when)}
          >
            {p.label}{p.key && <> <kbd>{p.key}</kbd></>}
          </button>
        ))}
        <button
          type="button"
          className="small quiet"
          aria-expanded={открыт}
          onClick={() => (открыт ? закрыть() : открыть())}
        >
          календарь <kbd>↓</kbd>
        </button>
      </div>
      <input
        id={id}
        ref={поле}
        type="text"
        autoComplete="off"
        autoFocus={autoFocus}
        className={bad ? 'invalid' : ''}
        placeholder="или свой вариант: завтра в 9, 18.08, пн, полдесятого"
        aria-describedby={подсказкаId}
        value={value}
        onFocus={() => setВПоле(true)}
        onBlur={() => setВПоле(false)}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); onSubmit(); return; }
          if (e.key === 'ArrowDown') { e.preventDefault(); открыть(); }
        }}
      />
      <span id={подсказкаId} className={`${styles.preview} ${parsed?.past ? styles.past : ''}`}>
        {parsed?.ok ? parsed.label : parsed?.error ?? ''}
      </span>
      {открыт && (
        <MonthGrid
          value={разобрано}
          onPick={(d) => { поставить(new Date(`${d}T00:00:00`)); закрыть(); }}
          onCursor={(d) => { курсор.current = d; }}
          onRange={(от, до) => setДиапазон({ от, до })}
          marks={раскраска.data}
          containerRef={сетка}
        />
      )}
    </div>
  );
}
