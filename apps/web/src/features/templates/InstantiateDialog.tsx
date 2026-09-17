// «Завести задачу» — диалог развёртывания шаблона (SLICE2_SPEC.md §5.6).
// Предпросмотр дат считает ядро (`GET .../preview?start=`), диалог только
// показывает строки и не трогает даты сам — то же правило, что у ленты
// (CONTRACT.md).
import * as Dialog from '@radix-ui/react-dialog';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { api } from '@/api/client';
import type { PreviewRow, TemplateCard, WhenResult } from '@/api/client';
import { ApiError, errorsFor, generalErrors } from '@/api/errors';
import { DateField } from '@/ui/DateField';
import { useInstantiate } from './useTemplates';
import styles from '@/ui/control/ControlDialog.module.css';
import own from './TemplatesPage.module.css';

type Props = {
  template: TemplateCard | null;
  onClose: () => void;
};

export function InstantiateDialog({ template, onClose }: Props) {
  const [start, setStart] = useState('');
  const [parsed, setParsed] = useState<WhenResult | null>(null);
  const [title, setTitle] = useState('');
  const [preview, setPreview] = useState<PreviewRow[]>([]);
  const [errors, setErrors] = useState<{ field: string | null; error: string }[]>([]);
  const instantiate = useInstantiate(template?.name ?? '');
  const navigate = useNavigate();
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    setStart('');
    setParsed(null);
    setTitle('');
    setPreview([]);
    setErrors([]);
  }, [template]);

  // Предпросмотр по сохранённому шаблону: своя ручка (`GET .../preview`), не
  // `POST /templates/preview` — тот берёт черновик из формы правки (§1.3).
  useEffect(() => {
    if (!template) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      const { data } = await api.GET('/api/v1/templates/{name}/preview', {
        params: { path: { name: template.name }, query: { start: start.trim() || undefined } },
      });
      if (data) { setPreview(data.steps ?? []); setErrors(data.errors ?? []); }
    }, 200);
    return () => window.clearTimeout(timer.current);
  }, [template, start]);

  function submit() {
    if (!template) return;
    instantiate.mutate({ start: start.trim() || null, title: title.trim() || null }, {
      onSuccess: (r) => navigate(`/задача/${r.task_id}`),
      onError: (e) => setErrors(e instanceof ApiError ? e.errors : [{ field: null, error: e.message }]),
    });
  }

  return (
    <Dialog.Root open={template !== null} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={styles.content} aria-describedby={undefined}>
          {template && (
            <>
              <Dialog.Title className={styles.step}>Завести задачу из «{template.name}»</Dialog.Title>

              <div className={styles.form}>
                <DateField
                  id="inst-start"
                  value={start}
                  onChange={setStart}
                  onParsed={setParsed}
                  onSubmit={submit}
                  withTime={false}
                  label="Дата старта"
                  autoFocus
                />
                {errorsFor(errors, 'start') && <p className="err">{errorsFor(errors, 'start')}</p>}

                <div className={styles.field}>
                  <label htmlFor="inst-title">Название задачи</label>
                  <input
                    id="inst-title"
                    type="text"
                    placeholder={`по умолчанию — «${template.name}»`}
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } }}
                  />
                  {errorsFor(errors, 'title') && <p className="err">{errorsFor(errors, 'title')}</p>}
                </div>

                {preview.length > 0 && (
                  <ul className={own.preview} data-testid="inst-preview">
                    {preview.map((r) => (
                      <li key={r.position} className={r.on_weekend ? own.weekend : ''}>
                        <span className={own.previewTitle}>{r.title}</span>
                        <span>{r.control_text}</span>
                      </li>
                    ))}
                  </ul>
                )}

                {generalErrors(errors).map((e, i) => <p key={i} className="err">{e}</p>)}
                {parsed && !parsed.ok && <p className="err">{parsed.error}</p>}

                <div className={styles.actions}>
                  <button type="button" className="primary" disabled={instantiate.isPending} onClick={submit}>
                    Создать <kbd>Enter</kbd>
                  </button>
                  <button type="button" className="ghost" onClick={onClose}>Отмена <kbd>Esc</kbd></button>
                </div>
              </div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
