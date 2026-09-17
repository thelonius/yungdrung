// Лента «Что сегодня» — раздел 6.1 ТЗ, и разбор завала — R20, одним экраном.
//
// Страница не вычисляет ничего: просрочку, состояние и время показа считает
// ядро, здесь показ, клавиши и отправка ответа (CONTRACT.md). Правило среза:
// утренний разбор проходится без мыши, и любая запись отменяется тостом.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useHotkeys } from 'react-hotkeys-hook';
import { useNavigate } from 'react-router';
import type { FeedRow, MarkOp } from '@/api/client';
import { errorText } from '@/api/client';
import { useToaster } from '@/ui/toasterContext';
import { useAnyOverlayOpen, useOverlayRegistration } from '@/app/overlayContext';
import { ControlDialog } from '@/ui/control/ControlDialog';
import type { DialogMode } from '@/ui/control/ControlDialog';
import { CommandPalette } from './CommandPalette';
import { HelpOverlay } from './HelpOverlay';
import { Row } from './Row';
import { clampFocus, moveFocus, outcomeText } from './model';
import { useBacklogQuery, useFeedQuery, useMark, useUndo } from './useFeed';
import styles from './FeedPage.module.css';

type Mode = 'feed' | 'backlog';

export function FeedPage() {
  const feed = useFeedQuery();
  const [mode, setMode] = useState<Mode>('feed');
  const backlog = useBacklogQuery(mode === 'backlog');
  const mark = useMark();
  const undo = useUndo();
  const toaster = useToaster();
  const navigate = useNavigate();

  const rows: FeedRow[] = useMemo(
    () => (mode === 'feed' ? feed.data?.feed ?? [] : backlog.data?.backlog ?? []),
    [mode, feed.data, backlog.data],
  );

  const [focusIdx, setFocusIdx] = useState(-1);
  const idx = clampFocus(focusIdx, rows.length);
  const focused = idx >= 0 ? rows[idx] : null;
  // Пока список непустой, фокус всегда есть: утро начинается с `d`, а не с `j`.
  useEffect(() => { if (focusIdx < 0 && rows.length > 0) setFocusIdx(0); }, [rows.length, focusIdx]);

  const [dialog, setDialog] = useState<{ row: FeedRow; mode: DialogMode } | null>(null);
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);
  const ownOverlay = dialog !== null || palette || help;
  // Регистрация в общем реестре (`app/overlayContext`): хоткеи ленты должны
  // молчать не только при своём диалоге, но и когда поверх открыт QuickAdd
  // или HelpOverlay из `Layout` (находка ревью среза 2). `|| ownOverlay`
  // напрямую — не только через реестр — нужен вне `Layout` (юнит-тесты
  // монтируют страницу без `OverlayProvider`, где `useAnyOverlayOpen`
  // молчаливо отдаёт `false`).
  useOverlayRegistration(ownOverlay);
  const anyOverlay = useAnyOverlayOpen();
  const overlay = ownOverlay || anyOverlay;

  const listRef = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const el = listRef.current?.children[idx] as HTMLElement | undefined;
    // jsdom не реализует scrollIntoView; без проверки эффект бросил бы исключение
    // и React снял бы всё дерево — ровно то, что случилось в первом прогоне тестов.
    el?.scrollIntoView?.({ block: 'nearest' });
  }, [idx]);

  const doMark = useCallback((row: FeedRow, op: MarkOp, extra: { reason?: string | null; to?: string | null } = {}) => {
    setDialog(null);
    mark.mutate({ task_id: row.task_id, step: row.step, op, ...extra }, {
      onSuccess: (r) => {
        toaster.push({
          title: outcomeText(op, r),
          action: {
            label: 'Отменить',
            run: () => undo.mutate({ task_id: r.task_id, step: r.step }, {
              onSuccess: () => toaster.push({ title: 'Отменено' }),
              onError: (e) => toaster.push({ title: errorText(e) || e.message, tone: 'error' }),
            }),
          },
        });
      },
      onError: (e) => toaster.push({ title: e.message, tone: 'error' }),
    });
  }, [mark, undo, toaster]);

  // `/задача?name=…`, не `/задача/:id` напрямую: лента знает только название
  // (`FeedRow.task`), `ResolveTaskRoute` меняет ссылку на id первым переходом
  // (map-client.md, риск 3; таблица маршрутов §5.1 SLICE2_SPEC.md).
  const openCard = useCallback((row: FeedRow) => {
    navigate(`/задача?name=${encodeURIComponent(row.task)}`);
  }, [navigate]);

  // --- клавиши списка; в открытом диалоге и палитре они молчат --------------
  const opts = { enabled: !overlay, preventDefault: true };
  useHotkeys('j, down', () => setFocusIdx((i) => moveFocus(i, 1, rows.length)), opts, [rows.length]);
  useHotkeys('k, up', () => setFocusIdx((i) => moveFocus(i, -1, rows.length)), opts, [rows.length]);
  useHotkeys('d', () => { if (focused) doMark(focused, 'done'); }, opts, [focused, doMark]);
  useHotkeys('n', () => { if (focused) doMark(focused, 'notdone'); }, opts, [focused, doMark]);
  useHotkeys('t', () => { if (focused) setDialog({ row: focused, mode: 'defer' }); }, opts, [focused]);
  useHotkeys('f', () => { if (focused) setDialog({ row: focused, mode: 'fail' }); }, opts, [focused]);
  useHotkeys('enter', () => { if (focused) openCard(focused); }, opts, [focused, openCard]);
  useHotkeys('b', () => setMode('backlog'), opts, []);
  useHotkeys('escape', () => { if (mode === 'backlog') setMode('feed'); }, opts, [mode]);
  useHotkeys('u', () => { toaster.runLatestAction(); }, { enabled: !overlay }, [toaster]);
  useHotkeys('shift+slash', () => setHelp(true), opts, []);
  useHotkeys('mod+k, slash', () => setPalette(true), { preventDefault: true, enableOnFormTags: false }, []);

  const counts = feed.data?.counts;
  const overdue = feed.data?.overdue_count ?? 0;

  return (
    <main className="wrap">
      <header className={styles.head}>
        <div className={styles.headRow}>
          <h1 className={styles.h1}>{mode === 'feed' ? 'Что сегодня' : 'Разбор завала'}</h1>
        </div>
        {counts && (
          <div className={styles.counters}>
            <span className={overdue > 0 ? styles.hot : ''}>просрочено<b>{counts.overdue}</b></span>
            <span>сегодня<b>{counts.today}</b></span>
            <a href="/задачи" className="muted">ждут<b>{counts.waiting}</b></a>
          </div>
        )}
      </header>

      {mode === 'feed' && overdue > 0 && (
        <button type="button" className={styles.plate} onClick={() => setMode('backlog')}>
          <span>Просрочено: {overdue}</span>
          <span>Разобрать <kbd>b</kbd></span>
        </button>
      )}

      {mode === 'backlog' && (
        <div className={styles.queueHead}>
          <h2>{rows.length ? `${idx + 1} из ${rows.length}` : 'Завал пуст'}</h2>
          <button type="button" className="ghost small" onClick={() => setMode('feed')}>
            Назад к ленте <kbd>Esc</kbd>
          </button>
        </div>
      )}

      {feed.isError && <p className="err">{feed.error.message}</p>}

      <ol className={styles.list} ref={listRef} role="listbox" aria-label={mode === 'feed' ? 'Лента' : 'Завал'}>
        {rows.map((r, i) => (
          <Row
            key={`${r.task_id}:${r.step}`}
            row={r}
            focused={i === idx}
            showDate={mode === 'backlog'}
            onFocus={() => setFocusIdx(i)}
            onDone={() => doMark(r, 'done')}
            onMore={() => setDialog({ row: r, mode: 'menu' })}
          />
        ))}
      </ol>

      {feed.isSuccess && rows.length === 0 && mode === 'feed' && (
        <p className={styles.empty} data-testid="empty">
          {feed.data.next_ahead
            ? <>На сегодня всё. Дальше: «{feed.data.next_ahead.title}» — {feed.data.next_ahead.task}.</>
            : 'На сегодня всё.'}
        </p>
      )}

      <footer className={styles.footer}>
        <span><kbd>d</kbd> сделан</span>
        <span><kbd>n</kbd> не сделан</span>
        <span><kbd>t</kbd> перенести</span>
        <span><kbd>u</kbd> отменить</span>
        <span><kbd>Ctrl+K</kbd> палитра</span>
        <button type="button" className="quiet small" onClick={() => setHelp(true)}>все клавиши <kbd>?</kbd></button>
      </footer>

      <ControlDialog
        row={dialog?.row ?? null}
        mode={dialog?.mode ?? 'menu'}
        progress={mode === 'backlog' && rows.length ? `${idx + 1} из ${rows.length}` : undefined}
        onClose={() => setDialog(null)}
        onMark={(op, extra) => { if (dialog) doMark(dialog.row, op, extra); }}
      />
      <CommandPalette
        open={palette}
        onClose={() => setPalette(false)}
        focused={focused}
        onMark={(op) => { if (focused) doMark(focused, op); }}
        onOpenDialog={(m) => { if (focused) setDialog({ row: focused, mode: m }); }}
      />
      <HelpOverlay open={help} onClose={() => setHelp(false)} />
    </main>
  );
}
