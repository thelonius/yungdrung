// ЗАГЛУШКА (владелец F4, SLICE2_SPEC.md §5.1/§5.7): реальная загрузка идёт
// через `multipart/form-data` (`POST .../attachments`, `openapi-fetch` с
// `bodySerializer: (b) => b`, иначе тело уедет как JSON). Сигнатура
// зафиксирована, чтобы F2/F3 писали обработчики загрузки против неё уже сейчас.
import type { Owner } from './model';

export async function uploadPending(
  owner: Owner,
  files: File[],
): Promise<{ ok: File[]; failed: { file: File; error: string }[] }> {
  void owner;
  return { ok: [], failed: files.map((file) => ({ file, error: 'не реализовано' })) };
}
