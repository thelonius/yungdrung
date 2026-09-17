// Легенда быстрых клавиш под сеткой: человек не обязан помнить буквы, он их
// видит. Отдельным файлом от `keys.ts` — там только данные, и смешанный
// экспорт ломал бы горячую перезагрузку (react-refresh).
//
// Первой стоит русская крышка: в ней буквы и выбраны по смыслу. Латинская
// рядом для тех, у кого сейчас включена она — кнопка одна и та же.
import { БЫСТРЫЕ } from './keys';
import styles from './MonthGrid.module.css';

export function QuickKeys() {
  return (
    <>
      <p className={styles.keys}>
        {БЫСТРЫЕ.map((б) => (
          <span key={б.hotkey}>
            <kbd>{б.ru}</kbd><span className={styles.slash}>/</span><kbd>{б.en}</kbd> {б.hint}
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
        <span><kbd>н</kbd> и <kbd>м</kbd> шагают от того дня, где стоит курсор,
          и жмутся сколько нужно; остальные считаются от сегодня. Пока курсор
          в поле — те же кнопки с <kbd>Alt</kbd></span>
      </p>
    </>
  );
}
