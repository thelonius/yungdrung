// Быстрый ввод: задача одной строкой (SLICE2_SPEC.md §5.3). Открывается по
// `a` из `Layout` и из палитры команд. Разбор даты и создание — тот же код,
// что у полной формы и у бота (Р8: «форма и бот поймут ввод одинаково»);
// результат виден прямо в диалоге, тоста нет — закрыть его можно и не читая.
import { useEffect, useRef } from 'react';
import type { JSX } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { useNavigate } from 'react-router';
import styles from '@/ui/control/ControlDialog.module.css';
import own from './QuickAdd.module.css';
import { useQuickAdd } from './useQuickAdd';

export type Props = { open: boolean; onClose: () => void };

export function QuickAdd({ open, onClose }: Props): JSX.Element {
  const { text, onChange, extract, label, error, done, pending, create } = useQuickAdd(open);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => inputRef.current?.focus(), 30);
    return () => window.clearTimeout(t);
  }, [open]);

  // Enter на пустом поле после успешного создания — открыть карточку
  // (§5.3); на непустом — обычное создание. Второй Enter подряд без
  // печати между ними ведёт себя как «открыть», а не «создать ещё раз».
  async function submit() {
    if (!text.trim()) {
      if (done) {
        const id = done.task_id;
        onClose();
        navigate(`/задача/${id}`);
      }
      return;
    }
    await create();
  }

  const matched = extract?.span ? text.slice(extract.span[0], extract.span[1]) : '';
  const openMode = text.trim() === '' && done !== null;

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={styles.content} aria-describedby={undefined}>
          <Dialog.Title className={styles.step}>Быстрый ввод</Dialog.Title>

          <div className={styles.field}>
            <input
              id="quick-text"
              data-testid="quick-input"
              ref={inputRef}
              type="text"
              autoComplete="off"
              className={error ? 'invalid' : ''}
              placeholder="позвонить Василию завтра в полдесятого"
              value={text}
              disabled={pending}
              onChange={(e) => onChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); void submit(); }
              }}
            />
            {text.trim() !== '' && (
              <>
                {matched && (
                  <p className={own.preview} data-testid="quick-span">
                    <span>{extract?.title}</span>{' — '}
                    <span className={`${own.when} ${extract?.past ? own.past : ''}`}>{matched}</span>
                  </p>
                )}
                <p data-testid="quick-label" className={own.label}>{label}</p>
              </>
            )}
          </div>

          {error && <p className="err">{error.message}</p>}

          {done && (
            <p data-testid="quick-done" className={own.done}>
              Создана «{done.task}», шаг на {done.label} · Enter — открыть карточку · Esc — закрыть
            </p>
          )}

          <div className={styles.actions}>
            <button type="button" className="primary" onClick={() => void submit()} disabled={pending}>
              {openMode ? 'Открыть' : 'Создать'} <kbd>Enter</kbd>
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => { const t = text; onClose(); navigate('/новая', { state: { text: t } }); }}
            >
              Полная форма
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
