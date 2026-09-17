// Регрессия: ответ на устаревший текст перетирал свежую подпись. Снятия
// таймера в `DateField` не хватало — улетевший запрос доживал до ответа.
// Поймано на стенде пикеров: очередь быстрых клавиш (z, потом w) оставляла
// в поле «2026-09-24», а под полем «завтра».
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render as renderRaw, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { DateField } from './DateField';

/** Поле спрашивает у ядра раскраску сетки, поэтому ему нужен клиент запросов.
 *  Повторов не делаем: тест не должен ждать их впустую. */
function render(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const обёртка = (что: ReactNode) => <QueryClientProvider client={qc}>{что}</QueryClientProvider>;
  const итог = renderRaw(обёртка(ui));
  // `rerender` из библиотеки подменяет корень целиком, вместе с провайдером,
  // поэтому оборачиваем и его.
  return { ...итог, rerender: (следующий: ReactNode) => итог.rerender(обёртка(следующий)) };
}

type Ответ = { текст: string; задержка: number };

function стабФетча(ответы: Ответ[]) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    const { text } = JSON.parse(await req.text()) as { text: string };
    const о = ответы.find((x) => x.текст === text);
    await new Promise((r) => setTimeout(r, о?.задержка ?? 0));
    // Разбор подделываем ровно настолько, насколько полю от него нужно:
    // строка даты (и час, если он был во вводе) плюс подпись для человека.
    const время = /(\d{1,2})[:-](\d{2})/.exec(text);
    const час = время ? `T${время[1].padStart(2, '0')}:${время[2]}:00` : '';
    const день = /^\d{4}-\d{2}-\d{2}/.exec(text)?.[0] ?? '2026-09-18';
    return new Response(
      JSON.stringify({
        ok: true, date: `${день}${час}`,
        label: `подпись ${text}${час ? ` ${время![1]}:${время![2]}` : ''}`, past: false,
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  }));
}

function Поле({ value }: { value: string }) {
  return (
    <DateField id="d" value={value} onChange={() => {}} onParsed={() => {}}
               onSubmit={() => {}} label="Дата" />
  );
}

beforeEach(() => { vi.unstubAllGlobals(); });

describe('DateField', () => {
  it('медленный ответ на прошлый текст не перетирает свежую подпись', async () => {
    стабФетча([{ текст: 'первый 18', задержка: 300 }, { текст: 'второй 24', задержка: 0 }]);
    const { rerender } = render(<Поле value="первый 18" />);
    // Даём запросу уйти (debounce 200 мс), но не дожидаемся ответа.
    await new Promise((r) => setTimeout(r, 250));
    rerender(<Поле value="второй 24" />);

    await waitFor(() => expect(screen.getByText('подпись второй 24')).toBeInTheDocument());
    // Ответ на «первый 18» приходит позже — и должен быть выброшен.
    await new Promise((r) => setTimeout(r, 400));
    expect(screen.getByText('подпись второй 24')).toBeInTheDocument();
    expect(screen.queryByText('подпись первый 18')).not.toBeInTheDocument();
  });

  it('очистка поля гасит подпись и не даёт вернуть её висящему ответу', async () => {
    стабФетча([{ текст: 'первый 18', задержка: 300 }]);
    const { rerender } = render(<Поле value="первый 18" />);
    await new Promise((r) => setTimeout(r, 250));
    rerender(<Поле value="" />);
    await new Promise((r) => setTimeout(r, 400));
    expect(screen.queryByText('подпись первый 18')).not.toBeInTheDocument();
  });
});


/** Поле со своим состоянием — как оно живёт в форме и в окне контроля. */
function Живое({ value = '', id = 'd', label = 'Дата' }:
                { value?: string; id?: string; label?: string }) {
  const [текст, setТекст] = useState(value);
  return (
    <DateField id={id} value={текст} onChange={setТекст} onParsed={() => {}}
               onSubmit={() => {}} withTime={false} label={label} />
  );
}

function поле(имя = 'Дата'): HTMLInputElement {
  return screen.getByLabelText(имя) as HTMLInputElement;
}

function нажать(code: string, key: string, опции: KeyboardEventInit = {}) {
  act(() => {
    document.dispatchEvent(new KeyboardEvent('keydown', { code, key, bubbles: true, ...опции }));
  });
}

function открытьКалендарь() {
  act(() => {
    поле().focus();
    поле().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
  });
}

describe('календарь и быстрые клавиши поля', () => {
  beforeEach(() => {
    стабФетча([]);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(2026, 8, 17, 10, 0, 0));  // четверг
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('стрелка вниз открывает сетку и уводит в неё курсор', () => {
    render(<Живое />);
    открытьКалендарь();

    const день = document.activeElement as HTMLElement;
    expect(день.tagName).toBe('BUTTON');
    expect(день.getAttribute('aria-label')).toContain('17 сентября 2026');
  });

  it('пока курсор в поле, голая буква остаётся буквой, а с Alt работает', () => {
    render(<Живое />);
    act(() => { поле().focus(); });

    // Нажатие приходит в само поле — как при обычном наборе текста.
    act(() => {
      поле().dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyP', key: 'з', bubbles: true }));
    });
    expect(поле().value).toBe('');

    нажать('KeyP', 'з', { altKey: true });
    expect(поле().value).toBe('2026-09-18');
  });

  it('в сетке работают голые буквы, и на русской раскладке тоже', () => {
    render(<Живое />);
    открытьКалендарь();

    нажать('KeyG', 'п');  // понедельник
    expect(поле().value).toBe('2026-09-21');
  });

  it('«+неделя» шагает от курсора и жмётся подряд', () => {
    render(<Живое />);
    открытьКалендарь();

    нажать('KeyY', 'н');
    expect(поле().value).toBe('2026-09-24');
    нажать('KeyY', 'н');
    expect(поле().value).toBe('2026-10-01');
    нажать('KeyV', 'м');
    expect(поле().value).toBe('2026-11-01');
  });

  it('чужое поле на странице клавиши не забирает', () => {
    render(<><Живое id="одно" label="Первая" /><Живое id="другое" label="Вторая" /></>);
    act(() => { поле('Вторая').focus(); });

    нажать('KeyP', 'з', { altKey: true });
    expect(поле('Вторая').value).toBe('2026-09-18');
    expect(поле('Первая').value).toBe('');
  });

  it('Escape закрывает сетку и не отдаёт событие наружу', () => {
    // Поле живёт в диалоге Radix, а тот слушает Escape на документе и
    // закрывает окно. Первое нажатие обязано достаться календарю.
    const снаружи = vi.fn();
    document.addEventListener('keydown', снаружи, true);
    render(<Живое />);
    открытьКалендарь();
    снаружи.mockClear();

    act(() => {
      document.activeElement!.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });

    expect(document.querySelector('table[role=grid]')).toBeNull();
    expect(document.activeElement).toBe(поле());
    expect(снаружи).not.toHaveBeenCalled();
    document.removeEventListener('keydown', снаружи, true);
  });

  it('выбор дня сохраняет введённый час', async () => {
    render(<Живое value="завтра в 9:30" />);
    // Час знает только ядро: ждём разбора, иначе поле про него не в курсе.
    await waitFor(() => expect(screen.getByText(/9:30/)).toBeInTheDocument());
    открытьКалендарь();

    нажать('KeyY', 'н');
    expect(поле().value).toBe('2026-09-25 09:30');
  });
});
