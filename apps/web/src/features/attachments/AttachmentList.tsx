// ЗАГЛУШКА (владелец F4, SLICE2_SPEC.md §5.7). Сигнатура зафиксирована срезом
// 2 §5.1: карточка (F2) и шаблоны (F3) пишут против неё уже сейчас —
// `items` не задан → сам грузит по `owner`, задан → показывает переданное
// (карточка грузит список один раз и раздаёт по шагам через `filter`).
import type { JSX } from 'react';
import type { AttachmentInfo } from '@/api/client';
import type { Owner } from './model';

export type Props = {
  owner: Owner;
  items?: AttachmentInfo[];
  filter?: (a: AttachmentInfo) => boolean;
  onChanged?: () => void;
  compact?: boolean;
};

export function AttachmentList(props: Props): JSX.Element {
  void props;
  return <></>;
}
