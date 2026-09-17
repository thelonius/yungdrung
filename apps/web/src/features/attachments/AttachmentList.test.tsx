// SLICE2_SPEC.md §5.7: multipart с полем `step_id`, битая картинка убирает
// `<img>` не трогая строку, удаление оптимистичное с откатом на ошибке.
//
// Загрузка мокается на уровне `api.POST`, а не `globalThis.fetch`: jsdom и
// undici (fetch/Request в Node) несут разные классы `FormData`, и настоящий
// `Request` с телом-`FormData` в этой связке молча теряет multipart-кодировку
// (`Content-Type: text/plain` вместо `multipart/form-data; boundary=…`) —
// это расхождение тестового окружения, не браузера. `api.POST` — граница
// нашего кода: дальше тело реальному `fetch` отдаёт openapi-fetch, и это его
// зона ответственности, не наша.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import type { AttachmentInfo } from '@/api/client';
import { AttachmentList } from './AttachmentList';
import type { Owner } from './model';

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, api: { ...actual.api, POST: vi.fn() } };
});

function attachment(over: Partial<AttachmentInfo> = {}): AttachmentInfo {
  return {
    id: 1, filename: 'схема.png', mime: 'image/png', bytes: 2048,
    caption: null, added: '2026-09-08', step_id: null, url: '/вложение/1',
    ...over,
  };
}

function json(o: unknown, status = 200) {
  return new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } });
}

function mount(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.mocked(api.POST).mockReset();
});

describe('загрузка файла', () => {
  it('шлёт multipart с полем step_id', async () => {
    const owner: Owner = { kind: 'task', task_id: 5, step_id: 2 };
    let capturedBody: FormData | undefined;
    let capturedPath: unknown;
    let resolvePost: ((r: { data: unknown }) => void) | null = null;
    vi.mocked(api.POST).mockImplementation((_path, opts) => {
      capturedPath = _path;
      capturedBody = (opts as { body: FormData }).body;
      return new Promise((res) => { resolvePost = res; });
    });

    vi.stubGlobal('fetch', vi.fn(async () => json({ attachments: [] })));

    const user = userEvent.setup();
    mount(<AttachmentList owner={owner} />);
    await waitFor(() => expect(screen.queryByText('скан.pdf')).not.toBeInTheDocument());

    const file = new File(['x'], 'скан.pdf', { type: 'application/pdf' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);

    await waitFor(() => expect(capturedBody).toBeInstanceOf(FormData));
    expect(capturedPath).toBe('/api/v1/tasks/{task_id}/attachments');
    expect((capturedBody!.get('file') as File).name).toBe('скан.pdf');
    expect(capturedBody!.get('step_id')).toBe('2');

    resolvePost!({ data: { ok: true, attachment: attachment({ id: 9, filename: 'скан.pdf', mime: 'application/pdf' }) } });
    await screen.findByText('скан.pdf');
  });
});

describe('превью картинки', () => {
  it('onError убирает <img>, строка остаётся', () => {
    const owner: Owner = { kind: 'task', task_id: 5 };
    mount(<AttachmentList owner={owner} items={[attachment()]} />);

    const img = screen.getByRole('img');
    fireEvent.error(img);

    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByText('схема.png')).toBeInTheDocument();
  });
});

describe('удаление', () => {
  it('оптимистично убирает строку и откатывается на ошибке', async () => {
    const owner: Owner = { kind: 'task', task_id: 7 };
    let resolveDelete: ((r: Response) => void) | null = null;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const req = input instanceof Request ? input : new Request(String(input), init);
      if (req.method === 'DELETE') return new Promise<Response>((res) => { resolveDelete = res; });
      return json({ attachments: [attachment()] });
    }));

    const user = userEvent.setup();
    mount(<AttachmentList owner={owner} />);

    await screen.findByText('схема.png');
    await user.click(screen.getByTitle('Удалить вложение'));

    // Оптимистично: строка исчезла раньше, чем сервер ответил.
    await waitFor(() => expect(screen.queryByText('схема.png')).not.toBeInTheDocument());

    resolveDelete!(json({ ok: false, errors: [{ field: null, error: 'нет вложения 1' }] }, 404));

    await screen.findByText('схема.png');
    expect(screen.getByText('нет вложения 1')).toBeInTheDocument();
  });
});
