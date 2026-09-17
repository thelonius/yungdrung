// Клавиатурный проход по ленте с подменённым API: `d` на строке под фокусом
// зовёт mark и убирает строку до ответа сервера; `u` зовёт undo.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HotkeysProvider } from 'react-hotkeys-hook';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { FeedResult, MarkResult } from '@/api/client';
import { ToasterProvider } from '@/ui/Toaster';
import { FeedPage } from './FeedPage';

const calls: { method: string; url: string; body?: unknown }[] = [];

function feedFixture(): FeedResult {
  const row = (task_id: number, step: number, title: string) => ({
    task_id, step, task: `Задача ${task_id}`, title, group: null, note: null,
    control_at: '2026-09-08', show_at: '2026-09-08T09:00:00', state: 'due', postponed: 0,
    stalled: false, tags: [], last_reason: null, actions: ['done', 'notdone', 'defer', 'skip'],
  });
  return {
    now: '2026-09-08T11:00:00',
    feed: [row(1, 1, 'Позвонить'), row(2, 1, 'Написать')],
    overdue_count: 0, counts: { overdue: 0, today: 2, waiting: 3 }, next_ahead: null,
    stalled_count: 0, broken: [],
  };
}

let feedState: FeedResult;
let resolveMark: ((r: MarkResult) => void) | null = null;

beforeEach(() => {
  calls.length = 0;
  feedState = feedFixture();
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    // openapi-fetch отдаёт готовый Request, а не (url, init): читаем оба вида.
    const req = input instanceof Request ? input : new Request(String(input), init);
    const url = req.url;
    const method = req.method;
    const text = method === 'GET' ? '' : await req.text();
    calls.push({ method, url, body: text ? JSON.parse(text) : undefined });
    const json = (o: unknown, status = 200) =>
      new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } });
    if (url.endsWith('/api/v1/feed')) return json(feedState);
    if (url.endsWith('/api/v1/reasons')) return json({ reasons: ['не было времени'] });
    if (url.includes('/mark')) {
      const r: MarkResult = await new Promise((res) => { resolveMark = res; });
      feedState = { ...feedState, feed: feedState.feed.filter((x) => x.task_id !== r.task_id) };
      return json(r);
    }
    if (url.includes('/undo')) return json({ ok: true, task_id: 1, task: 'Задача 1', step: 1,
      status: 'pending', task_status: 'due', dates_assigned: [], stalled: 0, undone: 'done',
      counts: { overdue: 0, today: 2, waiting: 3 } });
    return json({ ok: false, errors: [{ field: null, error: 'нет такого' }] }, 404);
  }));
});

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // MemoryRouter: срез 2 завёл `useNavigate` (открыть карточку, палитра
  // команд) — в проде страница всегда внутри роутера (`app/router.tsx`),
  // здесь тот же контекст нужен только для этого.
  return render(
    <MemoryRouter initialEntries={['/']}>
      <QueryClientProvider client={qc}>
        <HotkeysProvider>
          <ToasterProvider>
            <FeedPage />
          </ToasterProvider>
        </HotkeysProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('лента с клавиатуры', () => {
  it('d убирает строку под фокусом сразу и показывает тост с отменой', async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText('Позвонить');
    expect(screen.getAllByTestId('feed-row')).toHaveLength(2);

    await user.keyboard('d');
    // Оптимистично: строки уже одна, ответа сервера ещё нет.
    await waitFor(() => expect(screen.getAllByTestId('feed-row')).toHaveLength(1));
    const markCall = calls.find((c) => c.url.includes('/mark'));
    expect(markCall?.url).toContain('/api/v1/tasks/1/steps/1/mark');
    expect(markCall?.body).toEqual({ op: 'done', reason: null, to: null });

    resolveMark!({
      ok: true, op: 'done', task_id: 1, task: 'Задача 1', step: 1, status: 'done',
      task_status: 'waiting', next_step_id: 2, next_step_title: 'Съездить', dates_assigned: [2],
      next_check: null, stalled: 0, hint: null, undone: null, control_date: null, row: null,
      counts: { overdue: 0, today: 1, waiting: 4 },
    });
    await screen.findByText('Сделано. Следующий шаг — Съездить');

    await user.keyboard('u');
    await waitFor(() => expect(calls.some((c) => c.url.includes('/steps/1/undo'))).toBe(true));
  });

  it('j/k двигают фокус, t открывает окно переноса с полем даты', async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText('Позвонить');
    await user.keyboard('j');
    const rows = screen.getAllByTestId('feed-row');
    expect(rows[1]).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('t');
    const dateInput = await screen.findByLabelText('Новая дата контроля');
    await waitFor(() => expect(dateInput).toHaveFocus());
    // В открытом окне `d` не отмечает строку под фокусом.
    await user.keyboard('d');
    expect(calls.some((c) => c.url.includes('/mark'))).toBe(false);
  });
});
