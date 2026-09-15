// Регрессия на находку ревью среза 2: запись шаблона (`useSaveTemplate`)
// обязана положить вернувшийся `TemplateCard` прямо в кэш списка (§1.5
// SLICE2_SPEC.md) — раньше здесь был безусловный `invalidateTemplates`,
// то есть лишний `GET /api/v1/templates` после каждой записи.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor, act } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { TemplateCard, TemplateList } from '@/api/client';
import { TEMPLATES, useSaveTemplate, useTemplatesQuery } from './useTemplates';

const calls: { method: string; url: string }[] = [];

function card(over: Partial<TemplateCard> = {}): TemplateCard {
  return {
    ok: true, name: 'грант', tags: [], body: '', steps_count: 1, attachments_count: 0,
    steps: [{ position: 1, title: 'написать', offset_days: 0, time_of_day: null }],
    recurrence: null, ...over,
  };
}

function listFixture(): TemplateList {
  return { count: 1, templates: [card()] };
}

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    calls.push({ method: req.method, url: req.url });
    const json = (o: unknown, status = 200) =>
      new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } });
    if (req.method === 'GET' && req.url.endsWith('/api/v1/templates')) return json(listFixture());
    if (req.method === 'PUT' && req.url.includes('/api/v1/templates/')) {
      return json(card({ steps_count: 2, steps: [
        { position: 1, title: 'написать', offset_days: 0, time_of_day: null },
        { position: 2, title: 'отправить', offset_days: 5, time_of_day: null },
      ] }));
    }
    return json({ ok: false, errors: [{ field: null, error: 'нет такого' }] }, 404);
  }));
});

function wrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

describe('useSaveTemplate — кэш списка', () => {
  it('пишет вернувшийся TemplateCard в кэш списка без повторного GET', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const Wrapper = wrapper(qc);

    const list = renderHook(() => useTemplatesQuery(), { wrapper: Wrapper });
    await waitFor(() => expect(list.result.current.data).toBeDefined());
    const getsBefore = calls.filter((c) => c.method === 'GET').length;

    const save = renderHook(() => useSaveTemplate(), { wrapper: Wrapper });
    await act(async () => {
      await save.result.current.mutateAsync({ data: { name: 'грант', steps: [] }, expectName: 'грант' });
    });

    expect(qc.getQueryData<TemplateList>(TEMPLATES)?.templates[0].steps_count).toBe(2);
    // Ни одного нового `GET /api/v1/templates` — кэш обновлён напрямую.
    expect(calls.filter((c) => c.method === 'GET').length).toBe(getsBefore);
  });
});
