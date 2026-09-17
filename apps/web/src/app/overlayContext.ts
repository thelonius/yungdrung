// Общий реестр открытых оверлеев (SLICE2_SPEC.md §5.2, находка ревью среза 2:
// `Layout` (QuickAdd/HelpOverlay/CommandPalette) и каждая страница считают
// свою область `overlay` независимо друг от друга. Диалог Radix не глушит
// всплытие `keydown` до `document`, а `react-hotkeys-hook` по умолчанию не
// отключается фокусом на кнопке (только на `input`/`textarea`/`select`) —
// поэтому буква, нажатая с фокусом на кнопке внутри открытого QuickAdd,
// долетала до хоткеев страницы под ним. Правило области хоткеев обязано
// учитывать оверлеи **всего дерева**, а не только своего компонента.
//
// Сам провайдер — в `OverlayProvider.tsx` (как `Toaster.tsx`/`toasterContext.ts`):
// здесь только контексты и хуки, без JSX, ради fast refresh.
import { createContext, useContext, useEffect, useId } from 'react';

export type OverlaySet = ReadonlySet<string>;
export type SetOverlay = (id: string, active: boolean) => void;

// Два раздельных контекста, не один объект `{overlays, setOverlay}`: `setOverlay`
// стабилен (не меняется никогда), а `overlays` меняется на каждую регистрацию.
// Общий объект-обёртка менял бы identity вместе с `overlays`, и если положить
// его в зависимости эффекта `useOverlayRegistration`, эффект перезапускался бы
// на **свой же** побочный эффект (снял регистрацию → `overlays` изменился →
// обёртка новая → эффект перезапустился → снял регистрацию → …) — бесконечный
// цикл рендеров, пойманный вручную (зависшие тесты, не сообщение об ошибке).
export const SetOverlayContext = createContext<SetOverlay | null>(null);
export const OverlaysContext = createContext<OverlaySet>(new Set());

/** Сообщает общему реестру, открыт ли оверлей этого компонента (диалог,
 * палитра, QuickAdd, HelpOverlay…). Вызывать безусловно на каждый рендер —
 * компонент сам решает, что такое «открыт», хук только регистрирует итог.
 * Вне `OverlayProvider` — тихий no-op (страничные компоненты в юнит-тестах
 * монтируются без `Layout`, и старое поведение — гейтинг только локальным
 * `overlay` — должно сохраниться без него). */
export function useOverlayRegistration(active: boolean): void {
  const id = useId();
  const setOverlay = useContext(SetOverlayContext);
  useEffect(() => {
    if (!setOverlay) return undefined;
    setOverlay(id, active);
    return () => setOverlay(id, false);
    // `setOverlay` стабилен (см. выше) — в зависимостях безопасен и не
    // вызывает лишних перезапусков; `overlays` здесь нарочно не читается.
  }, [setOverlay, id, active]);
}

/** Открыт ли хоть один зарегистрированный оверлей где угодно в дереве —
 * не только у вызывающего компонента. `false` вне `OverlayProvider`. */
export function useAnyOverlayOpen(): boolean {
  return useContext(OverlaysContext).size > 0;
}
