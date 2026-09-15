// Серверное состояние вложений (SLICE2_SPEC.md §5.7): загрузка и список через
// `POST/GET .../attachments`, удаление через `DELETE /attachments/{id}`.
// Кэш TanStack Query ключуется по владельцу без `step_id` — задача отдаёт
// вложения себя и всех шагов одним списком (Р6), поэтому карточка и её
// редакторы шагов делят один запрос и один кэш вместо N.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { QueryKey } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { AttachmentInfo, AttachmentListResult } from '@/api/client';
import { ApiError, unwrapErrors } from '@/api/errors';
import type { components } from '@/api/schema';
import type { Owner } from './model';

type TaskAttachBody = components['schemas']['Body_task_attach_api_v1_tasks__task_id__attachments_post'];
type TemplateAttachBody = components['schemas']['Body_template_attach_api_v1_templates__name__attachments_post'];

export function attachmentsKey(owner: Owner): QueryKey {
  // Ключ по владельцу-сущности, не по строке `Owner` целиком: разные `step_id`
  // у одной задачи должны попадать в одну и ту же запись кэша.
  return owner.kind === 'task'
    ? (['attachments', 'task', owner.task_id] as const)
    : (['attachments', 'template', owner.name] as const);
}

async function fetchAttachments(owner: Owner): Promise<AttachmentListResult> {
  if (owner.kind === 'task') {
    return unwrapErrors(api.GET('/api/v1/tasks/{task_id}/attachments', {
      params: { path: { task_id: owner.task_id } },
    }));
  }
  return unwrapErrors(api.GET('/api/v1/templates/{name}/attachments', {
    params: { path: { name: owner.name } },
  }));
}

export function useAttachmentsQuery(owner: Owner, opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: attachmentsKey(owner),
    queryFn: () => fetchAttachments(owner),
    enabled: opts.enabled ?? true,
  });
}

/** Один файл на сервер, `multipart/form-data`. `bodySerializer` тождественный:
 * без него openapi-fetch сериализует `FormData` в JSON и сервер получит
 * пустое тело (SLICE2_SPEC.md §5.7). */
export async function uploadAttachment(
  owner: Owner,
  file: File,
  caption?: string | null,
): Promise<AttachmentInfo> {
  const fd = new FormData();
  fd.append('file', file, file.name);
  if (caption) fd.append('caption', caption);
  if (owner.kind === 'task') {
    if (owner.step_id != null) fd.append('step_id', String(owner.step_id));
    const result = await unwrapErrors(api.POST('/api/v1/tasks/{task_id}/attachments', {
      params: { path: { task_id: owner.task_id } },
      body: fd as unknown as TaskAttachBody,
      bodySerializer: (b) => b,
    }));
    return result.attachment;
  }
  const result = await unwrapErrors(api.POST('/api/v1/templates/{name}/attachments', {
    params: { path: { name: owner.name } },
    body: fd as unknown as TemplateAttachBody,
    bodySerializer: (b) => b,
  }));
  return result.attachment;
}

export async function deleteAttachmentRequest(id: number): Promise<void> {
  await unwrapErrors(api.DELETE('/api/v1/attachments/{id}', { params: { path: { id } } }));
}

/** Загрузка через кнопку/инпут в `AttachmentList`: пишет результат сразу в
 * кэш владельца, второго `GET` не требуется (§1.5 SLICE2_SPEC.md). */
export function useUploadAttachment(owner: Owner) {
  const qc = useQueryClient();
  const key = attachmentsKey(owner);
  return useMutation({
    mutationFn: ({ file, caption }: { file: File; caption?: string | null }) =>
      uploadAttachment(owner, file, caption),
    onSuccess: (attachment) => {
      const prev = qc.getQueryData<AttachmentListResult>(key);
      if (prev) qc.setQueryData<AttachmentListResult>(key, { attachments: [...prev.attachments, attachment] });
      else qc.invalidateQueries({ queryKey: key });
    },
  });
}

/** Удаление оптимистичное: строка уходит из кэша до ответа сервера, ошибка
 * возвращает снимок обратно (SLICE2_SPEC.md §5.7). */
export function useDeleteAttachment(owner: Owner) {
  const qc = useQueryClient();
  const key = attachmentsKey(owner);
  return useMutation({
    mutationFn: (id: number) => deleteAttachmentRequest(id),
    onMutate: async (id: number) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<AttachmentListResult>(key);
      if (prev) {
        qc.setQueryData<AttachmentListResult>(key, {
          attachments: prev.attachments.filter((a) => a.id !== id),
        });
      }
      return { prev };
    },
    onError: (_e, _id, ctx) => {
      if (ctx?.prev) qc.setQueryData(key, ctx.prev);
    },
  });
}

// --- очередь до первого сохранения (F1/F3, `PendingFiles`) ----------------
//
// Новая задача/шаблон ещё не существует на сервере, пока форма не отправлена,
// поэтому файлы копятся локально (`File[]`) и уходят одним проходом уже с
// настоящим `Owner`, когда id/имя появились. Последовательно, не `Promise.all`:
// так порядок ошибок в `failed` предсказуем и не зависит от планировщика сети.
export async function uploadPending(
  owner: Owner,
  files: File[],
): Promise<{ ok: File[]; failed: { file: File; error: string }[] }> {
  const ok: File[] = [];
  const failed: { file: File; error: string }[] = [];
  for (const file of files) {
    try {
      await uploadAttachment(owner, file);
      ok.push(file);
    } catch (e) {
      failed.push({ file, error: e instanceof ApiError ? e.message : 'не получилось' });
    }
  }
  return { ok, failed };
}
