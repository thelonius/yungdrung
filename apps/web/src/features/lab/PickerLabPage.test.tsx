// Регрессия на две находки подряд: быстрые клавиши не ловились, пока фокус
// не побывал внутри календаря (открыл страницу — буквы в пустоту), и должны
// работать на любой раскладке. Опознаётся место на клавиатуре (`event.code`),
// поэтому русские «я» и «з» на тех же кнопках обязаны делать то же самое.
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

    нажать('KeyZ', 'z');
    await waitFor(() => expect(левоеПоле().value).toBe('2026-09-18'));
  });

  it('работают на русской раскладке теми же кнопками', async () => {
    render(<PickerLabPage />);

    нажать('KeyP', 'з');  // там, где в латинской «p» — понедельник
    await waitFor(() => expect(левоеПоле().value).toBe('2026-09-21'));

    нажать('KeyW', 'ц');  // там, где «w» — через неделю
    await waitFor(() => expect(левоеПоле().value).toBe('2026-09-24'));
  });

  it('пока курсор в поле, голая буква остаётся буквой, а с Alt работает', async () => {
    render(<PickerLabPage />);
    const поле = левоеПоле();
    поле.focus();

    act(() => {
      поле.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyZ', key: 'я', bubbles: true }));
    });
    expect(поле.value).toBe('');

    act(() => {
      поле.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyZ', key: 'я', altKey: true, bubbles: true }));
    });
    await waitFor(() => expect(левоеПоле().value).toBe('2026-09-18'));
  });
});
