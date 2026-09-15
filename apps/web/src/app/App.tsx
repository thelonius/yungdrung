// Точка сборки: провайдеры → роутер → шапка/слот страницы. Срез 1c вписывал
// `FeedPage` жёстко (маршрутизатора не было); срез 2 заводит react-router
// (SLICE2_SPEC.md §5.1) — `FeedPage` остаётся на `/` и `/лента`, просто уже
// как маршрут, а не единственная страница.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HotkeysProvider } from 'react-hotkeys-hook';
import { BrowserRouter } from 'react-router';
import { ToasterProvider } from '@/ui/Toaster';
import { AppRouter } from './router';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 10_000 } },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <HotkeysProvider>
        <ToasterProvider>
          <BrowserRouter>
            <AppRouter />
          </BrowserRouter>
        </ToasterProvider>
      </HotkeysProvider>
    </QueryClientProvider>
  );
}
