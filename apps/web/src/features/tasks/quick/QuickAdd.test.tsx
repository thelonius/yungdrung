// Быстрый ввод с подменённым API (как FeedPage.test.tsx): разбор даты,
// подсветка распознанного куска, создание и переход по второму Enter.
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QuickAdd } from './QuickAdd';

const calls: { method: string; url: string; body?: unknown }[] = [];

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    const url = req.url;
    const method = req.method;
    const text = method === 'GET' ? '' : await req.text();
    const body = text ? JSON.parse(text) : undefined;
    calls.push({ method, url, body });
    const json = (o: unknown, status = 200) =>
      new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/api/v1/extract-when')) {
      const t = (body as { text: string }).text;
      if (t.includes('сегодня')) {
        const start = t.indexOf('сегодня');
        return json({ title: t.slice(0, start).trim(), date: '2026-09-15', label: 'сегодня', past: false, span: [start, start + 'сегодня'.length] });
      }
      return json({ title: t, date: null, label: null, past: false, span: null });
    }
    if (url.includes('/api/v1/tasks/quick')) {
      const t = (body as { text: string }).text;
      if (t === 'дубль') {
        return json({ ok: false, errors: [{ field: 'title', error: 'Задача с таким названием уже есть' }] }, 422);
      }
      return json({
        ok: true, task_id: 42, task: t.replace(/ сегодня$/, ''), task_status: 'due', steps: 1, created: true,
        warnings: [], card: { ok: true, task_id: 42, task: t, task_status: 'due', status_ru: 'сегодня', body: '',
          created: '2026-09-15', start_date: '2026-09-15', tags: [], control_date: '2026-09-15', current_step: t,
          progress: '0 из 1', stalled: 0, steps: [], history: [], actions: [] },
      });
    }
    return json({ ok: false, errors: [{ field: null, error: 'нет такого' }] }, 404);
  }));
});

function mount() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/задача/:id" element={<p data-testid="card-route">карточка</p>} />
        <Route path="*" element={<QuickAdd open onClose={() => {}} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('QuickAdd', () => {
  it('показывает подпись и подсвечивает распознанный кусок текста', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ delay: null });
    mount();
    const input = screen.getByTestId('quick-input');
    await user.type(input, 'позвонить Василию сегодня');
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });

    expect(await screen.findByTestId('quick-label')).toHaveTextContent('сегодня');
    expect(screen.getByTestId('quick-span')).toHaveTextContent('сегодня');
    vi.useRealTimers();
  });

  it('без распознанной даты подпись предупреждает про сегодня', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ delay: null });
    mount();
    await user.type(screen.getByTestId('quick-input'), 'помыть машину');
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    expect(await screen.findByTestId('quick-label')).toHaveTextContent('дата контроля — сегодня');
    vi.useRealTimers();
  });

  it('Enter шлёт {text} на /tasks/quick, второй Enter на пустом поле навигирует', async () => {
    const user = userEvent.setup();
    mount();
    const input = screen.getByTestId('quick-input');
    await user.type(input, 'позвонить Василию');
    await user.keyboard('{Enter}');

    await screen.findByTestId('quick-done');
    const quickCall = calls.find((c) => c.url.includes('/tasks/quick'));
    expect(quickCall?.body).toEqual({ text: 'позвонить Василию' });
    expect(input).toHaveValue('');

    await user.keyboard('{Enter}');
    expect(await screen.findByTestId('card-route')).toBeInTheDocument();
  });

  it('422 показывает ошибку и не чистит поле', async () => {
    const user = userEvent.setup();
    mount();
    const input = screen.getByTestId('quick-input');
    await user.type(input, 'дубль');
    await user.keyboard('{Enter}');

    await screen.findByText('Задача с таким названием уже есть');
    expect(input).toHaveValue('дубль');
    expect(screen.queryByTestId('quick-done')).not.toBeInTheDocument();
  });
});
