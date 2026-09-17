// Карта клавиш — раздел «?» из карты клавиш срезов 1c/2 (SLICE2_SPEC.md §5.2).
// Группы по областям: лента/завал уже живые, карточка/форма/шаблоны —
// заготовки под будущие страницы (F1–F3 наполняют их вместе с формами;
// таблица здесь не должна разойтись с их клавишами).
import * as Dialog from '@radix-ui/react-dialog';
import styles from '@/ui/control/ControlDialog.module.css';

type KeyGroup = { title: string; keys: [string, string][] };

const GROUPS: KeyGroup[] = [
  {
    title: 'Везде',
    keys: [
      ['a', 'быстрый ввод — задача одной строкой'],
      ['/, Ctrl+K', 'палитра: страницы, поиск, действия'],
      ['?', 'эта подсказка'],
      ['u', 'отменить последнюю отметку, пока виден тост'],
    ],
  },
  {
    title: 'Лента и завал',
    keys: [
      ['j / k, ↓ / ↑', 'фокус по строкам'],
      ['d', 'сделан'],
      ['n', 'не сделан, без причины — спросит завтра'],
      ['t', 'перенести: поле даты сразу в фокусе, Enter сохраняет'],
      ['f', 'не будет сделано: причина обязательна'],
      ['Enter', 'карточка задачи'],
      ['b', 'разбор завала; Esc — назад к ленте'],
    ],
  },
  {
    title: 'Карточка задачи',
    keys: [
      ['j / k', 'фокус по шагам'],
      ['d / n / t / f', 'отметки шага под фокусом'],
      ['Enter', 'открыть/закрыть редактор шага'],
      ['r', 'переоткрыть сделанный шаг'],
      ['e', 'правка названия задачи'],
      ['Ctrl+Enter', 'сохранить карточку'],
    ],
  },
  {
    title: 'Новая задача',
    keys: [
      ['Enter', 'по полям: название → теги → шаг → дата шага → следующий шаг'],
      ['Ctrl+Enter', 'создать задачу'],
    ],
  },
  {
    title: 'Шаблоны',
    keys: [
      ['j / k', 'фокус по карточкам шаблонов'],
      ['Enter', 'завести задачу из шаблона под фокусом'],
      ['e', 'править шаблон под фокусом'],
      ['x', 'удалить (кнопка требует второго подтверждения)'],
    ],
  },
];

export function HelpOverlay({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={styles.content} aria-describedby={undefined}>
          <Dialog.Title className={styles.step}>Клавиши</Dialog.Title>
          {GROUPS.map((g) => (
            <section key={g.title} style={{ marginTop: 12 }}>
              <h2 style={{ margin: '0 0 6px' }}>{g.title}</h2>
              <table style={{ borderSpacing: '0 6px' }}>
                <tbody>
                  {g.keys.map(([k, what]) => (
                    <tr key={k}>
                      <td style={{ paddingRight: 16, whiteSpace: 'nowrap' }}><kbd>{k}</kbd></td>
                      <td>{what}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ))}
          <footer className={styles.foot}><span className={styles.hint}>Esc — закрыть</span></footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
