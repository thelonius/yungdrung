// Регрессия на находку ревью среза 2: `useTaskAttachments` (карточка) и
// `useUploadAttachment`/`useDeleteAttachment` (`AttachmentList`) должны
// делить одну запись кэша — иначе оптимистичная запись мутации уходит туда,
// откуда карточка её не видит, и список обновляется только явным `refetch`.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor, act } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { useUploadAttachment } from '@/features/attachments/useAttachments';
import { useTaskAttachments } from './useTask';

const calls: { method: string; url: string }[] = [];

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init);
    calls.push({ method: req.method, url: req.url });
    const json = (o: unknown, status = 200) =>
      new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } });
    if (req.method === 'GET' && req.url.includes('/attachments')) {
      return json({ attachments: [] });
    }
    if (req.method === 'POST' && req.url.includes('/attachments')) {
      return json({
        ok: true,
        attachment: { id: 9, filename: 'скрин.png', mime: 'image/png', bytes: 5, caption: null,
          added: '2026-09-15', step_id: null, url: '/вложение/9' },
      });
    }
    return json({ ok: false, errors: [{ field: null, error: 'нет такого' }] }, 404);
  }));
});

function wrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

describe('useTaskAttachments и useUploadAttachment — общая запись кэша', () => {
  it('загрузка через useUploadAttachment сразу видна useTaskAttachments, без второго GET', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const Wrapper = wrapper(qc);

    const list = renderHook(() => useTaskAttachments(5), { wrapper: Wrapper });
    await waitFor(() => expect(list.result.current.data).toEqual([]));

    const getsBeforeUpload = calls.filter((c) => c.method === 'GET').length;

    const upload = renderHook(() => useUploadAttachment({ kind: 'task', task_id: 5 }), { wrapper: Wrapper });
    await act(async () => {
      await upload.result.current.mutateAsync({ file: new File(['x'], 'скрин.png') });
    });

    await waitFor(() => expect(list.result.current.data).toHaveLength(1));
    expect(list.result.current.data?.[0].filename).toBe('скрин.png');
    // Оптимистичная запись мутации попала ровно в кэш, который читает
    // карточка — второго похода за списком не потребовалось.
    expect(calls.filter((c) => c.method === 'GET').length).toBe(getsBeforeUpload);
  });
});
