// Общая рамка приложения: шапка со ссылками (`PAGES`) и слот страницы
// (`Outlet`). Глобальные клавиши `a` (быстрый ввод), `?` (карта клавиш) и
// `Ctrl+K`/`/` (палитра) живут здесь — кроме ленты: она уже ведёт их сама
// (свой экземпляр `CommandPalette` с доступом к строке под фокусом), и
// повторная привязка тех же клавиш здесь дала бы двойной диалог/двойной
// тост на `/` (SLICE2_SPEC.md §5.1, §5.2).
import { useState } from 'react';
import { useHotkeys } from 'react-hotkeys-hook';
import { Link, Outlet, useLocation } from 'react-router';
import { useToaster } from '@/ui/toasterContext';
import { CommandPalette } from '@/features/feed/CommandPalette';
import { HelpOverlay } from '@/features/feed/HelpOverlay';
import { QuickAdd } from '@/features/tasks/quick/QuickAdd';
import { OverlayProvider } from './OverlayProvider';
import { useAnyOverlayOpen, useOverlayRegistration } from './overlayContext';
import { PAGES } from './pages';
import styles from './Layout.module.css';

const FEED_ROUTES = new Set(['/', '/лента']);

export function Layout() {
  // Провайдер — здесь, а не в `App.tsx`: `Layout` и все страницы за `Outlet`
  // должны делить один реестр, но остальное дерево (провайдеры запросов,
  // роутер) реестру не нужно.
  return (
    <OverlayProvider>
      <LayoutContent />
    </OverlayProvider>
  );
}

function LayoutContent() {
  const location = useLocation();
  const toaster = useToaster();
  const onFeed = FEED_ROUTES.has(location.pathname);

  const [quickOpen, setQuickOpen] = useState(false);
  const [help, setHelp] = useState(false);
  const [palette, setPalette] = useState(false);
  const ownOverlay = quickOpen || help || palette;
  // Гейтинг собственных клавиш — по **общему** реестру (находка ревью): свой
  // `overlay` регистрируется в нём же, поэтому `anyOverlay` уже включает его,
  // плюс любой оверлей открытой сейчас страницы (диалог карточки, палитра
  // шаблонов и т.п.) — `a` не должен открывать второй диалог поверх первого.
  useOverlayRegistration(ownOverlay);
  const anyOverlay = useAnyOverlayOpen();

  useHotkeys('a', () => setQuickOpen(true), { enabled: !anyOverlay, preventDefault: true }, [anyOverlay]);
  useHotkeys('shift+slash', () => setHelp(true),
    { enabled: !anyOverlay && !onFeed, preventDefault: true }, [anyOverlay, onFeed]);
  useHotkeys('mod+k, slash', () => setPalette(true),
    { enabled: !onFeed, preventDefault: true, enableOnFormTags: false }, [onFeed]);
  useHotkeys('u', () => { toaster.runLatestAction(); }, { enabled: !onFeed }, [onFeed, toaster]);

  return (
    <>
      <header className={styles.bar}>
        <Link to="/" className={styles.brand}>Yungdrung</Link>
        <nav className={styles.nav}>
          {PAGES.map((p) => ('to' in p
            ? <Link key={p.to} to={p.to}>{p.label}</Link>
            : <a key={p.href} href={p.href}>{p.label}</a>))}
        </nav>
      </header>

      <Outlet />

      <QuickAdd open={quickOpen} onClose={() => setQuickOpen(false)} />
      <HelpOverlay open={help} onClose={() => setHelp(false)} />
      <CommandPalette
        open={palette}
        onClose={() => setPalette(false)}
        focused={null}
        onMark={() => {}}
        onOpenDialog={() => {}}
        onQuickAdd={() => setQuickOpen(true)}
      />
    </>
  );
}
