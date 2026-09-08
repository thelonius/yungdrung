import * as Dialog from '@radix-ui/react-dialog';
import styles from './ControlDialog.module.css';

const KEYS: [string, string][] = [
  ['j / k, ↓ / ↑', 'фокус по строкам'],
  ['d', 'сделан'],
  ['n', 'не сделан, без причины — спросит завтра'],
  ['t', 'перенести: поле даты сразу в фокусе, Enter сохраняет'],
  ['f', 'не будет сделано: причина обязательна'],
  ['Enter', 'карточка задачи'],
  ['u', 'отменить последнюю отметку, пока виден тост'],
  ['b', 'разбор завала; Esc — назад к ленте'],
  ['Ctrl+K', 'палитра: страницы, поиск, действия'],
  ['?', 'эта подсказка'],
];

export function HelpOverlay({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={styles.content} aria-describedby={undefined}>
          <Dialog.Title className={styles.step}>Клавиши</Dialog.Title>
          <table style={{ borderSpacing: '0 6px' }}>
            <tbody>
              {KEYS.map(([k, what]) => (
                <tr key={k}>
                  <td style={{ paddingRight: 16, whiteSpace: 'nowrap' }}><kbd>{k}</kbd></td>
                  <td>{what}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <footer className={styles.foot}><span className={styles.hint}>Esc — закрыть</span></footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
