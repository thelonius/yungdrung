// Легенда быстрых клавиш под сеткой: человек не обязан помнить буквы, он их
// видит. Отдельным файлом от `keys.ts` — там только данные, и смешанный
// экспорт ломал бы горячую перезагрузку (react-refresh).
//
// На кнопке подписаны обе крышки, латинская и русская: опознаётся место на
// клавиатуре, поэтому раскладку переключать не нужно, а глазами человек
// ищет ту букву, которая у него сейчас нарисована.
import { БЫСТРЫЕ } from './keys';
import styles from './MonthGrid.module.css';

export function QuickKeys() {
  return (
    <>
      <p className={styles.keys}>
        {БЫСТРЫЕ.map((б) => (
          <span key={б.label}>
            <kbd>{б.label}</kbd><span className={styles.slash}>/</span><kbd>{б.ru}</kbd> {б.hint}
          </span>
        ))}
      </p>
      <p className={styles.keys}>
        <span><kbd>стрелки</kbd> день и неделя</span>
        <span><kbd>Shift</kbd>+<kbd>стрелки</kbd> месяц и год</span>
        <span><kbd>Enter</kbd> выбрать</span>
        <span><kbd>Esc</kbd> назад в поле</span>
      </p>
      <p className={styles.keys}>
        <span>буквы работают с любым фокусом и любой раскладкой; пока курсор
          в поле — те же кнопки с <kbd>Alt</kbd></span>
      </p>
    </>
  );
}
