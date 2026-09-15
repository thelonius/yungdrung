// Серверное состояние ленты: запросы и мутации через TanStack Query.
//
// Оптимистичная отметка: строка уходит сразу (`onMutate`), ответ ядра
// подменяет счётчики (`onSuccess`), ошибка откатывает снимок (`onError`).
// Откат TanStack закрывает упавший запрос; пользовательская «Отменить» на
// успешной записи — это `undo` в ядре, библиотекой не заменяется (REFACTOR.md).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, errorText, rowKey } from '@/api/client';
import type { BacklogResult, FeedResult, MarkOp, MarkResult } from '@/api/client';
import { applyMark, applyServer, withoutRow } from './model';

export const FEED = ['feed'] as const;
export const BACKLOG = ['backlog'] as const;

async function unwrap<T>(p: Promise<{ data?: T; error?: unknown }>): Promise<T> {
  const { data, error } = await p;
  if (error || data === undefined) throw new Error(errorText(error));
  return data;
}

export function useFeedQuery() {
  return useQuery({
    queryKey: FEED,
    queryFn: () => unwrap<FeedResult>(api.GET('/api/v1/feed')),
    refetchOnWindowFocus: true,
    refetchInterval: 5 * 60_000,
  });
}

export function useBacklogQuery(enabled: boolean) {
  return useQuery({
    queryKey: BACKLOG,
    queryFn: () => unwrap<BacklogResult>(api.GET('/api/v1/backlog')),
    enabled,
  });
}

export type MarkVars = {
  task_id: number;
  step: number;
  op: MarkOp;
  reason?: string | null;
  to?: string | null;
};

export function useMark() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: MarkVars) =>
      unwrap<MarkResult>(api.POST('/api/v1/tasks/{task_id}/steps/{step_id}/mark', {
        params: { path: { task_id: v.task_id, step_id: v.step } },
        body: { op: v.op, reason: v.reason ?? null, to: v.to ?? null },
      })),
    onMutate: async (v) => {
      await Promise.all([qc.cancelQueries({ queryKey: FEED }), qc.cancelQueries({ queryKey: BACKLOG })]);
      const key = rowKey({ task_id: v.task_id, step: v.step });
      const feed = qc.getQueryData<FeedResult>(FEED);
      const backlog = qc.getQueryData<BacklogResult>(BACKLOG);
      if (feed) qc.setQueryData<FeedResult>(FEED, applyMark(feed, key, v.op));
      if (backlog) {
        const rows = withoutRow(backlog.backlog, key);
        qc.setQueryData<BacklogResult>(BACKLOG, { ...backlog, backlog: rows, count: rows.length });
      }
      return { feed, backlog };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.feed) qc.setQueryData(FEED, ctx.feed);
      if (ctx?.backlog) qc.setQueryData(BACKLOG, ctx.backlog);
    },
    onSuccess: (result) => {
      const feed = qc.getQueryData<FeedResult>(FEED);
      if (feed) qc.setQueryData<FeedResult>(FEED, applyServer(feed, result));
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: FEED });
      qc.invalidateQueries({ queryKey: BACKLOG });
    },
  });
}

export function useUndo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { task_id: number; step: number }) =>
      unwrap<MarkResult>(api.POST('/api/v1/tasks/{task_id}/steps/{step_id}/undo', {
        params: { path: { task_id: v.task_id, step_id: v.step } },
      })),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: FEED });
      qc.invalidateQueries({ queryKey: BACKLOG });
    },
  });
}
