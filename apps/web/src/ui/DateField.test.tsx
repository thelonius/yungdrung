// Регрессия: ответ на устаревший текст перетирал свежую подпись. Снятия
// таймера в `DateField` не хватало — улетевший запрос доживал до ответа.
// Поймано на стенде пикеров: очередь быстрых клавиш (z, потом w) оставляла
// в поле «2026-09-24», а под полем «завтра».
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DateField } from './DateField';

type Ответ = { текст: string; задержка: number };

function стабФетча(ответы: Ответ[]) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    const { text } = JSON.parse(await req.text()) as { text: string };
    const о = ответы.find((x) => x.текст === text);
    await new Promise((r) => setTimeout(r, о?.задержка ?? 0));
    return new Response(
      JSON.stringify({ ok: true, date: `2026-09-${text.slice(-2)}`, label: `подпись ${text}`, past: false }),
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
