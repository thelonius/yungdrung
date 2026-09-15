// Чеклист шагов — дерево `CardStep`, п.5 §5.5 SLICE2_SPEC.md: чекбокс только
// у листьев, «⋯» открывает общий `ControlDialog` с `row` шага, перетаскивание
// переставляет соседей одного уровня (группа едет с детьми). Рекурсия одним
// компонентом: строка, под ней — редактор (если раскрыт), потом дети.
import { Fragment, useState } from 'react';
import type { DragEvent, JSX } from 'react';
import type { AttachmentInfo, FieldError } from '@/api/client';
import type { DialogMode } from '@/ui/control/ControlDialog';
import { errorAt, groupModeLabel, shortControlText, type DraftStep } from './model';
import { StepEditor } from './StepEditor';
import styles from './TaskCardPage.module.css';

type Props = {
  nodes: DraftStep[];
  taskId: number;
  focusedPath: string | null;
  openPaths: Set<string>;
  errors: FieldError[];
  attachments?: AttachmentInfo[];
  onFocus: (indices: number[]) => void;
  onToggleOpen: (indices: number[]) => void;
  onMarkDone: (node: DraftStep, indices: number[]) => void;
  onReopen: (node: DraftStep, indices: number[]) => void;
  onOpenDialog: (node: DraftStep, mode: DialogMode) => void;
  onPatch: (indices: number[], patch: Partial<DraftStep>) => void;
  onAddSubstep: (indices: number[]) => void;
  onSplit: (indices: number[]) => void;
  onMove: (parent: number[], from: number, to: number) => void;
  onAttachmentsChanged: () => void;
};

export function StepChecklist(props: Props): JSX.Element {
  return (
    <ol className={styles.checklist} role="list">
      <StepList {...props} parent={[]} />
    </ol>
  );
}

function StepList(props: Props & { parent: number[] }): JSX.Element {
  const { nodes, parent } = props;
  const [dragFrom, setDragFrom] = useState<number | null>(null);

  function onDragStart(i: number) {
    return (e: DragEvent) => { setDragFrom(i); e.dataTransfer.effectAllowed = 'move'; };
  }
  function onDragOver(e: DragEvent) { if (dragFrom !== null) e.preventDefault(); }
  function onDrop(i: number) {
    return (e: DragEvent) => {
      e.preventDefault();
      if (dragFrom !== null && dragFrom !== i) props.onMove(parent, dragFrom, i);
      setDragFrom(null);
    };
  }

  return (
    <>
      {nodes.map((node, i) => {
        const indices = [...parent, i];
        const path = indices.join(',');
        const isGroup = node.steps.length > 0;
        const focused = !isGroup && path === props.focusedPath;
        const open = props.openPaths.has(path);
        const titleErr = errorAt(props.errors, indices, 'title');

        return (
          <Fragment key={node.id ?? `new-${path}`}>
            <li
              className={[
                styles.row,
                node.closed ? styles.closed : '',
                node.state === 'overdue' ? styles.overdue : '',
                node.active && !node.closed ? styles.active : '',
                isGroup ? styles.group : '',
                parent.length > 0 ? styles.substep : '',
                focused ? styles.focused : '',
                titleErr ? styles.invalidRow : '',
              ].filter(Boolean).join(' ')}
              data-testid="card-step"
              data-step-id={node.id ?? 'new'}
              aria-selected={focused}
              draggable
              onDragStart={onDragStart(i)}
              onDragOver={onDragOver}
              onDrop={onDrop(i)}
              onClick={() => { if (!isGroup) props.onFocus(indices); props.onToggleOpen(indices); }}
            >
              {!isGroup ? (
                <input
                  type="checkbox"
                  checked={node.status === 'done'}
                  disabled={node.status === 'failed' || node.status === 'skipped'}
                  onClick={(e) => e.stopPropagation()}
                  onChange={() => (node.status === 'done'
                    ? props.onReopen(node, indices)
                    : props.onMarkDone(node, indices))}
                />
              ) : <span className={styles.groupMark} aria-hidden>▸</span>}

              <span className={styles.stepTitle}>{node.title || '(без названия)'}</span>

              <span className={styles.stepMeta}>
                {isGroup ? groupModeLabel(node.mode) : shortControlText(node.control_date)}
                {node.stalled > 0 && <span className={styles.stalledTag}>переносов: {node.stalled}</span>}
                {node.note && <span title="есть заметка" aria-hidden> 📝</span>}
              </span>

              <span className={styles.rowActions}>
                {!isGroup && node.row && (
                  <button
                    type="button" className="ghost small" title="Окно контроля"
                    onClick={(e) => { e.stopPropagation(); props.onOpenDialog(node, 'menu'); }}
                  >⋯</button>
                )}
                {!isGroup && node.status === 'done' && (
                  <button
                    type="button" className="ghost small" title="Переоткрыть"
                    onClick={(e) => { e.stopPropagation(); props.onReopen(node, indices); }}
                  >↺</button>
                )}
              </span>
            </li>

            {open && (
              <li className={styles.editorRow}>
                <StepEditor
                  node={node}
                  indices={indices}
                  taskId={props.taskId}
                  errors={props.errors}
                  attachments={props.attachments}
                  onPatch={(patch) => props.onPatch(indices, patch)}
                  onAddSubstep={() => props.onAddSubstep(indices)}
                  onSplit={() => props.onSplit(indices)}
                  onAttachmentsChanged={props.onAttachmentsChanged}
                />
              </li>
            )}

            {isGroup && (
              <li className={styles.substepList}>
                <ol role="list">
                  <StepList {...props} nodes={node.steps} parent={indices} />
                </ol>
              </li>
            )}
          </Fragment>
        );
      })}
    </>
  );
}
