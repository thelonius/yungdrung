// Карточка задачи с подменённым API: отметка с клавиатуры, окно контроля,
// сохранение с пропавшим шагом (кнопка `force`), 422 раскрывает редактор
// нужного шага, `ConfirmButton` требует второго подтверждения, переименование
// показывает `renamed_from` в тосте (SLICE2_SPEC.md §5.5, «Vitest:»).
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HotkeysProvider } from 'react-hotkeys-hook';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CardStep, FeedRow, TaskCard } from '@/api/client';
import { ToasterProvider } from '@/ui/Toaster';
import { TaskCardPage } from './TaskCardPage';

function feedRow(task_id: number, step: number, title: string): FeedRow {
  return {
    task_id, task: 'Позвонить Василию', step, title, group: null, note: null,
    control_at: '2026-09-08', show_at: '2026-09-08T09:00:00', state: 'due', postponed: 0,
    stalled: false, tags: [], last_reason: null, actions: ['done', 'notdone', 'defer', 'fail', 'skip'],
  };
}

function stepFixture(id: number, title: string, extra: Partial<CardStep> = {}): CardStep {
  return {
    id, title, status: 'pending', start_date: null, control_date: `2026-09-0${id} 09:00`,
    completed_date: null, note: null, mode: null, closed: false, active: true, stalled: 0, state: 'due',
    row: feedRow(1, id, title), actions: ['done', 'notdone', 'defer', 'fail', 'skip'], steps: [],
    ...extra,
  };
}

function cardFixture(extra: Partial<TaskCard> = {}): TaskCard {
  return {
    ok: true, task_id: 1, task: 'Позвонить Василию', task_status: 'due', status_ru: 'ожидает',
    body: 'Заметка', created: '2026-09-01', start_date: '2026-09-01', tags: ['важное'],
    cancelled: false, cancelled_reason: null, template_name: null, cycle_key: null,
    control_date: '2026-09-08 09:00', current_step: 'Позвонить', progress: '0/2', stalled: 0,
    steps: [stepFixture(1, 'Позвонить'), stepFixture(2, 'Написать письмо')],
    history: [],
    actions: ['close', 'cancel', 'delete', 'to_template'],
    ...extra,
  };
}

let cardState: TaskCard;
const calls: { method: string; url: string; body?: unknown }[] = [];
let resolveMark: ((r: unknown) => void) | null = null;

function json(o: unknown, status = 200) {
  return new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } });
}

