// Тосты с действием. Radix держит доступность и таймер; здесь — очередь и
// «последнее действие», чтобы клавиша `u` отменяла то, что только что случилось.
import * as Toast from '@radix-ui/react-toast';
import { useCallback, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ToasterContext, UNDO_MS } from './toasterContext';
import type { ToastItem } from './toasterContext';
import styles from './Toaster.module.css';

export function ToasterProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback((id: number) => {
    setItems((xs) => xs.filter((x) => x.id !== id));
  }, []);

  const push = useCallback((t: Omit<ToastItem, 'id'>) => {
    const id = ++seq.current;
    setItems((xs) => [...xs.slice(-2), { ...t, id }]);
  }, []);

  const runLatestAction = useCallback(() => {
    const last = [...items].reverse().find((x) => x.action);
    if (!last?.action) return false;
    last.action.run();
    dismiss(last.id);
    return true;
  }, [items, dismiss]);

  const value = useMemo(() => ({ push, runLatestAction }), [push, runLatestAction]);

  return (
    <ToasterContext.Provider value={value}>
      <Toast.Provider duration={UNDO_MS} swipeDirection="right">
        {children}
        {items.map((t) => (
          <Toast.Root
            key={t.id}
            className={`${styles.toast} ${t.tone === 'error' ? styles.error : ''}`}
            open
            onOpenChange={(open) => { if (!open) dismiss(t.id); }}
            duration={t.tone === 'error' ? 8000 : UNDO_MS}
          >
            <Toast.Title className={styles.title}>{t.title}</Toast.Title>
            {t.action && (
              <Toast.Action asChild altText={t.action.label}>
                <button
                  type="button"
                  className={`ghost small ${styles.action}`}
                  onClick={() => { t.action!.run(); dismiss(t.id); }}
                >
                  {t.action.label} <kbd>u</kbd>
                </button>
              </Toast.Action>
            )}
          </Toast.Root>
        ))}
        <Toast.Viewport className={styles.viewport} />
      </Toast.Provider>
    </ToasterContext.Provider>
  );
}
