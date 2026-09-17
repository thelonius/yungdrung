// Регрессии, найденные подряд на живом стенде:
//  1) быстрые клавиши не ловились, пока фокус не побывал внутри календаря —
//     открыл страницу, жмёшь буквы, тишина;
//  2) они обязаны работать на любой раскладке (опознаётся `event.code`);
//  3) шаговые клавиши считаются от курсора и жмутся подряд, иначе «+неделя»
//     всё время возвращала одну и ту же дату от сегодня.
import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PickerLabPage } from './PickerLabPage';

function стабФетча() {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    const { text } = JSON.parse(await req.text()) as { text: string };
    return new Response(
      JSON.stringify({ ok: true, date: text, label: `подпись ${text}`, past: false }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  }));
}

function нажать(code: string, key: string, опции: KeyboardEventInit = {}) {
  act(() => {
    document.dispatchEvent(new KeyboardEvent('keydown', { code, key, bubbles: true, ...опции }));
  });
}

/** Поле левой колонки — она активна по умолчанию. */
function левоеПоле(): HTMLInputElement {
  return screen.getAllByLabelText('Дата контроля')[0] as HTMLInputElement;
}

beforeEach(() => {
  стабФетча();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date(2026, 8, 17, 10, 0, 0));  // четверг
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('быстрые клавиши пикера', () => {
  it('ловятся с фокусом на странице, а не только внутри календаря', async () => {
    render(<PickerLabPage />);
    expect(document.activeElement).toBe(document.body);

    нажать('KeyP', 'p');  // «з» — завтра
    await waitFor(() => expect(левоеПоле().value).toBe('2026-09-18'));
  });

  it('работают на русской раскладке теми же кнопками', async () => {
    render(<PickerLabPage />);

    нажать('KeyG', 'п');  // понедельник
    await waitFor(() => expect(левоеПоле().value).toBe('2026-09-21'));

    нажать('KeyC', 'с');  // сегодня
    await waitFor(() => expect(левоеПоле().value).toBe('2026-09-17'));
  });

  it('«+неделя» шагает от курсора и жмётся подряд', async () => {
    render(<PickerLabPage />);

    нажать('KeyY', 'н');
    await waitFor(() => expect(левоеПоле().value).toBe('2026-09-24'));

    // Второе нажатие считает уже от новой даты, а не снова от сегодня.
    нажать('KeyY', 'н');
    await waitFor(() => expect(левоеПоле().value).toBe('2026-10-01'));

    // И месяц шагает оттуда же, куда доехал курсор.
    нажать('KeyV', 'м');
    await waitFor(() => expect(левоеПоле().value).toBe('2026-11-01'));
  });

  it('якорные клавиши считаются от сегодня, куда бы ни уехал курсор', async () => {
    render(<PickerLabPage />);

    нажать('KeyV', 'м');
    await waitFor(() => expect(левоеПоле().value).toBe('2026-10-17'));

    нажать('KeyP', 'з');  // завтра — всё равно от сегодня
    await waitFor(() => expect(левоеПоле().value).toBe('2026-09-18'));
  });

  it('пока курсор в поле, голая буква остаётся буквой, а с Alt работает', async () => {
    render(<PickerLabPage />);
    const поле = левоеПоле();
    поле.focus();

    act(() => {
      поле.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyP', key: 'з', bubbles: true }));
    });
    expect(поле.value).toBe('');

    act(() => {
      поле.dispatchEvent(new KeyboardEvent('keydown', {
        code: 'KeyP', key: 'з', altKey: true, bubbles: true,
      }));
    });
    await waitFor(() => expect(левоеПоле().value).toBe('2026-09-18'));
  });
});
