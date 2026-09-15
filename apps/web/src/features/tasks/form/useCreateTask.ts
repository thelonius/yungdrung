// Отправка формы: `POST /api/v1/tasks`, затем — если есть отмеченные
// гипотезы базы знаний — `POST /api/v1/tasks/{id}/kb-confirm` (SLICE2_SPEC.md
// §5.4 п.8). Ссылки можно проставить только после создания: до этой точки
// `source_id` (название задачи) ещё не существует (`static/app.js`, тот же
// порядок).
import { useMutation } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { Hypothesis, TaskIn } from '@/api/client';
import { unwrapErrors } from '@/api/errors';

export type CreateVars = { task: TaskIn; mentions: Hypothesis[] };
export type CreateOutcome = { task: string; task_id: number; steps: number; statusRu: string; linked: number | null };

export function useCreateTask() {
  return useMutation({
    mutationFn: async ({ task, mentions }: CreateVars): Promise<CreateOutcome> => {
      const result = await unwrapErrors(api.POST('/api/v1/tasks', { body: task }));
      let linked: number | null = null;
      if (mentions.length > 0) {
        try {
          const confirm = await unwrapErrors(api.POST('/api/v1/tasks/{task_id}/kb-confirm', {
            params: { path: { task_id: result.task_id } },
            body: { mentions },
          }));
          linked = confirm.links.length;
        } catch {
          // Задача уже создана — сбой простановки ссылок не повод показывать
          // форму как упавшую (`static/app.js::submit`, тот же расчёт).
        }
      }
      return {
        task: result.task, task_id: result.task_id, steps: result.steps,
        statusRu: result.card.status_ru, linked,
      };
    },
  });
}
