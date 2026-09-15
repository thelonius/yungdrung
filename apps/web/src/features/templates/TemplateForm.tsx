// Создание и правка шаблона (SLICE2_SPEC.md §5.6). Переименование через эту
// форму невозможно (Р4): в режиме правки имя показывается текстом, не полем.
import * as Dialog from '@radix-ui/react-dialog';
import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { api } from '@/api/client';
import type { TemplateCard, TemplateIn } from '@/api/client';
import { ApiError, errorsFor, generalErrors } from '@/api/errors';
import { useToaster } from '@/ui/toasterContext';
import { PendingFiles } from '@/features/attachments/PendingFiles';
import { uploadPending } from '@/features/attachments/useAttachments';
import { recurrencePayload, saveThenUpload, stepErrors, sumOffsets, decomposeOffsets } from './model';
import { useSaveTemplate } from './useTemplates';
import styles from '@/ui/control/ControlDialog.module.css';
import own from './TemplatesPage.module.css';

type StepDraft = { title: string; offsetRaw: string; time: string };

type Props = {
  /** `null` — форма закрыта. */
  open: boolean;
  /** Шаблон под правкой, `null` — создание нового (Р4 SLICE2_SPEC.md). */
  initial: TemplateCard | null;
  onClose: () => void;
};

function stepsFromInitial(t: TemplateCard | null): StepDraft[] {
  if (!t || t.steps.length === 0) return [{ title: '', offsetRaw: '0', time: '' }];
  const increments = decomposeOffsets(t.steps.map((s) => s.offset_days));
  return t.steps.map((s, i) => ({ title: s.title, offsetRaw: String(increments[i]), time: s.time_of_day ?? '' }));
}

