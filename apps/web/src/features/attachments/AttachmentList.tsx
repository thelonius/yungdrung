// Список вложений владельца (SLICE2_SPEC.md §5.7): без `items` грузит сам по
// `owner` (кэш общий на задачу — см. `useAttachments.ts`), с `items` рисует
// переданное и с ним же мутирует, полагаясь на общий ключ кэша, чтобы
// оптимистичное удаление/добавление сразу увидел и владелец `items`.
import { useState } from 'react';
import type { ChangeEvent, JSX } from 'react';
import type { AttachmentInfo } from '@/api/client';
import { ApiError } from '@/api/errors';
import { sizeText } from './model';
import type { Owner } from './model';
import { useAttachmentsQuery, useDeleteAttachment, useUploadAttachment } from './useAttachments';
import styles from './AttachmentList.module.css';

export type Props = {
  owner: Owner;
  items?: AttachmentInfo[];
  filter?: (a: AttachmentInfo) => boolean;
  onChanged?: () => void;
  compact?: boolean;
};

function messageOf(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export function AttachmentList(props: Props): JSX.Element {
  const { owner, items, filter, onChanged, compact } = props;
  const query = useAttachmentsQuery(owner, { enabled: items === undefined });
  const del = useDeleteAttachment(owner);
  const add = useUploadAttachment(owner);
  const [error, setError] = useState<string | null>(null);

  const source = items ?? query.data?.attachments ?? [];
  const list = filter ? source.filter(filter) : source;

  async function handleAdd(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setError(null);
    try {
      await add.mutateAsync({ file });
      onChanged?.();
    } catch (err) {
      setError(messageOf(err, 'не получилось прикрепить файл'));
    }
  }

  async function handleDelete(id: number) {
    setError(null);
    try {
      await del.mutateAsync(id);
      onChanged?.();
    } catch (err) {
      setError(messageOf(err, 'не получилось удалить'));
    }
  }

  return (
    <div className={`${styles.root} ${compact ? styles.compact : ''}`}>
      {list.length > 0 && (
        <ul className={styles.list}>
          {list.map((a) => (
            <AttachmentRow key={a.id} attachment={a} onDelete={() => void handleDelete(a.id)} />
          ))}
        </ul>
      )}
      <label className={styles.add}>
        + Файл
        <input type="file" hidden onChange={(e) => void handleAdd(e)} />
      </label>
      {error && <p className={styles.error}>{error}</p>}
    </div>
  );
}

function AttachmentRow({
  attachment,
  onDelete,
}: {
  attachment: AttachmentInfo;
  onDelete: () => void;
}): JSX.Element {
  // Битый или потерянный на диске файл не должен оставлять сломанную иконку:
  // строка со ссылкой сама по себе рабочая (перенесено из static/task.js).
  const [broken, setBroken] = useState(false);
  const isImage = attachment.mime.startsWith('image/') && !broken;

  return (
    <li className={`${styles.row} ${isImage ? styles.hasThumb : ''}`} data-testid="attachment-row">
      {isImage && (
        <img
          className={styles.thumb}
          src={attachment.url}
          alt={attachment.caption ?? attachment.filename}
          loading="lazy"
          onError={() => setBroken(true)}
        />
      )}
      <a href={attachment.url} target="_blank" rel="noopener" title={attachment.caption ?? undefined}>
        {attachment.filename}
      </a>
      <span className={styles.size}>{sizeText(attachment.bytes)}</span>
      <button type="button" className={styles.remove} title="Удалить вложение" onClick={onDelete}>×</button>
    </li>
  );
}
