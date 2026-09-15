// Список шаблонов — раздел 5.6 ТЗ (R22). Страница не считает ничего сама:
// метаданные карточки, предпросмотр дат и подпись повторения отдаёт ядро
// (CONTRACT.md); здесь показ, клавиши и три диалога поверх списка.
import { useEffect, useRef, useState } from 'react';
import { useHotkeys } from 'react-hotkeys-hook';
import type { TemplateCard as TemplateCardData } from '@/api/client';
import { useAnyOverlayOpen, useOverlayRegistration } from '@/app/overlayContext';
import { clampFocus, moveFocus } from '@/features/feed/model';
import { useTemplatesQuery } from './useTemplates';
import { TemplateCard } from './TemplateCard';
import { InstantiateDialog } from './InstantiateDialog';
import { TemplateForm } from './TemplateForm';
import { RecurrenceForm } from './RecurrenceForm';
import styles from './TemplatesPage.module.css';

export function TemplatesPage() {
  const list = useTemplatesQuery();
  const templates = list.data?.templates ?? [];

  const [focusIdx, setFocusIdx] = useState(-1);
  const idx = clampFocus(focusIdx, templates.length);
  const focused = idx >= 0 ? templates[idx] : null;
  useEffect(() => { if (focusIdx < 0 && templates.length > 0) setFocusIdx(0); }, [templates.length, focusIdx]);

  const [instantiating, setInstantiating] = useState<TemplateCardData | null>(null);
  const [form, setForm] = useState<{ open: boolean; initial: TemplateCardData | null }>({ open: false, initial: null });
  const [recurring, setRecurring] = useState<TemplateCardData | null>(null);
  const ownOverlay = instantiating !== null || form.open || recurring !== null;
  // Общий реестр оверлеев (`app/overlayContext`, находка ревью среза 2):
  // `x`/`e`/`Enter` под фокусом должны молчать и при открытом QuickAdd из
  // `Layout`, не только при своих трёх диалогах.
  useOverlayRegistration(ownOverlay);
  const anyOverlay = useAnyOverlayOpen();
  const overlay = ownOverlay || anyOverlay;

  const listRef = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const el = listRef.current?.children[idx] as HTMLElement | undefined;
    el?.scrollIntoView?.({ block: 'nearest' });
  }, [idx]);

  const opts = { enabled: !overlay, preventDefault: true };
  useHotkeys('j, down', () => setFocusIdx((i) => moveFocus(i, 1, templates.length)), opts, [templates.length]);
  useHotkeys('k, up', () => setFocusIdx((i) => moveFocus(i, -1, templates.length)), opts, [templates.length]);
  useHotkeys('enter', () => { if (focused) setInstantiating(focused); }, opts, [focused]);
  useHotkeys('e', () => { if (focused) setForm({ open: true, initial: focused }); }, opts, [focused]);
  // `x` только вооружает `ConfirmButton` под фокусом (как в задании §5.2 —
  // «кнопка получает фокус, Enter подтверждает»): второй шаг делает сама
  // кнопка, страница туда не лезет (см. TemplateCard, `data-role="tpl-delete"`).
  useHotkeys('x', () => {
    const row = listRef.current?.children[idx] as HTMLElement | undefined;
    const btn = row?.querySelector<HTMLButtonElement>('[data-role="tpl-delete"] button');
    btn?.click();
  }, opts, [idx]);

  return (
    <main className="wrap">
      <header className={styles.head}>
        <h1 className={styles.h1}>Шаблоны</h1>
        <button type="button" className="primary small" onClick={() => setForm({ open: true, initial: null })}>
          + Новый шаблон
        </button>
      </header>

      {list.isError && <p className="err">{list.error.message}</p>}

      {list.isSuccess && templates.length === 0 && (
        <p className={styles.empty} data-testid="empty">
          Шаблонов пока нет — заведи первый кнопкой выше.
        </p>
      )}

      <ol className={styles.list} ref={listRef} role="listbox" aria-label="Шаблоны">
        {templates.map((t, i) => (
          <TemplateCard
            key={t.name}
            t={t}
            focused={i === idx}
            onFocus={() => setFocusIdx(i)}
            onInstantiate={() => setInstantiating(t)}
            onEdit={() => setForm({ open: true, initial: t })}
            onRecurrence={() => setRecurring(t)}
          />
        ))}
      </ol>

      <InstantiateDialog template={instantiating} onClose={() => setInstantiating(null)} />
      <TemplateForm open={form.open} initial={form.initial} onClose={() => setForm({ open: false, initial: null })} />
      <RecurrenceForm template={recurring} onClose={() => setRecurring(null)} />
    </main>
  );
}
