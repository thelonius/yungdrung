import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HotkeysProvider } from 'react-hotkeys-hook';
import { FeedPage } from '@/features/feed/FeedPage';
import { ToasterProvider } from '@/ui/Toaster';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 10_000 } },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <HotkeysProvider>
        <ToasterProvider>
          <FeedPage />
        </ToasterProvider>
      </HotkeysProvider>
    </QueryClientProvider>
  );
}
