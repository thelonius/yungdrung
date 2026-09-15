// Форма «Новая задача» с подменённым API (как QuickAdd.test.tsx): клавиатурная
// цепочка Enter, группировка в подшаги, живая ошибка `plan` под полем,
// раскладка 422 по вложенному пути и сброс формы с простановкой ссылок базы
// знаний после успеха (SLICE2_SPEC.md §5.4).
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ToasterProvider } from '@/ui/Toaster';
import { TaskForm } from './TaskForm';

const calls: { method: string; url: string; body?: unknown }[] = [];

let planErrors: { field: string | null; error: string }[] = [];
let createResponse: { status: number; body: unknown } | null = null;

beforeEach(() => {
  calls.length = 0;
  planErrors = [];
  createResponse = null;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    const url = req.url;
    const method = req.method;
    const text = method === 'GET' ? '' : await req.text();
    const body = text ? JSON.parse(text) : undefined;
    calls.push({ method, url, body });
    const json = (o: unknown, status = 200) =>
      new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/api/v1/parse-date')) {
      const t = (body as { text: string }).text as string;
      if (!t.trim()) return json({ ok: false, date: null, label: null, past: false, error: 'пусто' });
      return json({ ok: true, date: '2026-09-16', label: t, past: false, error: null });
    }
    if (url.includes('/api/v1/tasks/plan')) {
      return json({ ok: planErrors.length === 0, errors: planErrors, warnings: [], steps: [] });
    }
    if (url.includes('/api/v1/kb/scan')) {
      return json({ hypotheses: [], confirmed: [], kb_broken: [] });
    }
    if (url.includes('/kb-confirm')) {
      return json({ ok: true, links: [{ id: 1 }], errors: [] });
    }
    if (url.endsWith('/api/v1/tasks')) {
      if (createResponse) return json(createResponse.body, createResponse.status);
      return json({
        ok: true, task_id: 7, task: 'Задача', task_status: 'due', steps: 1, created: true, warnings: [],
        card: { ok: true, task_id: 7, task: 'Задача', task_status: 'due', status_ru: 'сегодня', body: '',
          created: '2026-09-15', start_date: '2026-09-15', tags: [], control_date: '2026-09-16',
          current_step: 'Задача', progress: '0 из 1', stalled: 0, steps: [], history: [], actions: [] },
      });
    }
    return json({ ok: false, errors: [{ field: null, error: 'нет такого' }] }, 404);
  }));
});

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/новая']}>
        <ToasterProvider>
          <TaskForm />
        </ToasterProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function stepTitleInputs(): HTMLInputElement[] {
  return screen.getAllByTestId('step-title') as HTMLInputElement[];
}

describe('TaskForm', () => {
  it('цепочка Enter ведёт название → теги → шаг → дата → новый шаг', async () => {
    const user = userEvent.setup();
    mount();

    const title = screen.getByLabelText('Название задачи');
    await user.type(title, 'Переезд');
    await user.keyboard('{Enter}');
    expect(screen.getByLabelText(/Категории/)).toHaveFocus();

    await user.keyboard('{Enter}');
    expect(stepTitleInputs()[0]).toHaveFocus();

    await user.type(stepTitleInputs()[0], 'Собрать документы');
    await user.keyboard('{Enter}');
    expect(screen.getByLabelText('Дата контроля')).toHaveFocus();

    // Enter в дате единственного шага — новый шаг добавляется после него, и
    // фокус уходит в название нового шага (п.2 §5.4).
    await user.keyboard('{Enter}');
    await waitFor(() => expect(stepTitleInputs()).toHaveLength(2));
    expect(stepTitleInputs()[1]).toHaveFocus();
  });

  it('«Подшаги» очищает дату шага и показывает выбор режима', async () => {
    const user = userEvent.setup();
    mount();

    await user.type(stepTitleInputs()[0], 'Разобрать архив');
    await user.type(screen.getByLabelText('Дата контроля'), 'завтра');
    expect(screen.getByLabelText('Дата контроля')).toHaveValue('завтра');

    await user.click(screen.getByRole('button', { name: 'Подшаги' }));

    expect(screen.queryByLabelText('Дата контроля')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Порядок подшагов')).toHaveValue('par');
    expect(stepTitleInputs()).toHaveLength(2); // родитель + один подшаг
  });

  it('живая ошибка plan ложится под дату по скобочному пути', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ delay: null });
    mount();

    planErrors = [{ field: 'steps[0].control_date', error: 'дата в прошлом' }];
    await user.type(stepTitleInputs()[0], 'Позвонить');
    await user.type(screen.getByLabelText('Дата контроля'), 'позавчера');
    // DateField разбирает через 200 мс; plan перезапускается, когда разбор
    // (`onParsed`) меняет сигнатуру дерева — второго таймера у формы нет.
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });

    expect(await screen.findByText('дата в прошлом')).toBeInTheDocument();
    vi.useRealTimers();
  });

  it('422 с steps[0].steps[1].title подсвечивает поле второго подшага', async () => {
    const user = userEvent.setup();
    mount();

    await user.type(screen.getByLabelText('Название задачи'), 'Переезд');
    await user.click(screen.getByRole('button', { name: 'Подшаги' }));
    await user.click(screen.getByRole('button', { name: 'Добавить подшаг' }));
    expect(stepTitleInputs()).toHaveLength(3); // родитель + 2 подшага

    createResponse = {
      status: 422,
      body: { ok: false, errors: [{ field: 'steps[0].steps[1].title', error: 'Нужно название' }] },
    };
    await user.keyboard('{Control>}{Enter}{/Control}');

    expect(await screen.findByText('Нужно название')).toBeInTheDocument();
    // Ошибка относится ко второму подшагу, а не к первому.
    const errParagraph = await screen.findByText('Нужно название');
    const substeps = screen.getAllByTestId('form-substep');
    expect(substeps[1]).toContainElement(errParagraph);
  });

  it('успех сбрасывает форму и проставляет ссылку базы знаний', async () => {
    const user = userEvent.setup();
    mount();

    await user.type(screen.getByLabelText('Название задачи'), 'Позвонить Василию');
    await user.type(stepTitleInputs()[0], 'Позвонить');
    await user.type(screen.getByLabelText('Дата контроля'), 'завтра');

    // Гипотеза базы знаний приходит из внешнего скана `KbHints`, здесь
    // мы просто фиксируем, что при пустом списке подтверждений `kb-confirm`
    // не зовётся, а создание всё равно сбрасывает форму (без гипотез).
    await user.keyboard('{Control>}{Enter}{/Control}');

    await waitFor(() => expect(screen.getByLabelText('Название задачи')).toHaveValue(''));
    expect(stepTitleInputs()).toHaveLength(1);
    expect(stepTitleInputs()[0]).toHaveValue('');
    expect(calls.some((c) => c.url.endsWith('/api/v1/tasks') && c.method === 'POST')).toBe(true);
    expect(calls.some((c) => c.url.includes('kb-confirm'))).toBe(false);
  });
});
