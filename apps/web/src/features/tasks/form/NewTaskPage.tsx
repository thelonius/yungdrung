// Страница «Новая задача» (SLICE2_SPEC.md §5.4). `location.state.text` —
// перенос текста со ссылки «Полная форма» из `QuickAdd` (§5.3): то, что
// человек успел набрать в быстром вводе, не пропадает, если он решил
// расписать задачу по шагам.
import type { JSX } from 'react';
import { useLocation } from 'react-router';
import { TaskForm } from './TaskForm';
import styles from './NewTaskPage.module.css';

type NavState = { text?: string } | null;

export function NewTaskPage(): JSX.Element {
  const location = useLocation();
  const state = location.state as NavState;

  return (
    <main className="wrap">
      <header className={styles.head}>
        <h1>Новая задача</h1>
        <p className="muted">Enter в шаге добавляет следующий · Ctrl+Enter сохраняет</p>
      </header>
      <TaskForm initialTitle={state?.text ?? ''} />
    </main>
  );
}
