// Серверное состояние карточки задачи: запрос по `task_id` и мутации записи.
// Отметка шага (`mark`/`undo`) — общий `useMark`/`useUndo` из `features/feed`
// (SLICE2_SPEC.md §5.5 п.6: «тот же useMark»), здесь — то, чего у ленты нет:
// сама карточка, правка, закрытие/отмена/удаление, переоткрытие шага,
// сохранение в шаблон. Мутации записи инвалидируют `['task', id]` и, как
// любая запись задачи, `FEED`/`BACKLOG` (риск 16 map-client.md, §5.1 таблица
// ключей кэша) — лента держит своё название/счётчики этой же задачи.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import type {
  AttachmentListResult, CancelIn, CloseResult, PlanIn, PlanResult, TaskCard, TaskDeleteResult, TaskEditIn,
  TaskSaveResult, TemplateCard,
} from '@/api/client';
import { ApiError, unwrapErrors } from '@/api/errors';
import { BACKLOG, FEED } from '@/features/feed/useFeed';

export const TASK = (id: number) => ['task', id] as const;

export function useTaskQuery(id: number | null) {
  return useQuery({
    queryKey: TASK(id ?? -1),
    queryFn: () => unwrapErrors<TaskCard>(api.GET('/api/v1/tasks/{task_id}', {
      params: { path: { task_id: id as number } },
    })),
    enabled: id !== null,
  });
}

/** Инвалидация после любой записи — карточка и лента вместе (риск 16). */
function useInvalidateAfterWrite(id: number) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: TASK(id) });
    qc.invalidateQueries({ queryKey: FEED });
    qc.invalidateQueries({ queryKey: BACKLOG });
  };
}

// Явный `TError = ApiError` на каждой мутации: `unwrapErrors` всегда бросает
// её, но дефолт `useMutation` — голый `Error`, и без аннотации `onError` на
// месте вызова не видит `.errors` (нужен форме для раскладки по полям).
export function useUpdateTask(id: number) {
  const invalidate = useInvalidateAfterWrite(id);
  return useMutation<TaskSaveResult, ApiError, TaskEditIn>({
    mutationFn: (data) => unwrapErrors<TaskSaveResult>(api.PUT('/api/v1/tasks/{task_id}', {
      params: { path: { task_id: id } },
      body: data,
    })),
    onSuccess: invalidate,
  });
}

export function useCancelTask(id: number) {
  const invalidate = useInvalidateAfterWrite(id);
  return useMutation<TaskCard, ApiError, CancelIn>({
    mutationFn: (data) => unwrapErrors<TaskCard>(api.POST('/api/v1/tasks/{task_id}/cancel', {
      params: { path: { task_id: id } },
      body: data,
    })),
    onSuccess: invalidate,
  });
}

export function useCloseTask(id: number) {
  const invalidate = useInvalidateAfterWrite(id);
  return useMutation<CloseResult, ApiError, void>({
    mutationFn: () => unwrapErrors<CloseResult>(api.POST('/api/v1/tasks/{task_id}/close', {
      params: { path: { task_id: id } },
    })),
    onSuccess: invalidate,
  });
}

export function useDeleteTask(id: number) {
  const qc = useQueryClient();
  return useMutation<TaskDeleteResult, ApiError, void>({
    mutationFn: () => unwrapErrors<TaskDeleteResult>(api.DELETE('/api/v1/tasks/{task_id}', {
      params: { path: { task_id: id } },
    })),
    onSuccess: () => {
      qc.removeQueries({ queryKey: TASK(id) });
      qc.invalidateQueries({ queryKey: FEED });
      qc.invalidateQueries({ queryKey: BACKLOG });
    },
  });
}

export function useReopenStep(id: number) {
  const invalidate = useInvalidateAfterWrite(id);
  return useMutation<TaskCard, ApiError, number>({
    mutationFn: (stepId) => unwrapErrors<TaskCard>(api.POST(
      '/api/v1/tasks/{task_id}/steps/{step_id}/reopen',
      { params: { path: { task_id: id, step_id: stepId } } },
    )),
    onSuccess: invalidate,
  });
}

/** «Сохранить как шаблон» — не трогает карточку, только заводит шаблон. */
export function useToTemplate(id: number) {
  return useMutation<TemplateCard, ApiError, string | undefined>({
    mutationFn: (name) => unwrapErrors<TemplateCard>(api.POST('/api/v1/templates/from-task', {
      body: { task_id: id, name: name ?? null },
    })),
  });
}

/** Живой предпросмотр плана (§5.5 п.10): опрашивается на каждую правку
 * черновика, ответ — только для подсветки полей, ничего не пишет. */
export function usePlan() {
  return useMutation({
    mutationFn: (data: PlanIn) => unwrapErrors<PlanResult>(api.POST('/api/v1/tasks/plan', { body: data })),
  });
}

/** Один запрос на всю задачу (Р6 SLICE2_SPEC.md) — файлы задачи и всех её
 * шагов; ключ кэша `['attachments', owner]` тот же, что возьмёт настоящий
 * `AttachmentList` (F4b), когда сам начнёт по нему грузить — раздача идёт
 * через `items`+`filter`, повторного похода в сеть на каждый шаг нет
 * (§5.1 интерфейс `AttachmentList`). */
export function useTaskAttachments(id: number | null) {
  return useQuery({
    queryKey: ['attachments', { kind: 'task', task_id: id ?? -1 }],
    queryFn: () => unwrapErrors<AttachmentListResult>(api.GET('/api/v1/tasks/{task_id}/attachments', {
      params: { path: { task_id: id as number } },
    })).then((r) => r.attachments),
    enabled: id !== null,
  });
}
