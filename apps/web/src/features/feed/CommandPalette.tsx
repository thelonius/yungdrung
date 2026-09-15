// Палитра команд (Ctrl+K): страницы, поиск по задачам через /api/v1/search и
// действия над строкой под фокусом. Одна точка входа вместо шести ссылок в шапке.
import { Command } from 'cmdk';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { api } from '@/api/client';
import type { FeedRow, MarkOp } from '@/api/client';
import { hitHref, isInternalPath, PAGES } from '@/app/pages';
import type { Hit, Page } from '@/app/pages';
import styles from './CommandPalette.module.css';

type Props = {
  open: boolean;
  onClose: () => void;
  focused: FeedRow | null;
  onMark: (op: MarkOp) => void;
  onOpenDialog: (mode: 'defer' | 'fail') => void;
};

export function CommandPalette({ open, onClose, focused, onMark, onOpenDialog }: Props) {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<Hit[]>([]);
  const navigate = useNavigate();

  useEffect(() => { if (!open) { setQ(''); setHits([]); } }, [open]);

  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    const t = window.setTimeout(async () => {
      const { data } = await api.GET('/api/v1/search', { params: { query: { q, limit: 12 } } });
      setHits(((data?.results ?? []) as unknown as Hit[]));
    }, 180);
    return () => window.clearTimeout(t);
  }, [q]);

  // Внутренние маршруты переходят через роутер (без перезагрузки), внешние —
  // как раньше, обычной ссылкой: страница ещё не переехала на React.
  function go(path: string) {
    onClose();
    if (isInternalPath(path)) navigate(path);
    else window.location.href = path;
  }

  function goPage(p: Page) {
    onClose();
    if ('to' in p) navigate(p.to);
    else if (isInternalPath(p.href)) navigate(p.href);
    else window.location.href = p.href;
  }

  return (
    <Command.Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}
      label="Палитра команд" className={styles.dialog} overlayClassName={styles.overlay}
      contentClassName={styles.content} loop>
      <Command.Input value={q} onValueChange={setQ} placeholder="Куда или что…" className={styles.input} />
      <Command.List className={styles.list}>
        <Command.Empty className={styles.empty}>Ничего не нашлось</Command.Empty>
        {focused && (
          <Command.Group heading={`Шаг: ${focused.title}`} className={styles.group}>
            <Command.Item onSelect={() => { onClose(); onMark('done'); }}>Сделано <kbd>d</kbd></Command.Item>
            <Command.Item onSelect={() => { onClose(); onMark('notdone'); }}>Не сделано <kbd>n</kbd></Command.Item>
            <Command.Item onSelect={() => { onClose(); onOpenDialog('defer'); }}>Перенести <kbd>t</kbd></Command.Item>
            <Command.Item onSelect={() => { onClose(); onOpenDialog('fail'); }}>Не будет сделано <kbd>f</kbd></Command.Item>
            <Command.Item onSelect={() => go(`/задача?name=${encodeURIComponent(focused.task)}`)}>
              Открыть карточку <kbd>Enter</kbd>
            </Command.Item>
          </Command.Group>
        )}
        {hits.length > 0 && (
          <Command.Group heading="Найдено" className={styles.group}>
            {hits.map((h) => (
              <Command.Item key={`${h.source_type}:${h.source_id}`} value={`${h.title} ${h.source_id}`}
                onSelect={() => go(hitHref(h))}>
                <span>{h.title}</span>
                {h.preview && <span className={styles.preview}>{h.preview}</span>}
              </Command.Item>
            ))}
          </Command.Group>
        )}
        <Command.Group heading="Страницы" className={styles.group}>
          {PAGES.map((p) => (
            <Command.Item key={'to' in p ? p.to : p.href} value={p.label} onSelect={() => goPage(p)}>
              {p.label}
            </Command.Item>
          ))}
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}
