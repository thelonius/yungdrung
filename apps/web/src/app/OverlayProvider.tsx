// Провайдер общего реестра оверлеев — контексты и хуки в `overlayContext.ts`
// (тот же приём разделения, что `Toaster.tsx`/`toasterContext.ts`: файл с
// JSX отдельно от файла с одними хуками, ради fast refresh).
import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { OverlaysContext, SetOverlayContext } from './overlayContext';
import type { OverlaySet, SetOverlay } from './overlayContext';

export function OverlayProvider({ children }: { children: ReactNode }) {
  const [overlays, setOverlays] = useState<OverlaySet>(() => new Set());
  const setOverlay = useMemo<SetOverlay>(() => (id, active) => {
    setOverlays((prev) => {
      if (active === prev.has(id)) return prev;
      const next = new Set(prev);
      if (active) next.add(id); else next.delete(id);
      return next;
    });
  }, []);
  return (
    <SetOverlayContext.Provider value={setOverlay}>
      <OverlaysContext.Provider value={overlays}>{children}</OverlaysContext.Provider>
    </SetOverlayContext.Provider>
  );
}
