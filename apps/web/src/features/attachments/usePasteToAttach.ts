// ЗАГЛУШКА (владелец F4, SLICE2_SPEC.md §5.1/§5.7): Ctrl+V картинки к задаче
// целиком (`image.png` → `вставка-YYYY-MM-DD-HH-MM-SS.png`, map-client.md
// §2.2.4) появится здесь. Сигнатура зафиксирована.
import type { AttachmentInfo } from '@/api/client';
import type { Owner } from './model';

export function usePasteToAttach(
  owner: Owner | null,
  opts: { onDone: (a: AttachmentInfo) => void },
): void {
  void owner;
  void opts;
}
