// Регрессия на находку ревью среза 2: `RecurrenceForm` нигде не монтировался
// в тестах — round-trip скрытых полей (`holiday_shift`/`lead_days`/`until`/
// `paused`) был проверен только на чистых функциях (`model.test.ts`), не на
// проводке виджетов (SLICE2_SPEC.md §5.6, «Vitest: round-trip правила через
// форму сохраняет…»).
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { TemplateCard } from '@/api/client';
import { ToasterProvider } from '@/ui/Toaster';
import { RecurrenceForm } from './RecurrenceForm';

const calls: { method: string; url: string; body?: unknown }[] = [];

function templateFixture(): TemplateCard {
  return {
    ok: true, name: 'грант', tags: [], body: '', steps_count: 1, attachments_count: 0,
    steps: [{ position: 1, title: 'написать', offset_days: 0, time_of_day: null }],
    recurrence: {
      anchor: '2026-09-01', freq: 'weekly', interval: 2, byweekday: [1], bymonthday: [],
      bysetpos: [], bymonth: [], holiday_shift: 'next_workday', lead_days: 3,
      until: '2027-01-01', paused: true, description: 'каждые 2 недели по вторникам',
    },
  };
}

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    const text = req.method === 'GET' ? '' : await req.text();
    const body = text ? JSON.parse(text) : undefined;
    calls.push({ method: req.method, url: req.url, body });
    const json = (o: unknown, status = 200) =>
      new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } });
    if (req.url.includes('/recurrence/preview')) {
      return json({ ok: true, description: 'предпросмотр', anchor: '2026-09-01', preview: [], errors: [] });
    }
    if (req.url.includes('/recurrence') && req.method === 'PUT') {
      return json({ ...templateFixture(), recurrence: { ...templateFixture().recurrence, ...(body as { rule?: object })?.rule } });
    }
    return json({ ok: false, errors: [{ field: null, error: 'нет такого' }] }, 404);
  }));
});

function mount(template: TemplateCard) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToasterProvider>
        <RecurrenceForm template={template} onClose={() => {}} />
      </ToasterProvider>
    </QueryClientProvider>,
  );
}

describe('RecurrenceForm — round-trip скрытых полей', () => {
  it('правка одного виджета (Каждые) не трогает holiday_shift/lead_days/until/paused', async () => {
    const user = userEvent.setup();
    mount(templateFixture());

    // Поле контролируемое (`value={rule.interval ?? 1}`) — простой `clear()`
    // снимает выделение раньше, чем React успевает применить пустое
    // значение, и компонент снова показывает старое число; выделяем текст
    // тройным кликом и печатаем поверх него.
    const interval = screen.getByLabelText('Каждые');
    await user.tripleClick(interval);
    await user.keyboard('3');

    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.includes('/recurrence'))).toBe(true));
    const put = calls.find((c) => c.method === 'PUT' && c.url.includes('/recurrence'));
    const rule = put?.body as Record<string, unknown>;
    expect(rule.interval).toBe(3);
    expect(rule.holiday_shift).toBe('next_workday');
    expect(rule.lead_days).toBe(3);
    expect(rule.until).toBe('2027-01-01');
    expect(rule.paused).toBe(true);
  });
});
