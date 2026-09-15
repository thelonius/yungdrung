"""Вложения задач, шагов и шаблонов (§1.4 спецификации среза 2). Владелец B3.

Файл приходит `multipart/form-data` (`UploadFile` + `Form`), а не base64 в
JSON, как в легаси: браузер шлёт `FormData` без перекодирования, а тело не
раздувается на треть. `python-multipart` для этого в `requirements.txt`.

Второй роутер `public` без префикса и вне схемы отдаёт байты по
человеческому пути `/вложение/{id}` (Р17): его видно в новой вкладке и в
`src` картинки, и пользовательские маршруты по-русски не меняются. Та же
функция висит и на `/api/v1/attachments/{id}/bytes`, чтобы у клиента был
типизированный адрес в схеме.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse

from api.deps import ctx, moment
from core import attachments as core_att
from core.attachments import TaskOwner, TemplateOwner
from core.context import Context
from core.models_attachments import AttachmentDeleteResult, AttachmentList, AttachResult

router = APIRouter(prefix="/api/v1", tags=["attachments"])
public = APIRouter(include_in_schema=False)


async def _add(c: Context, owner, file: UploadFile, caption, now: datetime) -> AttachResult:
    # `mime` считает ядро по имени файла, `file.content_type` не читается:
    # заголовок клиента не доверенный, и решение «инлайн или скачивание» всё
    # равно принимается по белому списку в `attachments.py`. Имя тоже от
    # клиента: для вставки из буфера он сам подставляет «вставка-….png».
    data = await file.read()
    info = core_att.add(c, owner, data, file.filename or "", caption, now.date())
    return AttachResult(attachment=info)


@router.get("/tasks/{task_id}/attachments", response_model=AttachmentList)
def task_attachments(task_id: int, c: Context = Depends(ctx)):
    """Файлы задачи и всех её шагов одним списком, у строк шагов — `step_id`."""
    return core_att.list_for(c, TaskOwner(task_id))


@router.post("/tasks/{task_id}/attachments", response_model=AttachResult)
async def task_attach(task_id: int, file: UploadFile = File(...),
                      step_id: int | None = Form(None), caption: str | None = Form(None),
                      c: Context = Depends(ctx), now: datetime = Depends(moment)):
    return await _add(c, TaskOwner(task_id, step_id), file, caption, now)


@router.get("/templates/{name}/attachments", response_model=AttachmentList)
def template_attachments(name: str, c: Context = Depends(ctx)):
    return core_att.list_for(c, TemplateOwner(name))


@router.post("/templates/{name}/attachments", response_model=AttachResult)
async def template_attach(name: str, file: UploadFile = File(...),
                          caption: str | None = Form(None),
                          c: Context = Depends(ctx), now: datetime = Depends(moment)):
    return await _add(c, TemplateOwner(name), file, caption, now)


@router.delete("/attachments/{id}", response_model=AttachmentDeleteResult)
def attachment_delete(id: int, c: Context = Depends(ctx)):
    return core_att.remove(c, id)


def attachment_bytes(id: int, c: Context = Depends(ctx)):
    """Байты вложения — не JSON. `FileResponse`, а не `read_bytes()` в
    память: файл до 15 МБ уходит потоком. `nosniff` запрещает браузеру
    переугадывать тип: для всего вне белого списка это `octet-stream` на
    скачивание, и переугадывание вернуло бы html со скриптом шанс исполниться."""
    путь, ctype, disposition = core_att.locate_bytes(c, id)
    return FileResponse(путь, media_type=ctype,
                        headers={"Content-Disposition": disposition,
                                 "X-Content-Type-Options": "nosniff"})


router.add_api_route("/attachments/{id}/bytes", attachment_bytes, methods=["GET"],
                     response_class=FileResponse)
public.add_api_route("/вложение/{id}", attachment_bytes, methods=["GET"])
