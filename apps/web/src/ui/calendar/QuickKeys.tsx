// Легенда быстрых клавиш под сеткой: человек не обязан помнить буквы, он их
// видит. Отдельным файлом от `keys.ts` — там только данные, и смешанный
// экспорт ломал бы горячую перезагрузку (react-refresh).
import { БЫСТРЫЕ } from './keys';
import styles from './MonthGrid.module.css';

export function QuickKeys() {
  return (
    <p className={styles.keys}>
      {БЫСТРЫЕ.map((б) => (
        <span key={б.label}><kbd>{б.label}</kbd> {б.hint}</span>
      ))}
      <span><kbd>стрелки</kbd> день и неделя</span>
      <span><kbd>Shift</kbd>+<kbd>стрелки</kbd> месяц и год</span>
      <span><kbd>Enter</kbd> выбрать</span>
      <span><kbd>Esc</kbd> назад в поле</span>
    </p>
  );
}