export function TemplateForm({ open, initial, onClose }: Props) {
  const [name, setName] = useState('');
  const [tags, setTags] = useState('');
  const [body, setBody] = useState('');
  const [sampleStart, setSampleStart] = useState('');
  const [steps, setSteps] = useState<StepDraft[]>([{ title: '', offsetRaw: '0', time: '' }]);
  const [pending, setPending] = useState<File[]>([]);
  const [previewText, setPreviewText] = useState<Record<number, string>>({});
  const [errors, setErrors] = useState<{ field: string | null; error: string }[]>([]);
  const [saving, setSaving] = useState(false);
  const save = useSaveTemplate();
  const toaster = useToaster();
  const refs = useRef<Record<string, HTMLInputElement | HTMLTextAreaElement | null>>({});
  const previewTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!open) return;
    setName(initial?.name ?? '');
    setTags(initial?.tags.join(', ') ?? '');
    setBody(initial?.body ?? '');
    setSampleStart('');
    setSteps(stepsFromInitial(initial));
    setPending([]);
    setPreviewText({});
    setErrors([]);
    window.setTimeout(() => refs.current.name?.focus(), 30);
  }, [open, initial]);

  // Живой предпросмотр — POST /templates/preview, дебаунс 220 (§5.6). Пустое
  // название шага заменяется заглушкой перед отправкой: имя шага при наборе
  // ещё не готово, а `core.templates.preview` без него вообще не считает даты
  // (см. `templates.py::validate_template`) — тот же приём, что был в
  // `static/templates.js::ntОбновитьДаты`, не новая придумка.
  useEffect(() => {
    if (!open) return;
    window.clearTimeout(previewTimer.current);
    const offsets = sumOffsets(steps.map((s) => s.offsetRaw));
    const draft: TemplateIn = {
      name: name.trim() || '—',
      tags: [],
      body: '',
      steps: steps.map((s, i) => ({
        title: s.title.trim() || '·',
        offset_days: offsets[i],
        time_of_day: s.time.trim() || null,
      })),
      recurrence: null,
    };
    previewTimer.current = window.setTimeout(async () => {
      const { data } = await api.POST('/api/v1/templates/preview', {
        body: { template: draft, start: sampleStart.trim() || undefined },
      });
      if (!data) return;
      if (data.ok) {
        const byPos: Record<number, string> = {};
        (data.steps ?? []).forEach((r, i) => { byPos[i] = r.control_text; });
        setPreviewText(byPos);
      } else {
        setPreviewText({});
      }
    }, 220);
    return () => window.clearTimeout(previewTimer.current);
    // name/tags/body намеренно не в зависимостях: они не влияют на даты,
    // а `draft` их всё равно захватывает свежими на каждый рендер-эффект.
  }, [open, steps, sampleStart]);

  if (!open) return null;

  const stepFieldErrors = stepErrors(errors);

  function addStep(after: number) {
    setSteps((xs) => {
      const next = [...xs];
      next.splice(after + 1, 0, { title: '', offsetRaw: '1', time: '' });
      return next;
    });
    window.setTimeout(() => refs.current[`title-${after + 1}`]?.focus(), 30);
  }

  function removeStep(i: number) {
    if (steps.length < 2) return;
    setSteps((xs) => xs.filter((_, j) => j !== i));
    const focusAfter = i > 0 ? i - 1 : 0;
    window.setTimeout(() => refs.current[`title-${focusAfter}`]?.focus(), 30);
  }

  function patchStep(i: number, patch: Partial<StepDraft>) {
    setSteps((xs) => xs.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  }

  async function submit() {
    setSaving(true);
    setErrors([]);
    const offsets = sumOffsets(steps.map((s) => s.offsetRaw));
    const data: TemplateIn = {
      name: name.trim(),
      tags: tags.split(',').map((s) => s.trim()).filter(Boolean),
      body,
      steps: steps.map((s, i) => ({ title: s.title, offset_days: offsets[i], time_of_day: s.time.trim() || null })),
      recurrence: initial ? recurrencePayload(initial.recurrence) : undefined,
    };
    try {
      const card = await saveThenUpload(
        () => save.mutateAsync({ data, expectName: initial?.name }),
        async (saved) => {
          if (pending.length === 0) return;
          const { failed } = await uploadPending({ kind: 'template', name: saved.name }, pending);
          if (failed.length) {
            toaster.push({
              title: `Шаблон сохранён, но файлы не прикрепились: ${failed.map((f) => f.file.name).join(', ')}`,
              tone: 'error',
            });
          }
        },
      );
      toaster.push({ title: `Шаблон «${card.name}» сохранён` });
      onClose();
    } catch (e) {
      if (e instanceof ApiError) setErrors(e.errors);
      else toaster.push({ title: 'не получилось', tone: 'error' });
    } finally {
      setSaving(false);
    }
  }

  function onEnterField(e: KeyboardEvent, next: () => void) {
    if (e.key !== 'Enter') return;
    if (e.ctrlKey || e.metaKey) { e.preventDefault(); submit(); return; }
    e.preventDefault();
    next();
  }

  return (
    <Dialog.Root open onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={`${styles.content} ${own.formContent}`} aria-describedby={undefined}>
          <Dialog.Title className={styles.step}>{initial ? `Правка «${initial.name}»` : 'Новый шаблон'}</Dialog.Title>

          <div className={styles.form}>
            <div className={styles.field}>
              <label htmlFor="tpl-name">Название</label>
              {initial ? (
                <p className={own.fixedName}>{initial.name}</p>
              ) : (
                <input
                  id="tpl-name"
                  ref={(el) => { refs.current.name = el; }}
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => onEnterField(e, () => refs.current.tags?.focus())}
                  className={errorsFor(errors, 'name') ? 'invalid' : ''}
                />
              )}
              {errorsFor(errors, 'name') && <p className="err">{errorsFor(errors, 'name')}</p>}
            </div>

            <div className={styles.field}>
              <label htmlFor="tpl-tags">Теги, через запятую</label>
              <input
                id="tpl-tags"
                ref={(el) => { refs.current.tags = el; }}
                type="text"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                onKeyDown={(e) => onEnterField(e, () => refs.current['title-0']?.focus())}
              />
            </div>

            <div className={styles.field}>
              <label htmlFor="tpl-sample-start">Дата старта для предпросмотра</label>
              <input
                id="tpl-sample-start"
                type="text"
                placeholder="сегодня"
                value={sampleStart}
                onChange={(e) => setSampleStart(e.target.value)}
              />
            </div>

            <ol className={own.stepsList}>
              {steps.map((s, i) => {
                const fe = stepFieldErrors[i] ?? {};
                return (
                  <li key={i} className={own.stepRow}>
                    <span className={own.stepNum}>{i + 1}</span>
                    <input
                      type="text"
                      placeholder="название шага"
                      value={s.title}
                      ref={(el) => { refs.current[`title-${i}`] = el; }}
                      onChange={(e) => patchStep(i, { title: e.target.value })}
                      onKeyDown={(e) => onEnterField(e, () => refs.current[`offset-${i}`]?.focus())}
                      className={errorsFor(errors, `steps[${i}].title`) ? 'invalid' : ''}
                    />
                    <div className={own.offsetField}>
                      <label htmlFor={`step-offset-${i}`} className={own.smallLabel}>
                        {i === 0 ? 'дн. от старта' : 'через, дн.'}
                      </label>
                      <input
                        id={`step-offset-${i}`}
                        type="text"
                        inputMode="numeric"
                        value={s.offsetRaw}
                        ref={(el) => { refs.current[`offset-${i}`] = el; }}
                        onChange={(e) => patchStep(i, { offsetRaw: e.target.value })}
                        onKeyDown={(e) => onEnterField(e, () => refs.current[`time-${i}`]?.focus())}
                        className={fe.offset_days ? 'invalid' : ''}
                      />
                    </div>
                    <input
                      type="text"
                      placeholder="время"
                      value={s.time}
                      ref={(el) => { refs.current[`time-${i}`] = el; }}
                      onChange={(e) => patchStep(i, { time: e.target.value })}
                      onKeyDown={(e) => onEnterField(e, () => (i === steps.length - 1 ? addStep(i) : refs.current[`title-${i + 1}`]?.focus()))}
                      className={fe.time_of_day ? 'invalid' : ''}
                    />
                    <span className={own.stepPreview}>{previewText[i] ?? ''}</span>
                    <button type="button" className={`quiet small ${own.stepDrop}`} hidden={steps.length < 2} onClick={() => removeStep(i)}>×</button>
                    {(fe.offset_days || fe.time_of_day) && (
                      <p className={`err ${own.stepErr}`}>{fe.offset_days ?? fe.time_of_day}</p>
                    )}
                  </li>
                );
              })}
            </ol>
            <button type="button" className="ghost small" onClick={() => addStep(steps.length - 1)}>+ Шаг</button>
            {errorsFor(errors, 'steps') && <p className="err">{errorsFor(errors, 'steps')}</p>}

            <div className={styles.field}>
              <label htmlFor="tpl-body">Заметка</label>
              <textarea id="tpl-body" value={body} onChange={(e) => setBody(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); } }} />
            </div>

            {!initial && (
              <div className={styles.field}>
                <label>Файлы</label>
                <PendingFiles files={pending} onChange={setPending} />
              </div>
            )}

            {generalErrors(errors).map((e, i) => <p key={i} className="err">{e}</p>)}

            <div className={styles.actions}>
              <button type="button" className="primary" disabled={saving} onClick={submit}>
                Сохранить <kbd>Ctrl+Enter</kbd>
              </button>
              <button type="button" className="ghost" onClick={onClose}>Отмена <kbd>Esc</kbd></button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
