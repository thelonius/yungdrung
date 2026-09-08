import { memo } from 'react';
import type { FeedRow } from '@/api/client';
import { shortDate, shortTime } from './model';
import styles from './FeedPage.module.css';

type Props = {
  row: FeedRow;
  focused: boolean;
  showDate?: boolean;
  onFocus: () => void;
  onDone: () => void;
  onMore: () => void;
};

// Мемоизирована: отметка перерисовывает одну строку, а не всю ленту
// (REFACTOR.md, пункт 7 целевого UX).
export const Row = memo(function Row({ row, focused, showDate, onFocus, onDone, onMore }: Props) {
  return (
    <li
      className={`${styles.row} ${focused ? styles.focused : ''} ${row.stalled ? styles.stalled : ''}`}
      data-testid="feed-row"
      data-key={`${row.task_id}:${row.step}`}
      aria-selected={focused}
      onMouseEnter={onFocus}
      onDoubleClick={onMore}
    >
      <div className={styles.main}>
        <div className={styles.title} title={row.title ?? ''}>{row.title}</div>
        <div className={styles.sub}>
          <span>{row.group ? `${row.task} · ${row.group}` : row.task}</span>
          {row.show_at && (
            <span className={styles.time}>
              {showDate ? `${shortDate(row.show_at)} ` : ''}{shortTime(row.show_at)}
            </span>
          )}
          {row.tags.map((t) => <span key={t} className={styles.tag}>{t}</span>)}
          {row.postponed > 0 && (
            <span className={styles.postponed}>
              {row.stalled ? `буксует, переносов ${row.postponed}` : `переносов ${row.postponed}`}
            </span>
          )}
        </div>
      </div>
      <div className={styles.actions}>
        <button type="button" className={styles.tick} title="Сделано (d)" onClick={onDone}>✓</button>
        <button type="button" className={styles.more} title="Окно контроля (t / f)" onClick={onMore}>⋯</button>
      </div>
    </li>
  );
});
