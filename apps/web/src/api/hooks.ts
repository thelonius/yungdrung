// Мелкие серверные запросы общего пользования — не привязаны к ленте, нужны
// и окну контроля, и будущей карточке (перенесено из
// `features/feed/useFeed.ts`, срез 2 §5.1: `ControlDialog` переехал в
// `ui/control/`, и держать его данные в `features/feed` стало бы циклом
// «ui зависит от features»).
import { useQuery } from '@tanstack/react-query';
import { api, errorText } from './client';

export function useReasons() {
  return useQuery({
    queryKey: ['reasons'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/reasons');
      if (error || data === undefined) throw new Error(errorText(error));
      return data.reasons;
    },
    staleTime: 10 * 60_000,
  });
}
