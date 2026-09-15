// Шапка карточки — п.1–2 §5.5 SLICE2_SPEC.md: редактируемое название, плашка
// статуса, срок, прогресс; три деструктивные кнопки через `ConfirmButton`
// (Р15 — без `confirm()`), «Сохранить как шаблон» — обычная кнопка.
import { useState } from 'react';
import type { JSX, RefObject } from 'react';
import type { TaskCard } from '@/api/client';
import { ConfirmButton } from '@/ui/ConfirmButton';
import { deadlineText, progressText } from './model';
import styles from './TaskCardPage.module.css';

type Props = {
  card: TaskCard;
  title: string;
  titleErr?: string;
  onTitleChange: (v: string) => void;
  titleRef?: RefObject<HTMLInputElement | null>;
  onClose: () => void;
  onToTemplate: () => void;
  onCancel: (reason: string | null) => void;
  onDelete: () => void;
  busy?: boolean;
};

export function CardHeader({
  card, title, titleErr, onTitleChange, titleRef, onClose, onToTemplate, onCancel, onDelete, busy,
}: Props): JSX.Element {
  const [reason, setReason] = useState('');
  const progress = progressText(card.progress);

  return (
    <header className={styles.head}>
      <div className={`${styles.field} field`}>
        <label htmlFor="task-title">Название</label>
        <input
          id="task-title"
          ref={titleRef}
          type="text"
          className={titleErr ? 'invalid' : ''}
          value={title}
          onChange={(e) => onTitleChange(e.target.value)}
        />
        {titleErr && <p className="err">{titleErr}</p>}
      </div>

      <div className={styles.headMeta}>
        <span className={styles.statusPill} data-status={card.task_status}>{card.status_ru}</span>
        <span className="muted">{deadlineText(card.control_date)}</span>
        {progress && <span className="muted">{progress}</span>}
      </div>

      <div className={styles.headActions}>
        {card.actions.includes('close') && (
          <ConfirmButton label="Закрыть" onConfirm={onClose} disabled={busy} className="primary" />
        )}
        {card.actions.includes('to_template') && (
          <button type="button" onClick={onToTemplate} disabled={busy}>Сохранить как шаблон</button>
        )}
        {card.actions.includes('cancel') && (
          <span className={styles.cancelGroup}>
            <input
              type="text"
              placeholder="причина отмены (необязательно)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <ConfirmButton label="Отменить задачу" onConfirm={() => onCancel(reason || null)} disabled={busy} />
          </span>
        )}
        {card.actions.includes('delete') && (
          <ConfirmButton label="Удалить" onConfirm={onDelete} disabled={busy} className={styles.danger} />
        )}
      </div>
    </header>
  );
}
