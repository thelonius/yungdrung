// Ctrl+V картинки к задаче целиком (SLICE2_SPEC.md §5.7, map-client.md
// §2.2.4): скриншот из буфера прикрепляется без диалога «сохранить на диск →
// выбрать файл». Текстовая вставка сюда не попадает — в `clipboardData` нет
// файлов, слушатель выходит сразу.
import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { AttachmentInfo } from '@/api/client';
import { ApiError } from '@/api/errors';
import { useToaster } from '@/ui/toasterContext';
import { attachmentsKey, uploadAttachment } from './useAttachments';
import type { Owner } from './model';

// Браузер отдаёт вставленной картинке имя «image.png» без исключения — по
// нему и отличаем «имени нет» от настоящего имени файла.
const CLIPBOARD_DEFAULT_NAME = 'image.png';

function generatedPasteName(): string {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
  return `вставка-${stamp}.png`;
}

function namedForPaste(file: File): File {
  if (file.name && file.name !== CLIPBOARD_DEFAULT_NAME) return file;
  return new File([file], generatedPasteName(), { type: file.type });
}

export function usePasteToAttach(
  owner: Owner | null,
  opts: { onDone: (a: AttachmentInfo) => void },
): void {
  const qc = useQueryClient();
  const toaster = useToaster();
  // `onDone` меняется на каждой перерисовке вызывающего компонента; ref
  // держит актуальный колбэк без пересборки слушателя на каждый рендер.
  const onDoneRef = useRef(opts.onDone);
  onDoneRef.current = opts.onDone;

  useEffect(() => {
    if (!owner) return;
    // Константа, а не сам параметр: замыкание `onPaste` должно видеть тип
    // `Owner` без `null` без ручных утверждений (`!`) на каждое обращение.
    const active = owner;

    function onPaste(e: ClipboardEvent) {
      const files = [...(e.clipboardData?.files ?? [])];
      if (!files.length) return;
      e.preventDefault();
      void (async () => {
        let attached = 0;
        for (const file of files) {
          try {
            const a = await uploadAttachment(active, namedForPaste(file));
            attached += 1;
            onDoneRef.current(a);
          } catch (err) {
            toaster.push({
              title: err instanceof ApiError ? err.message : 'не получилось прикрепить файл',
              tone: 'error',
            });
          }
        }
        if (attached > 0) {
          qc.invalidateQueries({ queryKey: attachmentsKey(active) });
          toaster.push({
            title: attached === 1 ? 'Картинка прикреплена' : `Прикреплено файлов: ${attached}`,
          });
        }
      })();
    }

    document.addEventListener('paste', onPaste);
    return () => document.removeEventListener('paste', onPaste);
  }, [owner, qc, toaster]);
}
