import { createContext, useContext } from 'react';

export type ToastAction = { label: string; run: () => void };
export type ToastItem = { id: number; title: string; action?: ToastAction; tone?: 'ok' | 'error' };

export type ToasterApi = {
  push: (t: Omit<ToastItem, 'id'>) => void;
  /** Выполнить действие самого свежего тоста с действием, если он ещё виден. */
  runLatestAction: () => boolean;
};

export const ToasterContext = createContext<ToasterApi | null>(null);

export const UNDO_MS = 6000;

export function useToaster(): ToasterApi {
  const ctx = useContext(ToasterContext);
  if (!ctx) throw new Error('useToaster вне ToasterProvider');
  return ctx;
}
