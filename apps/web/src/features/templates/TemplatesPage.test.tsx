// Клавиатурный проход по шаблонам с подменённым API — тот же приём, что
// `FeedPage.test.tsx`. `AttachmentList`/`PendingFiles` (F4) остаются
// заглушками (`SLICE2_SPEC.md`: «в своих тестах мокай модуль»), поэтому
// «Файлы» здесь не проверяются — это часть F4.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HotkeysProvider } from 'react-hotkeys-hook';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { TemplateList } from '@/api/client';
import { ToasterProvider } from '@/ui/Toaster';
import { TemplatesPage } from './TemplatesPage';

vi.mock('@/features/attachments/AttachmentList', () => ({ AttachmentList: () => null }));
vi.mock('@/features/attachments/PendingFiles', () => ({ PendingFiles: () => null }));

const calls: { method: string; url: string; body?: unknown }[] = [];

function listFixture(): TemplateList {
  return {
    count: 1,
    templates: [{
      ok: true, name: 'грант', tags: ['работа'], body: '', steps_count: 2, attachments_count: 0,
      steps: [
        { position: 1, title: 'написать', offset_days: 0, time_of_day: null },
        { position: 2, title: 'отправить', offset_days: 5, time_of_day: null },
      ],
      recurrence: null,
    }],
  };
}

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    // Имя шаблона кириллицей уходит в URL percent-encoded (`openapi-fetch`
    // не трогает регистр экранирования) — маршрутизация мока и проверки в
    // тестах сравнивают декодированный путь, а не сырую строку с `%D0%B3…`.
    const url = decodeURIComponent(req.url);
    const method = req.method;
    const text = method === 'GET' || method === 'DELETE' ? '' : await req.text();
    calls.push({ method, url, body: text ? JSON.parse(text) : undefined });
    const json = (o: unknown, status = 200) =>
      new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/api/v1/templates/грант/preview')) {
      const start = new URL(url).searchParams.get('start');
      return json({
        ok: true, start: '2026-09-16', start_text: start || 'сегодня',
        steps: [
          { position: 1, title: 'написать', offset_days: 0, control_date: '16.09', control_text: `16.09 (${start ?? 'default'})`, weekday: 'ср', on_weekend: false, show_at: '2026-09-16T09:00:00' },
          { position: 2, title: 'отправить', offset_days: 5, control_date: '21.09', control_text: '21.09', weekday: 'пн', on_weekend: false, show_at: '2026-09-21T09:00:00' },
        ],
      });
    }
    if (url.endsWith('/api/v1/templates') && method === 'GET') return json(listFixture());
    if (url.includes('/instantiate')) return json({ ok: true, task_id: 42, task: 'грант', template: 'грант', steps: 2, attachments: 0, task_status: 'due' });
    if (url.includes('/api/v1/templates/preview')) return json({ ok: true, start: '2026-09-16', start_text: 'сегодня', steps: [] });
    if (url.endsWith('/api/v1/templates/грант') && method === 'DELETE') return json({ ok: true, template: 'грант', deleted: true });
    return json({ ok: false, errors: [{ field: null, error: 'нет такого' }] }, 404);
  }));
});

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={['/шаблоны']}>
      <QueryClientProvider client={qc}>
        <HotkeysProvider>
          <ToasterProvider>
            <TemplatesPage />
          </ToasterProvider>
        </HotkeysProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('список шаблонов', () => {
  it('показывает карточку с метаданными из ядра', async () => {
    mount();
    await screen.findByText('грант');
    expect(screen.getByText('2 шага · работа')).toBeInTheDocument();
    expect(screen.getByText('без повторения')).toBeInTheDocument();
  });

  it('Enter на карточке открывает диалог развёртывания, предпросмотр перерисовывается при смене даты', async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText('грант');
    await user.keyboard('{Enter}');
    await screen.findByText('Завести задачу из «грант»');
    await waitFor(() => expect(calls.some((c) => c.url.includes('/preview'))).toBe(true));

    const before = calls.filter((c) => c.url.includes('/templates/грант/preview')).length;
    const input = screen.getByLabelText('Дата старта');
    await user.type(input, 'завтра');
    await waitFor(() => {
      const after = calls.filter((c) => c.url.includes('/templates/грант/preview')).length;
      expect(after).toBeGreaterThan(before);
    }, { timeout: 2000 });
    // Последний запрос несёт то, что набрали.
    const last = calls.filter((c) => c.url.includes('/templates/грант/preview')).at(-1);
    expect(last?.url).toContain('start=');
  });

  it('e открывает форму правки с разложенными промежутками (обратная раскладка §5.6)', async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText('грант');
    await user.keyboard('e');
    await screen.findByText('Правка «грант»');
    // Сохранённые сдвиги [0, 5] раскладываются в промежутки [0, 5].
    expect(screen.getByDisplayValue('написать')).toBeInTheDocument();
    const offsets = screen.getAllByLabelText(/дн\. от старта|через, дн\./);
    expect((offsets[0] as HTMLInputElement).value).toBe('0');
    expect((offsets[1] as HTMLInputElement).value).toBe('5');
  });

  it('x вооружает удаление, второй шаг стирает шаблон', async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText('грант');
    await user.keyboard('x');
    const confirm = await screen.findByText('Точно? Enter / Esc');
    await user.click(confirm);
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
  });
});
