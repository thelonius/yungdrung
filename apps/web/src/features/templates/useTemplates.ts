// Серверное состояние шаблонов через TanStack Query — тот же приём, что
// `features/feed/useFeed.ts`: запросы читаемые, мутации инвалидируют то, что
// могло устареть (SLICE2_SPEC.md §5.1, «Мутации и ключи кэша»).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { unwrapErrors } from '@/api/errors';
import { api } from '@/api/client';
import type {
  FromTemplateResult, InstantiateIn, RuleIn, TemplateCard, TemplateDeleteResult, TemplateIn, TemplateList,
} from '@/api/client';
import { BACKLOG, FEED } from '@/features/feed/useFeed';

export const TEMPLATES = ['templates'] as const;

export function useTemplatesQuery() {
  return useQuery({
    queryKey: TEMPLATES,
    queryFn: () => unwrapErrors<TemplateList>(api.GET('/api/v1/templates')),
  });
}

function invalidateTemplates(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: TEMPLATES });
}

/** Запись ответа мутации (`TemplateCard`) сразу в кэш списка — второй `GET
 * /api/v1/templates` не нужен (§1.5 SLICE2_SPEC.md: «`TemplateCard` после
 * любой записи шаблона»; находка ревью среза 2 — раньше здесь был
 * безусловный `invalidateTemplates`). Список не читан (`prev` нет) —
 * ничего не пишем, обычный запрос при следующем монтировании возьмёт
 * свежие данные сам. */
function upsertTemplate(qc: ReturnType<typeof useQueryClient>, card: TemplateCard) {
  qc.setQueryData<TemplateList>(TEMPLATES, (prev) => {
    if (!prev) return prev;
    const idx = prev.templates.findIndex((t) => t.name === card.name);
    if (idx === -1) return { count: prev.count + 1, templates: [...prev.templates, card] };
    const templates = prev.templates.slice();
    templates[idx] = card;
    return { ...prev, templates };
  });
}

/** Создание (`name` в теле, ещё не существует) или правка под тем же именем
 * (`expectName` — прежнее имя из пути `PUT /templates/{name}`, Р4: переименование
 * через эту ручку сервер отвергает 422, форма и не предлагает его редактировать). */
export function useSaveTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { data: TemplateIn; expectName?: string }) =>
      unwrapErrors<TemplateCard>(
        v.expectName
          ? api.PUT('/api/v1/templates/{name}', { params: { path: { name: v.expectName } }, body: v.data })
          : api.POST('/api/v1/templates', { body: v.data }),
      ),
    onSuccess: (card) => upsertTemplate(qc, card),
  });
}

export function useDeleteTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      unwrapErrors<TemplateDeleteResult>(api.DELETE('/api/v1/templates/{name}', { params: { path: { name } } })),
    onSuccess: () => invalidateTemplates(qc),
  });
}

/** Заводит задачу из шаблона — новая задача может попасть в сегодняшнюю
 * ленту, поэтому `FEED`/`BACKLOG` тоже инвалидируются (§5.1, риск 16
 * map-client: «мутация задачи не трогает кэш ленты»). */
export function useInstantiate(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: InstantiateIn) =>
      unwrapErrors<FromTemplateResult>(
        api.POST('/api/v1/templates/{name}/instantiate', { params: { path: { name } }, body: data }),
      ),
    onSuccess: () => {
      invalidateTemplates(qc);
      qc.invalidateQueries({ queryKey: FEED });
      qc.invalidateQueries({ queryKey: BACKLOG });
    },
  });
}

export function useSetRecurrence(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rule: RuleIn) =>
      unwrapErrors<TemplateCard>(
        api.PUT('/api/v1/templates/{name}/recurrence', { params: { path: { name } }, body: rule }),
      ),
    onSuccess: (card) => upsertTemplate(qc, card),
  });
}

export function useClearRecurrence(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrapErrors<TemplateCard>(
        api.DELETE('/api/v1/templates/{name}/recurrence', { params: { path: { name } } }),
      ),
    onSuccess: (card) => upsertTemplate(qc, card),
  });
}
