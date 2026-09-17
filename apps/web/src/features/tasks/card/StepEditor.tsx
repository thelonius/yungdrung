// Редактор одного шага (лист или группа) — раскрывается по клику/`Enter`
// на строке чеклиста (SLICE2_SPEC.md §5.5 п.7). Правки живут в черновике
// родителя до «Сохранить»: этот компонент только читает/пишет через `onPatch`.
import type { JSX } from 'react';
import type { AttachmentInfo, FieldError } from '@/api/client';
import { DateField } from '@/ui/DateField';
import { AttachmentList } from '@/features/attachments/AttachmentList';
import { errorAt, type DraftStep } from './model';
import styles from './TaskCardPage.module.css';

const STEP_STATUS_RU: Record<string, string> = { done: 'сделан', failed: 'провален', skipped: 'снят' };

type Props = {
  node: DraftStep;
  indices: number[];
  taskId: number;
  errors: FieldError[];
  attachments?: AttachmentInfo[];
  onPatch: (patch: Partial<DraftStep>) => void;
  onAddSubstep: () => void;
  onSplit: () => void;
  onAttachmentsChanged: () => void;
};

export function StepEditor({
  node, indices, taskId, errors, attachments, onPatch, onAddSubstep, onSplit, onAttachmentsChanged,
}: Props): JSX.Element {
  const path = indices.join('-');
  const isGroup = node.steps.length > 0;
  const canSplit = indices.length === 1; // «одна глубина» — разбить можно только верхний лист
  const titleErr = errorAt(errors, indices, 'title');
  const controlErr = errorAt(errors, indices, 'control_date');
  const startErr = errorAt(errors, indices, 'start_date');
  const modeErr = errorAt(errors, indices, 'mode') ?? errorAt(errors, indices, 'steps');

  return (
    <div className={styles.editor} data-testid="card-step-editor">
      <div className={`${styles.field} field`}>
        <label htmlFor={`step-title-${path}`}>Название</label>
        <input
          id={`step-title-${path}`}
          type="text"
          className={titleErr ? 'invalid' : ''}
          value={node.title ?? ''}
          onChange={(e) => onPatch({ title: e.target.value })}
        />
        {titleErr && <p className="err">{titleErr}</p>}
      </div>

      {isGroup ? (
        <>
          <div className={`${styles.field} field`}>
            <label htmlFor={`step-mode-${path}`}>Подшаги</label>
            <select
              id={`step-mode-${path}`}
              className={modeErr ? 'invalid' : ''}
              value={node.mode ?? 'par'}
              onChange={(e) => onPatch({ mode: e.target.value })}
            >
              <option value="par">в любом порядке</option>
              <option value="seq">по очереди</option>
            </select>
            {modeErr && <p className="err">{modeErr}</p>}
          </div>
          {/* Ядро отдаёт `.start_date`/`.control_date` для группы, если в
              данных остались даты листа (после «Разбить на подшаги» или
              правки шага руками) — «Даты ставятся подшагам, не группе»
              (domain/steps_plan.py). Раньше эти ошибки считались, но нигде
              не показывались (находка ревью среза 2). */}
          {startErr && <p className="err">{startErr}</p>}
          {controlErr && <p className="err">{controlErr}</p>}
          <button type="button" className="small" onClick={onAddSubstep}>+ Подшаг</button>
        </>
      ) : (
        <>
          <DateField
            id={`step-start-${path}`}
            label="Дата начала"
            value={node.start_date ?? ''}
            onChange={(text) => onPatch({ start_date: text || null })}
            onParsed={() => {}}
            onSubmit={() => {}}
            withTime={false}
          />
          {startErr && <p className="err">{startErr}</p>}
          <DateField
            id={`step-control-${path}`}
            label="Дата контроля"
            value={node.control_date ?? ''}
            onChange={(text) => onPatch({ control_date: text || null })}
            onParsed={() => {}}
            onSubmit={() => {}}
          />
          {controlErr && <p className="err">{controlErr}</p>}
        </>
      )}

      <div className={`${styles.field} field`}>
        <label htmlFor={`step-note-${path}`}>Заметка</label>
        <textarea
          id={`step-note-${path}`}
          rows={2}
          value={node.note ?? ''}
          onChange={(e) => onPatch({ note: e.target.value || null })}
        />
      </div>

      {node.closed && (
        <p className="hint">
          Шаг уже {STEP_STATUS_RU[node.status] ?? node.status} — правка полей не меняет отметку.
        </p>
      )}

      {!isGroup && canSplit && (
        <button type="button" className="ghost small" onClick={onSplit}>Разбить на подшаги</button>
      )}

      {node.id != null && (
        <AttachmentList
          owner={{ kind: 'task', task_id: taskId, step_id: node.id }}
          items={attachments}
          filter={(a) => a.step_id === node.id}
          onChanged={onAttachmentsChanged}
          compact
        />
      )}
    </div>
  );
}
