// ЗАГЛУШКА (владелец F1, SLICE2_SPEC.md §5.3). Сигнатура зафиксирована срезом
// 2 §5.1, чтобы `Layout.tsx` компилировался и подключал компонент по клавише
// `a` до того, как F1 сдаст форму, — тело меняется, `Props` нет.
import type { JSX } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import styles from '@/ui/control/ControlDialog.module.css';

export type Props = { open: boolean; onClose: () => void };

export function QuickAdd({ open, onClose }: Props): JSX.Element {
  return (
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={styles.content} aria-describedby={undefined}>
          <Dialog.Title className={styles.step}>Быстрый ввод</Dialog.Title>
          <p className="muted">
            Заглушка F1 (SLICE2_SPEC.md §5.3): поле текста с разбором даты через
            {' '}<code>/api/v1/extract-when</code> и создание через{' '}
            <code>/api/v1/tasks/quick</code> появятся здесь.
          </p>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
