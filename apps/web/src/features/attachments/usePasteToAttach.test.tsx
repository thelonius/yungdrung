// SLICE2_SPEC.md §5.7, map-client.md §2.2.4: вставленная картинка без
// собственного имени («image.png» из буфера) получает `вставка-…png`;
// `api.POST` мокается по той же причине, что в `AttachmentList.test.tsx` —
// jsdom и undici расходятся в классе `FormData`, реальный `fetch` тут не
// нужен для проверки нашей сборки формы.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import type { AttachmentInfo } from '@/api/client';
import { ToasterProvider } from '@/ui/Toaster';
import { usePasteToAttach } from './usePasteToAttach';
import type { Owner } from './model';

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, api: { ...actual.api, POST: vi.fn() } };
});

function attachment(over: Partial<AttachmentInfo> = {}): AttachmentInfo {
  return {
    id: 3, filename: 'вставка.png', mime: 'image/png', bytes: 100,
    caption: null, added: '2026-09-08', step_id: null, url: '/вложение/3',
    ...over,
  };
}

function Harness({ owner, onDone }: { owner: Owner | null; onDone: (a: AttachmentInfo) => void }) {
  usePasteToAttach(owner, { onDone });
  return <div>harness</div>;
}

function mount(owner: Owner | null, onDone: (a: AttachmentInfo) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToasterProvider>
        <Harness owner={owner} onDone={onDone} />
      </ToasterProvider>
    </QueryClientProvider>,
  );
}

// jsdom не реализует `DataTransfer`/`ClipboardEvent` — обычный `Event` с
// подставленным `clipboardData` даёт слушателю ровно то, что он читает
// (`e.clipboardData?.files`), без похода в неполный DOM-полифилл.
function pasteEventWith(file: File): Event {
  const event = new Event('paste', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'clipboardData', { value: { files: [file] } });
  return event;
}

beforeEach(() => {
  vi.mocked(api.POST).mockReset();
});

describe('usePasteToAttach', () => {
  it('переименовывает безымянную картинку и зовёт onDone', async () => {
    let capturedBody: FormData | undefined;
    vi.mocked(api.POST).mockImplementation((_path, opts) => {
      capturedBody = (opts as { body: FormData }).body;
      return Promise.resolve({ data: { ok: true, attachment: attachment() } });
    });

    const onDone = vi.fn();
    mount({ kind: 'task', task_id: 4 }, onDone);

    document.dispatchEvent(pasteEventWith(new File(['x'], 'image.png', { type: 'image/png' })));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(onDone).toHaveBeenCalledWith(attachment());
    const uploaded = capturedBody!.get('file') as File;
    expect(uploaded.name).toMatch(/^вставка-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\.png$/);
    await screen.findByText('Картинка прикреплена');
  });

  it('своё имя файла не трогает', async () => {
    let capturedBody: FormData | undefined;
    vi.mocked(api.POST).mockImplementation((_path, opts) => {
      capturedBody = (opts as { body: FormData }).body;
      return Promise.resolve({ data: { ok: true, attachment: attachment({ filename: 'диаграмма.png' }) } });
    });

    const onDone = vi.fn();
    mount({ kind: 'task', task_id: 4 }, onDone);

    document.dispatchEvent(pasteEventWith(new File(['x'], 'диаграмма.png', { type: 'image/png' })));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect((capturedBody!.get('file') as File).name).toBe('диаграмма.png');
  });

  it('без владельца не подписывается вовсе', () => {
    const onDone = vi.fn();
    mount(null, onDone);

    document.dispatchEvent(pasteEventWith(new File(['x'], 'a.png', { type: 'image/png' })));

    expect(onDone).not.toHaveBeenCalled();
    expect(api.POST).not.toHaveBeenCalled();
  });
});