beforeEach(() => {
  calls.length = 0;
  resolveMark = null;
  cardState = cardFixture();
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    const url = req.url;
    const method = req.method;
    const text = method === 'GET' || method === 'DELETE' ? '' : await req.text();
    calls.push({ method, url, body: text ? JSON.parse(text) : undefined });

    if (url.includes('/reasons')) return json({ reasons: ['не было времени'] });
    if (url.includes('/tasks/plan')) return json({ ok: true, errors: [], warnings: [], steps: [] });
    if (url.includes('/attachments')) return json({ attachments: [] });
    if (url.includes('/mark')) {
      const r = await new Promise((res) => { resolveMark = res; });
      return json(r);
    }
    if (url.includes('/undo')) {
      return json({
        ok: true, op: 'done', task_id: 1, task: cardState.task, step: 1, status: 'pending',
        task_status: 'due', dates_assigned: [], stalled: 0, undone: 'done', control_date: null,
        row: feedRow(1, 1, 'Позвонить'), counts: { overdue: 0, today: 1, waiting: 0 },
      });
    }
    if (method === 'PUT' && url.match(/\/tasks\/\d+$/)) {
      const body = JSON.parse(text) as { force: boolean; title: string };
      if (!body.force) {
        return json({ ok: false, errors: [{ field: 'steps', error: 'Шаг 2 пропал из данных — сначала «снять», не убирать так' }] }, 422);
      }
      cardState = { ...cardState, task: body.title, steps: [cardState.steps[0]] };
      return json({
        ok: true, task_id: 1, task: cardState.task, task_status: 'due', steps: 1, created: false,
        renamed_from: body.title !== 'Позвонить Василию' ? 'Позвонить Василию' : null,
        warnings: [], card: cardState,
      });
    }
    if (method === 'GET' && url.match(/\/tasks\/\d+$/)) return json(cardState);
    return json({ ok: false, errors: [{ field: null, error: 'нет такого' }] }, 404);
  }));
});

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={['/задача/1']}>
      <QueryClientProvider client={qc}>
        <HotkeysProvider>
          <ToasterProvider>
            <Routes>
              <Route path="/задача/:id" element={<TaskCardPage />} />
            </Routes>
          </ToasterProvider>
        </HotkeysProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('карточка задачи', () => {
  it('d на листе под фокусом зовёт /mark и после ответа инвалидирует карточку', async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByDisplayValue('Позвонить Василию');
    expect(screen.getAllByTestId('card-step')).toHaveLength(2);

    await user.keyboard('d');
    const markCall = calls.find((c) => c.url.includes('/mark'));
    expect(markCall?.url).toContain('/api/v1/tasks/1/steps/1/mark');
    expect(markCall?.body).toEqual({ op: 'done', reason: null, to: null });

    cardState = { ...cardState, steps: [stepFixture(1, 'Позвонить', { status: 'done', closed: true, active: false }), cardState.steps[1]] };
    resolveMark!({
      ok: true, op: 'done', task_id: 1, task: cardState.task, step: 1, status: 'done',
      task_status: 'due', next_step_id: 2, next_step_title: 'Написать письмо', dates_assigned: [],
      next_check: null, stalled: 0, hint: null, undone: null, control_date: null, row: null,
      counts: { overdue: 0, today: 1, waiting: 0 },
    });

    await screen.findByText('Сделано. Следующий шаг — Написать письмо');
    // Инвалидация ['task', 1] тянет перечитывание — второй GET карточки после отметки.
    await waitFor(() => expect(calls.filter((c) => c.method === 'GET' && c.url.match(/\/tasks\/1$/)).length).toBeGreaterThanOrEqual(2));
  });

  it('Ctrl+Enter сохраняет даже из текстового поля (заметка)', async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByDisplayValue('Позвонить Василию');

    const body = screen.getByLabelText('Заметка');
    await user.click(body);
    await user.keyboard('{Control>}{Enter}{/Control}');

    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
  });

  it('t открывает окно контроля с названием шага под фокусом', async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByDisplayValue('Позвонить Василию');

    await user.keyboard('t');
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Позвонить')).toBeInTheDocument();
    expect(await screen.findByLabelText('Новая дата контроля')).toBeInTheDocument();
  });

  it('сохранение с пропавшим шагом показывает кнопку force, второй запрос несёт force: true', async () => {
    mount();
    await screen.findByDisplayValue('Позвонить Василию');

    await userEvent.setup().click(screen.getByTestId('save-card'));

    await screen.findByText(/пропал из данных/);
    const forceBtn = await screen.findByRole('button', { name: /Всё равно сохранить/ });
    await userEvent.setup().click(forceBtn);

    await waitFor(() => {
      const puts = calls.filter((c) => c.method === 'PUT');
      expect(puts.length).toBeGreaterThanOrEqual(2);
      expect((puts[puts.length - 1].body as { force: boolean }).force).toBe(true);
    });
  });

  it('422 steps[1].control_date раскрывает редактор второго шага', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const req = input instanceof Request ? input : new Request(String(input), init);
      const url = req.url;
      const method = req.method;
      if (url.includes('/reasons')) return json({ reasons: [] });
      if (url.includes('/tasks/plan')) return json({ ok: true, errors: [], warnings: [], steps: [] });
      if (url.includes('/attachments')) return json({ attachments: [] });
      if (method === 'PUT' && url.match(/\/tasks\/\d+$/)) {
        return json({ ok: false, errors: [{ field: 'steps[1].control_date', error: 'Не разобрал дату' }] }, 422);
      }
      if (method === 'GET' && url.match(/\/tasks\/\d+$/)) return json(cardFixture());
      return json({ ok: false, errors: [] }, 404);
    }));
    const user = userEvent.setup();
    mount();
    await screen.findByDisplayValue('Позвонить Василию');

    await user.click(screen.getByTestId('save-card'));
    await screen.findByText('Не разобрал дату');
    expect(screen.getByLabelText('Дата контроля')).toBeInTheDocument();
  });

  it('ConfirmButton требует второго подтверждения перед удалением', async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByDisplayValue('Позвонить Василию');

    const del = screen.getByRole('button', { name: 'Удалить' });
    await user.click(del);
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    await screen.findByRole('button', { name: /Точно/ });
    await user.click(screen.getByRole('button', { name: /Точно/ }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
  });

  it('переименование показывает тост с renamed_from', async () => {
    const user = userEvent.setup();
    mount();
    const titleInput = await screen.findByDisplayValue('Позвонить Василию');
    await user.clear(titleInput);
    await user.type(titleInput, 'Позвонить Петру');

    await user.click(screen.getByTestId('save-card'));
    await screen.findByText(/пропал из данных/);
    await user.click(screen.getByRole('button', { name: /Всё равно сохранить/ }));

    await screen.findByText('Сохранено, переименовано в «Позвонить Петру»');
  });
});
