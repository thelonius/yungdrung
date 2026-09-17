// Очередь файлов новой задачи/шаблона до первого сохранения (SLICE2_SPEC.md
// §5.6/§5.7): сущности ещё нет на сервере, `Owner` появится только после
// успешного `POST /templates` или `POST /tasks` — до этого момента файлы
// живут в состоянии формы и никуда не уходят (см. `uploadPending` в
// `useAttachments.ts`, который вызывающий зовёт уже после сохранения).
import type { ChangeEvent, JSX } from 'react';
import { sizeText } from './model';
import styles from './AttachmentList.module.css';

export type Props = { files: File[]; onChange: (files: File[]) => void };

export function PendingFiles(props: Props): JSX.Element {
  const { files, onChange } = props;

  function addFiles(e: ChangeEvent<HTMLInputElement>) {
    const picked = [...(e.target.files ?? [])];
    e.target.value = '';
    if (picked.length > 0) onChange([...files, ...picked]);
  }

  function remove(index: number) {
    onChange(files.filter((_, i) => i !== index));
  }

  return (
    <div className={styles.root}>
      {files.length > 0 && (
        <ul className={styles.list}>
          {files.map((file, i) => (
            // Имя не уникальный ключ (два одинаковых скриншота подряд), индекс
            // в паре с именем достаточно стабилен для списка без drag&drop.
            <li key={`${i}-${file.name}`} className={styles.row} data-testid="pending-file">
              <span title={file.name}>{file.name}</span>
              <span className={styles.size}>{sizeText(file.size)}</span>
              <button type="button" className={styles.remove} title="Убрать файл" onClick={() => remove(i)}>×</button>
            </li>
          ))}
        </ul>
      )}
      <label className={styles.add}>
        + Файл
        <input type="file" hidden multiple onChange={addFiles} />
      </label>
    </div>
  );
}
