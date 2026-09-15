// Карточка задачи — `/задача/:id` (SLICE2_SPEC.md §5.5). Черновик правки
// (`draft`) живёт локально до «Сохранить»; отметка, переоткрытие, закрытие,
// отмена и удаление пишут сразу — карточка обновляется из их ответа, без
// второго похода за `GET` (п.11 §5.5), кроме отметки: `mark`/`undo` отдают
// `MarkResult`, не карточку, поэтому там — инвалидация и перечитывание
// (п.6 §5.5).
import { useCallback, useEffect, useRef, useState } from 'react';
import type { JSX } from 'react';
import { useHotkeys } from 'react-hotkeys-hook';
import { useNavigate, useParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import type { FieldError, FieldWarning, MarkOp, TaskCard, TaskEditIn } from '@/api/client';
import { errorText } from '@/api/client';
import { ApiError, errorsFor, generalErrors } from '@/api/errors';
import { useToaster } from '@/ui/toasterContext';
import { DateField } from '@/ui/DateField';
import { ControlDialog } from '@/ui/control/ControlDialog';
import type { DialogMode } from '@/ui/control/ControlDialog';
import { AttachmentList } from '@/features/attachments/AttachmentList';
import { usePasteToAttach } from '@/features/attachments/usePasteToAttach';
import { clampFocus, moveFocus, outcomeText } from '@/features/feed/model';
import { useMark, useUndo } from '@/features/feed/useFeed';
import {
  TASK, useCancelTask, useCloseTask, useDeleteTask, usePlan, useReopenStep, useTaskAttachments, useTaskQuery,
  useToTemplate, useUpdateTask,
} from './useTask';
import {
  addLeaf, buildEditSteps, errorPaths, fromCard, getNode, leafRows, missingStepsError, patchNode, moveSibling,
  splitToSubsteps, type DraftStep,
} from './model';
import { CardHeader } from './CardHeader';
import { StepChecklist } from './StepChecklist';
import { History } from './History';
import styles from './TaskCardPage.module.css';

export function TaskCardPage(): JSX.Element {
  const { id: idParam } = useParams();
  const id = Number(idParam);
  const validId = Number.isFinite(id) && id > 0 ? id : null;

  const navigate = useNavigate();
  const toaster = useToaster();
  const qc = useQueryClient();

  const query = useTaskQuery(validId);
  const card = query.data;
  const attachments = useTaskAttachments(validId);

  const update = useUpdateTask(validId ?? 0);
  const cancelTask = useCancelTask(validId ?? 0);
  const closeTask = useCloseTask(validId ?? 0);
  const deleteTask = useDeleteTask(validId ?? 0);
  const reopenStep = useReopenStep(validId ?? 0);
  const toTemplate = useToTemplate(validId ?? 0);
  const plan = usePlan();
  const mark = useMark();
  const undo = useUndo();

  // --- черновик правки: инициализируется с загрузки и после каждой явной
  // записи (ответ несёт свежую карточку — п.11 §5.5), но не на фоновый
  // рефетч (окно фокуса и т. п.), чтобы не затирать набранное.
  const [title, setTitle] = useState('');
  const [startDate, setStartDate] = useState('');
  const [tags, setTags] = useState('');
  const [body, setBody] = useState('');
  const [draft, setDraft] = useState<DraftStep[]>([]);
  const initedFor = useRef<number | null>(null);
  const titleRef = useRef<HTMLInputElement>(null);

  const syncFromCard = useCallback((c: TaskCard) => {
    initedFor.current = c.task_id;
    setTitle(c.task);
    setStartDate(c.start_date);
    setTags(c.tags.join(', '));
    setBody(c.body);
    setDraft(fromCard(c.steps));
    setSaveErrors([]);
    setForce(false);
  }, []);

  useEffect(() => {
    if (card && initedFor.current !== card.task_id) syncFromCard(card);
  }, [card, syncFromCard]);

  // --- ошибки: `saveErrors` из ответа `PUT` (авторитетны), `planErrors` —
  // живая проверка на каждую правку (п.10 §5.5), уступает `saveErrors`.
  const [saveErrors, setSaveErrors] = useState<FieldError[]>([]);
  const [planErrors, setPlanErrors] = useState<FieldError[]>([]);
  const [force, setForce] = useState(false);
  const [openPaths, setOpenPaths] = useState<Set<string>>(new Set());
  const [focusIdx, setFocusIdx] = useState(-1);
  const [dialog, setDialog] = useState<{ node: DraftStep; mode: DialogMode } | null>(null);

  const errors = saveErrors.length ? saveErrors : planErrors;
  const leaves = leafRows(draft);
  const idx = clampFocus(focusIdx, leaves.length);
  const focusedLeaf = idx >= 0 ? leaves[idx] : null;
  const focusedPath = focusedLeaf ? focusedLeaf.indices.join(',') : null;
  const overlay = dialog !== null;

  // Пока список листьев непустой, фокус всегда есть — как в ленте (`j`/`d`
  // должны работать сразу после загрузки, без промежуточного `j`).
  useEffect(() => { if (focusIdx < 0 && leaves.length > 0) setFocusIdx(0); }, [leaves.length, focusIdx]);

  // --- живая проверка (п.10 §5.5): на каждую правку черновика/даты старта,
  // дебаунс — как у `DateField` (200 мс), только на весь черновик разом.
  useEffect(() => {
    if (validId === null) return;
    const t = window.setTimeout(() => {
      plan.mutate({ task_id: validId, start_date: startDate || null, steps: buildEditSteps(draft) }, {
        onSuccess: (r) => setPlanErrors(r.errors),
      });
    }, 300);
    return () => window.clearTimeout(t);
    // `plan` намеренно не в зависимостях — мутация меняет идентичность на
    // каждый рендер, а перезапускать проверку от этого не нужно (как в DateField).
  }, [validId, startDate, JSON.stringify(draft)]);

  async function refreshCard() {
    if (validId === null) return;
    await qc.invalidateQueries({ queryKey: TASK(validId) });
    const fresh = qc.getQueryData<TaskCard>(TASK(validId));
    if (fresh) syncFromCard(fresh);
  }

  // --- отметка шага: тот же `useMark`, что у ленты (п.6 §5.5) -----------
  const doMark = useCallback((node: DraftStep, op: MarkOp, extra: { reason?: string | null; to?: string | null } = {}) => {
    if (validId === null || node.id == null) return;
    const stepId = node.id;
    setDialog(null);
    mark.mutate({ task_id: validId, step: stepId, op, ...extra }, {
      onSuccess: async (r) => {
        toaster.push({
          title: outcomeText(op, r),
          action: {
            label: 'Отменить',
            run: () => undo.mutate({ task_id: validId, step: stepId }, {
              onSuccess: async () => { toaster.push({ title: 'Отменено' }); await refreshCard(); },
              onError: (e) => toaster.push({ title: errorText(e) || e.message, tone: 'error' }),
            }),
          },
        });
        await refreshCard();
      },
      onError: (e) => toaster.push({ title: e.message, tone: 'error' }),
    });
  }, [validId, mark, undo, toaster]);

  const doReopen = useCallback((node: DraftStep) => {
    if (validId === null || node.id == null) return;
    reopenStep.mutate(node.id, {
      onSuccess: (c) => { syncFromCard(c); toaster.push({ title: 'Шаг переоткрыт' }); },
      onError: (e) => toaster.push({ title: e.message, tone: 'error' }),
    });
  }, [validId, reopenStep, syncFromCard]);

  // --- правка черновика ---------------------------------------------------
  function togglePath(indices: number[]) {
    const key = indices.join(',');
    setOpenPaths((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }
  function openPath(indices: number[]) {
    setOpenPaths((s) => new Set(s).add(indices.join(',')));
  }

  function addTopStep() {
    const newFocusIdx = leaves.length;
    setDraft((d) => addLeaf(d, []));
    openPath([draft.length]);
    setFocusIdx(newFocusIdx);
  }

  function addSubstep(indices: number[]) {
    const node = getNode(draft, indices);
    const childIdx = node ? node.steps.length : 0;
    setDraft((d) => addLeaf(d, indices));
    openPath([...indices, childIdx]);
  }

  function split(indices: number[]) {
    const node = getNode(draft, indices);
    if (!node) return;
    if (node.id == null) {
      toaster.push({ title: 'Сначала сохрани задачу, потом разбивай шаг', tone: 'error' });
      return;
    }
    setDraft((d) => patchNode(d, indices, splitToSubsteps(node)));
    openPath([...indices, 0]);
  }

  // --- сохранение (п.11 §5.5) ---------------------------------------------
  const doSave = useCallback((forceNow = force) => {
    if (validId === null) return;
    const payload: TaskEditIn = {
      title: title.trim(),
      start_date: startDate.trim() || null,
      tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
      body,
      steps: buildEditSteps(draft),
      force: forceNow,
    };
    update.mutate(payload, {
      onSuccess: (r) => {
        syncFromCard(r.card);
        toaster.push({ title: r.renamed_from ? `Сохранено, переименовано в «${r.task}»` : 'Сохранено' });
        r.warnings.forEach((w: FieldWarning) => toaster.push({ title: w.warning }));
      },
      onError: (e: ApiError) => {
        setSaveErrors(e.errors);
        setForce(forceNow);
        setOpenPaths((s) => new Set([...s, ...errorPaths(e.errors)]));
      },
    });
  }, [validId, title, startDate, tags, body, draft, force, update, syncFromCard, toaster]);

  usePasteToAttach(validId !== null ? { kind: 'task', task_id: validId } : null, {
    onDone: () => { toaster.push({ title: 'Картинка прикреплена' }); void attachments.refetch(); },
  });

  // --- клавиши (§5.2): в открытом окне контроля список молчит ------------
  const opts = { enabled: !overlay, preventDefault: true };
  useHotkeys('j, down', () => setFocusIdx((i) => moveFocus(i, 1, leaves.length)), opts, [leaves.length]);
  useHotkeys('k, up', () => setFocusIdx((i) => moveFocus(i, -1, leaves.length)), opts, [leaves.length]);
  useHotkeys('d', () => {
    if (focusedLeaf?.node.actions.includes('done')) doMark(focusedLeaf.node, 'done');
  }, opts, [focusedLeaf, doMark]);
  useHotkeys('n', () => {
    if (focusedLeaf?.node.actions.includes('notdone')) doMark(focusedLeaf.node, 'notdone');
  }, opts, [focusedLeaf, doMark]);
  useHotkeys('t', () => {
    if (focusedLeaf?.node.actions.includes('defer')) setDialog({ node: focusedLeaf.node, mode: 'defer' });
  }, opts, [focusedLeaf]);
  useHotkeys('f', () => {
    if (focusedLeaf?.node.actions.includes('fail')) setDialog({ node: focusedLeaf.node, mode: 'fail' });
  }, opts, [focusedLeaf]);
  useHotkeys('r', () => {
    if (focusedLeaf && focusedLeaf.node.status === 'done') doReopen(focusedLeaf.node);
  }, opts, [focusedLeaf, doReopen]);
  useHotkeys('enter', () => { if (focusedLeaf) togglePath(focusedLeaf.indices); }, opts, [focusedLeaf]);
  useHotkeys('e', () => titleRef.current?.focus(), opts, []);
  useHotkeys('mod+enter', () => doSave(), { enabled: true, preventDefault: true, enableOnFormTags: true }, [doSave]);

  if (validId === null) return <main className="wrap"><p className="err">Некорректный адрес задачи.</p></main>;
  if (query.isLoading) return <main className="wrap"><p className="muted">Загрузка…</p></main>;
  if (query.isError || !card) {
    return <main className="wrap"><p className="err">{query.error?.message ?? 'Задача не найдена'}</p></main>;
  }

  const missingErr = missingStepsError(saveErrors);

  return (
    <main className="wrap">
      <CardHeader
        card={card}
        title={title}
        titleErr={errorsFor(saveErrors, 'title')}
        onTitleChange={setTitle}
        titleRef={titleRef}
        busy={update.isPending || closeTask.isPending || cancelTask.isPending || deleteTask.isPending}
        onClose={() => closeTask.mutate(undefined, {
          onSuccess: (r) => { syncFromCard(r.card); toaster.push({ title: `Задача закрыта · шагов сразу: ${r.closed_steps}` }); },
          onError: (e) => toaster.push({ title: e.message, tone: 'error' }),
        })}
        onCancel={(reason) => cancelTask.mutate({ reason }, {
          onSuccess: (c) => { syncFromCard(c); toaster.push({ title: 'Задача отменена' }); },
          onError: (e) => toaster.push({ title: e.message, tone: 'error' }),
        })}
        onDelete={() => deleteTask.mutate(undefined, {
          onSuccess: () => navigate('/'),
          onError: (e) => toaster.push({ title: e.message, tone: 'error' }),
        })}
        onToTemplate={() => toTemplate.mutate(undefined, {
          onSuccess: (t) => toaster.push({ title: `Сохранено как шаблон «${t.name}»` }),
          onError: (e) => toaster.push({ title: e.message, tone: 'error' }),
        })}
      />

      <div className={styles.fields}>
        <DateField
          id="task-start" label="Дата начала" value={startDate} onChange={setStartDate}
          onParsed={() => {}} onSubmit={() => {}} withTime={false}
        />
        {errorsFor(saveErrors, 'start_date') && <p className="err">{errorsFor(saveErrors, 'start_date')}</p>}

        <div className="field">
          <label htmlFor="task-tags">Теги</label>
          <input id="task-tags" type="text" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="через запятую" />
          {errorsFor(saveErrors, 'tags') && <p className="err">{errorsFor(saveErrors, 'tags')}</p>}
        </div>

        <div className="field">
          <label htmlFor="task-body">Заметка</label>
          <textarea id="task-body" rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
          {errorsFor(saveErrors, 'body') && <p className="err">{errorsFor(saveErrors, 'body')}</p>}
        </div>

        <AttachmentList
          owner={{ kind: 'task', task_id: validId }}
          items={attachments.data}
          filter={(a) => a.step_id == null}
          onChanged={() => void attachments.refetch()}
        />
      </div>

      <StepChecklist
        nodes={draft}
        taskId={validId}
        focusedPath={focusedPath}
        openPaths={openPaths}
        errors={errors}
        attachments={attachments.data}
        onFocus={(indices) => setFocusIdx(leaves.findIndex((l) => l.indices.join(',') === indices.join(',')))}
        onToggleOpen={togglePath}
        onMarkDone={(node) => doMark(node, 'done')}
        onReopen={(node) => doReopen(node)}
        onOpenDialog={(node, mode) => setDialog({ node, mode })}
        onPatch={(indices, patch) => setDraft((d) => patchNode(d, indices, patch))}
        onAddSubstep={addSubstep}
        onSplit={split}
        onMove={(parent, from, to) => setDraft((d) => moveSibling(d, parent, from, to))}
        onAttachmentsChanged={() => void attachments.refetch()}
      />

      <button type="button" className="small" onClick={addTopStep}>+ Шаг</button>

      {missingErr && (
        <div className={styles.forceBox}>
          <p className="err">{missingErr}</p>
          <button type="button" onClick={() => doSave(true)}>Всё равно сохранить, шаг больше не нужен</button>
        </div>
      )}
      {generalErrors(saveErrors).map((e) => <p key={e} className="err">{e}</p>)}

      <div className={styles.saveBar}>
        <button
          type="button" className="primary" data-testid="save-card"
          disabled={update.isPending} onClick={() => doSave()}
        >
          Сохранить <kbd>Ctrl+Enter</kbd>
        </button>
      </div>

      <History history={card.history} />

      <footer className={styles.footer}>
        <span><kbd>j</kbd>/<kbd>k</kbd> фокус</span>
        <span><kbd>d</kbd> сделан</span>
        <span><kbd>t</kbd> перенести</span>
        <span><kbd>Enter</kbd> редактор шага</span>
        <span><kbd>e</kbd> название</span>
        <span><kbd>Ctrl+Enter</kbd> сохранить</span>
      </footer>

      <ControlDialog
        row={dialog?.node.row ?? null}
        mode={dialog?.mode ?? 'menu'}
        onClose={() => setDialog(null)}
        onMark={(op, extra) => { if (dialog) doMark(dialog.node, op, extra); }}
        idPrefix="card"
      />
    </main>
  );
}
