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

/** Раскраска сетки месяца: выходные по настройкам заказчика и сколько
 *  контролей уже стоит на каждом дне. Браузер это не считает — по контракту
 *  выходные знает только ядро (при работе по выходным суббота рабочая), а
 *  занятость дня ему и подавно неоткуда взять.
 *
 *  Запрашивается только при открытом календаре: `from` пустой — запроса нет. */
export function useCalendar(from: string | null, to: string | null) {
  return useQuery({
    queryKey: ['calendar', from, to],
    enabled: !!from && !!to,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/calendar', {
        params: { query: { from: from!, to: to! } },
      });
      if (error || data === undefined) throw new Error(errorText(error));
      return new Map(data.days.map((d) => [d.date, d]));
    },
    staleTime: 60_000,
  });
}
