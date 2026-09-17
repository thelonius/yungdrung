"""Модели ответов для вложений (§2.3 спецификации среза 2). Владелец B3.

Запросов здесь нет: файл приходит `multipart/form-data` (`UploadFile` +
`Form`), а не JSON, и Pydantic-модели тела у него не бывает. Общие
`FieldError`/`OkResult` живут в `core/models_tasks.py`.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class AttachmentInfo(BaseModel):
    id: int
    filename: str
    mime: str
    bytes: int
    caption: str | None
    added: date
    # У файлов шагов задачи — номер шага (`steps.step_id`, не PK); у файлов
    # самой задачи и у файлов шаблона — None. Так список задачи отдаётся одним
    # запросом, а карточка раздаёт строки по шагам фильтром (Р6).
    step_id: int | None = None
    # Готовая ссылка на байты. Ядро отдаёт её само, чтобы клиент не собирал
    # пути и не знал, что человеческий адрес — `/вложение/{id}` (Р17).
    url: str


class AttachmentList(BaseModel):
    attachments: list[AttachmentInfo]


class AttachResult(BaseModel):
    ok: bool = True
    attachment: AttachmentInfo


class AttachmentDeleteResult(BaseModel):
    ok: bool = True
    id: int
