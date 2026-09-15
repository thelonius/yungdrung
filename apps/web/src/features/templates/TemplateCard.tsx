// Одна карточка списка шаблонов (SLICE2_SPEC.md §5.6). Действия: завести
// задачу, раскрыть файлы на месте, правка повторения, правка шаблона,
// удаление. Удаление и раскрытие файлов не всплывают выше — карточка сама
// себе владелец этих двух состояний, странице не нужно знать про них.
import { useState } from 'react';
import type { TemplateCard as TemplateCardData } from '@/api/client';
import { AttachmentList } from '@/features/attachments/AttachmentList';
import { useToaster } from '@/ui/toasterContext';
import { ConfirmButton } from '@/ui/ConfirmButton';
import { useDeleteTemplate } from './useTemplates';
import { recurrenceSummary, templateMeta } from './model';
import styles from './TemplatesPage.module.css';

type Props = {
  t: TemplateCardData;
  focused: boolean;
  onFocus: () => void;
  onInstantiate: () => void;
  onEdit: () => void;
  onRecurrence: () => void;
};

export function TemplateCard({ t, focused, onFocus, onInstantiate, onEdit, onRecurrence }: Props) {
  const [files, setFiles] = useState(false);
  const del = useDeleteTemplate();
  const toaster = useToaster();

  return (
    <li
      className={`${styles.card} ${focused ? styles.focused : ''}`}
      data-testid="template-card"
      data-name={t.name}
      aria-selected={focused}
      onMouseEnter={onFocus}
    >
      <div className={styles.cardMain}>
        <div className={styles.name}>{t.name}</div>
        <div className={styles.meta}>{templateMeta(t)}</div>
        <div className={`${styles.recur} ${t.recurrence ? styles.recurSet : ''}`}>
          {recurrenceSummary(t.recurrence)}
        </div>
        {t.body && <p className={styles.note}>{t.body}</p>}
      </div>
      <div className={styles.cardActions}>
        <button type="button" className="primary small" onClick={onInstantiate}>
          Завести задачу <kbd>Enter</kbd>
        </button>
        <button type="button" className="ghost small" onClick={() => setFiles((v) => !v)}>
          Файлы{t.attachments_count ? ` (${t.attachments_count})` : ''}
        </button>
        <button type="button" className="ghost small" onClick={onRecurrence}>Повторение</button>
        <button type="button" className="ghost small" onClick={onEdit}>
          Править <kbd>e</kbd>
        </button>
        {/* `data-role`, не CSS-класс: клавиша `x` в `TemplatesPage` находит
            кнопку через DOM (`querySelector`), не трогая внутреннее состояние
            `ConfirmButton` — тот целиком в ведении F4, здесь только клик. */}
        <span data-role="tpl-delete">
          <ConfirmButton
            label="Удалить"
            className="ghost small"
            onConfirm={() => del.mutate(t.name, {
              onSuccess: () => toaster.push({ title: `Шаблон «${t.name}» удалён` }),
              onError: (e) => toaster.push({ title: e.message, tone: 'error' }),
            })}
          />
        </span>
      </div>
      {files && (
        <div className={styles.cardFiles}>
          <AttachmentList owner={{ kind: 'template', name: t.name }} compact />
        </div>
      )}
    </li>
  );
}
