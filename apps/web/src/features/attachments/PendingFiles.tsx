// ЗАГЛУШКА (владелец F4, SLICE2_SPEC.md §5.6). Очередь файлов нового
// шаблона/задачи до первого сохранения. Сигнатура зафиксирована срезом 2 §5.1.
import type { JSX } from 'react';

export type Props = { files: File[]; onChange: (files: File[]) => void };

export function PendingFiles(props: Props): JSX.Element {
  void props;
  return <></>;
}
