"""Прикладной слой вложений: владелец (задача, шаг, шаблон) → строки таблицы
`attachments` и байты на диске (§3.5 спецификации среза 2). Владелец B3.

Хранение байтов и дедуп по sha256 остаются в `attachments.py` в корне; там же
единственный источник лимита размера (`MAX_BYTES`). Здесь только адресация
владельца, ошибки ядра и модели ответов.

Владелец адресуется названием, а не `tasks.id` (Р3): `("task", title)`,
`("step", f"{title}:{step_id}")`, `("template", каноническое имя)`. Это
наследие markdown-стора, и переход на id отложен (§8, В1): миграция с
перезаписью `source_id` нарушила бы обещание «данные миграция не переписывает
никогда». Снаружи же владелец задаётся `task_id`: отсюда `owner_key`, который
переводит id в имя и заодно проверяет, что задача и шаг существуют.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import attachments as files
import templates as tpl
from core.context import Context
from core.errors import NotFound, ValidationError
from core.models_attachments import (
    AttachmentDeleteResult, AttachmentInfo, AttachmentList,
)
from domain.ru_dates import as_date
from domain.steps import steps_of


@dataclass(frozen=True)
class TaskOwner:
    """Задача целиком (`step_id=None`) или один её шаг. Группа подшагов тоже
    может нести файл (PLAN.md 554): шаг проверяется на существование, не на
    то, лист он или нет."""

    task_id: int
    step_id: int | None = None


@dataclass(frozen=True)
class TemplateOwner:
    """Имя шаблона в любом регистре; в ключ владельца попадает каноническое
    имя из хранилища, иначе «отчёт» завёл бы файлам вторую полку рядом с
    «Отчёт» (`tpl.same_name`)."""

    name: str


Owner = TaskOwner | TemplateOwner

# Человеческий адрес байтов; тот же путь отдаёт `/api/v1/attachments/{id}/bytes`,
# но в новой вкладке и в `src` картинки человек видит этот (Р17).
URL = "/вложение/{id}"


def _task(ctx: Context, task_id: int) -> dict:
    task = ctx.store.task_by_id(task_id)
    if task is None:
        raise NotFound(f"нет задачи с id {task_id}")
    return task


def owner_key(ctx: Context, owner: Owner) -> tuple[str, str, dict | None]:
    """`(source_type, source_id, задача | None)`. Задача возвращается третьим
    элементом, чтобы `list_for` не читала её второй раз ради списка шагов."""
    if isinstance(owner, TemplateOwner):
        шаблон = tpl.JsonStore(ctx.vault).get(owner.name)
        if not шаблон:
            raise NotFound(f"нет шаблона «{owner.name}»")
        return "template", шаблон["name"], None
    task = _task(ctx, owner.task_id)
    title = task["path"].stem
    if owner.step_id is None:
        return "task", title, task
    if owner.step_id not in {s["id"] for s in steps_of(task)}:
        raise NotFound(f"нет шага {owner.step_id} в «{title}»")
    return "step", f"{title}:{owner.step_id}", task


def _info(row: dict, step_id: int | None) -> AttachmentInfo:
    return AttachmentInfo(id=row["id"], filename=row["filename"], mime=row["mime"],
                          bytes=row["bytes"], caption=row["caption"],
                          added=as_date(row["added"]), step_id=step_id,
                          url=URL.format(id=row["id"]))


def add(ctx: Context, owner: Owner, data: bytes, filename: str, caption: str | None,
        today: date) -> AttachmentInfo:
    """Прикрепить байты к владельцу. Порядок проверок: сначала владелец (без
    него нечего прикреплять — 404), затем имя (422), затем размер, который
    сторожит `attachments.save` (422 с полем `file`).

    `mime` — по имени файла, не по заголовку клиента: заголовок не доверенный,
    а решение «показывать инлайн или отдавать на скачивание» всё равно
    принимает `content_type_and_disposition` по своему белому списку.
    """
    source_type, source_id, _ = owner_key(ctx, owner)
    имя = (filename or "").strip()
    if not имя:
        raise ValidationError.single("filename", "Нужно имя файла")
    try:
        sha256, size = files.save(ctx.vault, data, имя)
    except files.AttachmentError as e:
        raise ValidationError.single(e.field, e.message) from e
    mime = files.guess_mime(имя)
    подпись = (caption or "").strip() or None
    attachment_id = ctx.store.add_attachment(
        source_type, source_id, sha256, имя, mime, size, подпись, today)
    step_id = owner.step_id if isinstance(owner, TaskOwner) else None
    return _info({"id": attachment_id, "filename": имя, "mime": mime, "bytes": size,
                  "caption": подпись, "added": today}, step_id)


def list_for(ctx: Context, owner: Owner) -> AttachmentList:
    """Файлы владельца. Для задачи без `step_id` — её собственные и файлы
    каждого шага, включая группы, одним списком с проставленным `step_id`
    (Р6): карточка грузит вложения один раз и раздаёт по шагам фильтром.

    Запрос на каждый шаг, а не один `LIKE 'title:%'`: шагов в задаче единицы,
    а префиксный поиск по строке спутал бы «Грант:1» с «Грант:10» без
    дополнительного разбора хвоста.
    """
    source_type, source_id, task = owner_key(ctx, owner)
    склад = ctx.store
    step_id = owner.step_id if isinstance(owner, TaskOwner) else None
    rows = [_info(r, step_id) for r in склад.list_attachments(source_type, source_id)]
    if source_type == "task" and task is not None:
        for step in steps_of(task):
            ключ = f"{source_id}:{step['id']}"
            rows.extend(_info(r, step["id"]) for r in склад.list_attachments("step", ключ))
    return AttachmentList(attachments=rows)


def remove(ctx: Context, attachment_id: int) -> AttachmentDeleteResult:
    """Удалить строку. Файл на диске остаётся: он адресован содержимым, и тот
    же sha256 может держать другая строка (`store.delete_attachment`)."""
    if ctx.store.get_attachment(attachment_id) is None:
        raise NotFound(f"нет вложения {attachment_id}")
    ctx.store.delete_attachment(attachment_id)
    return AttachmentDeleteResult(id=attachment_id)


def locate_bytes(ctx: Context, attachment_id: int) -> tuple[Path, str, str]:
    """`(путь на диске, Content-Type, Content-Disposition)` для отдачи байтов.
    Оба отказа — нет строки и файл потерян на диске — `NotFound`: для
    браузера это одно и то же «нет такого», а разница видна в тексте."""
    row = ctx.store.get_attachment(attachment_id)
    if row is None:
        raise NotFound(f"нет вложения {attachment_id}")
    путь = files.locate(ctx.vault, row["sha256"])
    if путь is None:
        raise NotFound(f"файл вложения {attachment_id} потерян на диске")
    ctype, disposition = files.content_type_and_disposition(row["mime"], row["filename"])
    return путь, ctype, disposition


def copy_template_to_task(ctx: Context, template_name: str, task_title: str,
                          today: date) -> int:
    """Перенести файлы шаблона на заведённую из него задачу. Возвращает,
    сколько перенесено.

    Копируются строки в `attachments`, а не байты: файл на диске адресуется
    своим sha256 (`attachments.save`), поэтому вторая ссылка на ту же картинку
    ничего не пишет на диск и ничего не весит. Без этого шага фото на шаблоне
    остаётся украшением карточки: человек делает задачу, а схема, ради которой
    файл прикрепляли, лежит там, куда он в этот момент не смотрит.

    Подпись и имя файла переносятся как есть, дата ставится сегодняшняя — это
    дата появления файла у задачи, а не у шаблона. Имя шаблона берётся как
    передано: зовущий (`core.templates`, `core.recur`) уже держит каноническое.
    """
    склад = ctx.store
    перенесено = 0
    for r in склад.list_attachments("template", template_name):
        склад.add_attachment("task", task_title, r["sha256"], r["filename"],
                             r["mime"], r["bytes"], r["caption"], today)
        перенесено += 1
    return перенесено
