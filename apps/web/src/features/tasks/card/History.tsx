// История — п.12 §5.5: кнопка-тоггл со счётчиком, список уже отсортирован
// ядром (по убыванию даты), события переводятся в `model.ts::historyLine`.
import { useState } from 'react';
import type { JSX } from 'react';
import type { LogEntry } from '@/api/client';
import { historyLine } from './model';
import styles from './TaskCardPage.module.css';

type Props = { history: LogEntry[] };

export function History({ history }: Props): JSX.Element {
  const [open, setOpen] = useState(false);
  if (history.length === 0) return <></>;

  return (
    <section className={styles.history}>
      <button type="button" className="quiet small" onClick={() => setOpen((v) => !v)}>
        История ({history.length}) {open ? '▲' : '▼'}
      </button>
      {open && (
        <ul className={styles.historyList}>
          {history.map((e, i) => (
            // Индекс как ключ: у событий журнала нет id, порядок стабилен (список не пересортировывается).
            <li key={`${e.step_id}-${e.date}-${e.event}-${i}`}>{historyLine(e)}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
