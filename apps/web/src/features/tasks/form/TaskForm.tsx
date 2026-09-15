// Форма «Новая задача» (SLICE2_SPEC.md §5.4). Не `<form>`/`submit`: Enter в
// полях ведёт по цепочке (название → теги → шаг → дата → следующий шаг),
// сохраняет только `Ctrl+Enter` или кнопка — обычный `Enter` отправку не
// вызывает нигде (п.1), поэтому нативная семантика формы тут не нужна и
// только мешала бы.
import { useRef, useState } from 'react';
import { useHotkeys } from 'react-hotkeys-hook';
import { useNavigate } from 'react-router';
import type { FieldError, Hypothesis } from '@/api/client';
import { ApiError, errorsFor, generalErrors } from '@/api/errors';
import { useToaster } from '@/ui/toasterContext';
import { KbHints } from './KbHints';
import { StepsEditor } from './StepsEditor';
import { useCreateTask } from './useCreateTask';
import { usePlan } from './usePlan';
import { firstErrorField, newLeaf, toTaskIn } from './model';
import type { StepDraft } from './model';
import styles from './TaskForm.module.css';

export type Props = { initialTitle?: string };

function knownField(f: string | null): boolean {
  return f === null || f === 'title' || f === 'steps' || f.startsWith('steps[');
}

export function TaskForm({ initialTitle = '' }: Props) {
  const [title, setTitle] = useState(initialTitle);
  const [tags, setTags] = useState('');
  const [body, setBody] = useState('');
  const [steps, setSteps] = useState<StepDraft[]>(() => [newLeaf()]);
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [mentions, setMentions] = useState<Hypothesis[]>([]);
  const [focusFirstToken, setFocusFirstToken] = useState(0);

  const titleRef = useRef<HTMLInputElement>(null);
  const tagsRef = useRef<HTMLInputElement>(null);

  const plan = usePlan(steps);
  const create = useCreateTask();
  const toaster = useToaster();
  const navigate = useNavigate();

  const titleErr = errorsFor(errors, 'title');
  const stepsErr = errorsFor(errors, 'steps');
  // Прочее — в общий блок: ошибка без поля (`field: null`) и любая, чей путь
  // сюда не подходит (страховка от расхождения с контрактом, не должна
  // срабатывать при нормальной работе API).
  const other = errors.filter((e) => !knownField(e.field)).map((e) => e.error);
  const general = [...generalErrors(errors), ...other];

  async function submit() {
    if (create.isPending) return;
    setErrors([]);
    try {
      const task = toTaskIn(title, tags, body, steps);
      const result = await create.mutateAsync({ task, mentions });
      let message = `Создана: ${result.task} · шагов ${result.steps} · ${result.statusRu}`;
      if (result.linked !== null) message += ` · проставлено ссылок: ${result.linked}`;
      toaster.push({ title: message, action: { label: 'Открыть', run: () => navigate(`/задача/${result.task_id}`) } });
      setTitle('');
      setTags('');
      setBody('');
      setSteps([newLeaf()]);
      setMentions([]);
      titleRef.current?.focus();
    } catch (e) {
      if (e instanceof ApiError) {
        setErrors(e.errors);
        if (firstErrorField(e.errors) === 'title') titleRef.current?.focus();
      } else {
        toaster.push({ title: 'сервер не ответил', tone: 'error' });
      }
    }
  }

  useHotkeys('mod+enter', () => void submit(), { enableOnFormTags: true, preventDefault: true },
    [title, tags, body, steps, mentions, create.isPending]);

  return (
    <div className={styles.form}>
      <div className="field">
        <label htmlFor="task-title">Название задачи</label>
        <input
          id="task-title"
          ref={titleRef}
          type="text"
          autoComplete="off"
          autoFocus
          className={titleErr ? 'invalid' : ''}
          placeholder="Оплатить налоги за третий квартал"
          value={title}
          onChange={(e) => { setTitle(e.target.value); setErrors((es) => es.filter((x) => x.field !== 'title')); }}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' || e.ctrlKey || e.metaKey) return;
            e.preventDefault();
            tagsRef.current?.focus();
          }}
        />
        {titleErr && <p className="err">{titleErr}</p>}
      </div>

      <div className="field">
        <label htmlFor="task-tags">Категории <span className="muted">необязательно</span></label>
        <input
          id="task-tags"
          ref={tagsRef}
          type="text"
          autoComplete="off"
          placeholder="финансы, отчётность"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' || e.ctrlKey || e.metaKey) return;
            e.preventDefault();
            setFocusFirstToken((t) => t + 1);
          }}
        />
        <p className="note">Через запятую</p>
      </div>

      <section className={styles.stepsBlock}>
        <div className={styles.stepsHead}>
          <h2>Шаги</h2>
          <p className="note">Идут по очереди: следующий откроется, когда закроешь предыдущий</p>
        </div>
        <StepsEditor steps={steps} onChange={setSteps} plan={plan} errors={errors} focusFirstToken={focusFirstToken} />
        {stepsErr && <p className="err">{stepsErr}</p>}
        <button type="button" className="ghost" onClick={() => setSteps((ss) => [...ss, newLeaf()])}>Добавить шаг</button>
      </section>

      <div className="field">
        <label htmlFor="task-body">Заметка <span className="muted">необязательно</span></label>
        <textarea
          id="task-body"
          rows={3}
          placeholder="Телефоны, номера счетов, ссылки на [[Василий Говнов]]"
          value={body}
          onChange={(e) => setBody(e.target.value)}
        />
        <p className="note">Ссылки в двойных скобках ведут в базу знаний</p>
      </div>

      <KbHints text={body} onConfirmedChange={setMentions} />

      {general.map((m) => <p key={m} className="err">{m}</p>)}

      <div className={styles.actions}>
        <button type="button" className="primary" disabled={create.isPending} onClick={() => void submit()}>
          Создать задачу <kbd>Ctrl+Enter</kbd>
        </button>
      </div>
    </div>
  );
}
