// `uploadPending` — очередь файлов новой задачи/шаблона после первого
// сохранения (SLICE2_SPEC.md §5.6/§5.7): часть файлов может не пройти
// (`file` за лимитом, `filename` пустое), список неудачных возвращается
// вызывающему для подписи «Шаблон сохранён, но файлы не прикрепились: …».
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import type { AttachmentInfo } from '@/api/client';
import { uploadPending } from './useAttachments';

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, api: { ...actual.api, POST: vi.fn() } };
});

function attachment(over: Partial<AttachmentInfo> = {}): AttachmentInfo {
  return {
    id: 1, filename: 'a.png', mime: 'image/png', bytes: 10,
    caption: null, added: '2026-09-08', step_id: null, url: '/вложение/1',
    ...over,
  };
}

beforeEach(() => {
  vi.mocked(api.POST).mockReset();
});

describe('uploadPending', () => {
  it('делит файлы на прошедшие и упавшие, не теряя порядок', async () => {
    const ok = new File(['x'], 'ok.png');
    const bad = new File(['x'], 'bad.png');
    vi.mocked(api.POST).mockImplementation((_path, opts) => {
      const file = (opts as { body: FormData }).body.get('file') as File;
      if (file.name === 'bad.png') {
        return Promise.resolve({ error: { ok: false, errors: [{ field: 'file', error: 'Файл больше 15 МБ' }] } });
      }
      return Promise.resolve({ data: { ok: true, attachment: attachment({ filename: file.name }) } });
    });

    const result = await uploadPending({ kind: 'template', name: 'Отчёт' }, [ok, bad]);

    expect(result.ok).toEqual([ok]);
    expect(result.failed).toEqual([{ file: bad, error: 'Файл больше 15 МБ' }]);
  });
});
