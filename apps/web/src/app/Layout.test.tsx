// Регрессия на находку ревью среза 2: `Layout` (QuickAdd/HelpOverlay/палитра)
// и хоткеи страницы раньше считали `overlay` независимо, поэтому буква,
// нажатая при открытом QuickAdd, долетала до хоткеев ленты под ним
// (app/overlayContext.tsx).
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HotkeysProvider } from 'react-hotkeys-hook';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { FeedResult } from '@/api/client';
import { ToasterProvider } from '@/ui/Toaster';
import { FeedPage } from '@/features/feed/FeedPage';
import { Layout } from './Layout';

const calls: { method: string; url: string }[] = [];

function feedFixture(): FeedResult {
  return {
    now: '2026-09-08T11:00:00',
    feed: [{
      task_id: 1, step: 1, task: 'Задача 1', title: 'Позвонить', group: null, note: null,
      control_at: '2026-09-08', show_at: '2026-09-08T09:00:00', state: 'due', postponed: 0,
      stalled: false, tags: [], last_reason: null, actions: ['done', 'notdone', 'defer', 'skip'],
    }],
    overdue_count: 0, counts: { overdue: 0, today: 1, waiting: 0 }, next_ahead: null,
    stalled_count: 0, broken: [],
  };
}

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    calls.push({ method: req.method, url: req.url });
    const json = (o: unknown, status = 200) =>
      new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } });
    if (req.url.endsWith('/api/v1/feed')) return json(feedFixture());
    if (req.url.endsWith('/api/v1/reasons')) return json({ reasons: [] });
    if (req.url.includes('/api/v1/extract-when')) return json({ title: '', date: null, label: null, past: false, span: null });
    if (req.url.includes('/mark')) return json({
      ok: true, task_id: 1, task: 'Задача 1', step: 1, status: 'done', task_status: 'closed',
      dates_assigned: [], stalled: 0, counts: { overdue: 0, today: 0, waiting: 0 },
    });
    return json({ ok: false, errors: [{ field: null, error: 'нет такого' }] }, 404);
  }));
});

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={['/']}>
      <QueryClientProvider client={qc}>
        <HotkeysProvider>
          <ToasterProvider>
            <Routes>
              <Route element={<Layout />}>
                <Route path="/" element={<FeedPage />} />
              </Route>
            </Routes>
          </ToasterProvider>
        </HotkeysProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('область хоткеев Layout и страницы', () => {
  it('открытый QuickAdd глушит хоткеи ленты под ним', async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText('Позвонить');

    await user.keyboard('a');
    expect(await screen.findByTestId('quick-input')).toBeInTheDocument();

    await user.keyboard('d');
    // Строка ленты должна остаться на месте — `d` не долетел до FeedPage.
    expect(screen.getByText('Позвонить')).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes('/mark'))).toBe(false);

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByTestId('quick-input')).not.toBeInTheDocument());

    await user.keyboard('d');
    await waitFor(() => expect(calls.some((c) => c.url.includes('/mark'))).toBe(true));
  });
});
