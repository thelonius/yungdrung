"""Шаблоны и правила повторения (§1.3 спецификации среза 2). Владелец B2.

Исключения ядра (`NotFound`, `ValidationError`) не ловятся здесь — их
переводит в HTTP общий обработчик `api/errors.py`, подключённый в `api/app.py`.
Маршруты — тонкие адаптеры над `core.templates`: разбор запроса и вызов, без
собственной логики (то же правило, что у `api/v1/router.py`).
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import ctx, moment
from api.errors import bracket_path
from core import templates as core_templates
from core.context import Context
from core.models_templates import (
    FromTaskIn,
    FromTemplateResult,
    InstantiateIn,
    RuleIn,
    RuleParseResult,
    RulePreviewIn,
    RulePreviewResult,
    TemplateCard,
    TemplateDeleteResult,
    TemplateIn,
    TemplateList,
    TemplatePreview,
    TemplatePreviewIn,
)

router = APIRouter(prefix="/api/v1", tags=["templates"])


class TextIn(BaseModel):
    """Как `TextIn` в `api/v1/router.py` — свой экземпляр, а не импорт оттуда:
    модуль шаблонов не должен тянуть за собой маршруты задач ради одного поля."""

    text: str | None = None


def _bracketed(result):
    """Скобочный путь поля (Р1) для живых проверок: они отвечают 200 и не
    проходят через исключение — общий обработчик `api/errors.py` их не видит,
    перевод делаем здесь, перед самой отдачей."""
    result.errors = [type(e)(field=bracket_path(e.field), error=e.error)
                     for e in result.errors]
    return result


@router.get("/templates", response_model=TemplateList)
def list_templates(c: Context = Depends(ctx)):
    return core_templates.list_all(c)


@router.get("/templates/{name}", response_model=TemplateCard)
def get_template(name: str, c: Context = Depends(ctx)):
    return core_templates.get(c, name)


@router.get("/templates/{name}/preview", response_model=TemplatePreview)
def preview_saved(name: str, start: str | None = None,
                  c: Context = Depends(ctx), now: datetime = Depends(moment)):
    """Предпросмотр уже сохранённого шаблона на дату `start` (по умолчанию
    сегодня). Шаблона нет — 404; плохая дата — 200 `ok: false` (Р9: живая
    проверка опрашивается на каждое нажатие, а не рвёт форму ошибкой запроса).
    """
    шаблон = core_templates.load(c, name)
    return _bracketed(core_templates.preview(c, шаблон, start, now.date()))


@router.post("/templates/preview", response_model=TemplatePreview)
def preview_draft(body: TemplatePreviewIn, c: Context = Depends(ctx),
                  now: datetime = Depends(moment)):
    """Предпросмотр ещё не сохранённого шаблона — форма шлёт черновик целиком,
    имя не проверяется (человек набирает шаги раньше, чем придумывает имя)."""
    return _bracketed(core_templates.preview(c, body.template, body.start, now.date()))


@router.post("/templates", response_model=TemplateCard)
def create_template(body: TemplateIn, c: Context = Depends(ctx), now: datetime = Depends(moment)):
    return core_templates.save(c, body, now.date(), create_only=True)


@router.put("/templates/{name}", response_model=TemplateCard)
def replace_template(name: str, body: TemplateIn, c: Context = Depends(ctx),
                     now: datetime = Depends(moment)):
    """Переписать шаблон целиком. Переименование через `PUT` отвергается
    (Р4): `name` в теле обязан совпасть с `name` в пути. `recurrence` из тела
    заменяет сохранённое — клиент шлёт то, что получил в `GET`, снятое
    правило доезжает как `null`."""
    return core_templates.save(c, body, now.date(), expect_name=name)


@router.delete("/templates/{name}", response_model=TemplateDeleteResult)
def delete_template(name: str, c: Context = Depends(ctx)):
    return core_templates.delete(c, name)


@router.post("/templates/{name}/instantiate", response_model=FromTemplateResult)
def instantiate_template(name: str, body: InstantiateIn, c: Context = Depends(ctx),
                         now: datetime = Depends(moment)):
    return core_templates.instantiate(
        c, name, body.start, body.title, now.date(), now, c.work())


@router.post("/templates/from-task", response_model=TemplateCard)
def template_from_task(body: FromTaskIn, c: Context = Depends(ctx),
                       now: datetime = Depends(moment)):
    return core_templates.from_task(c, body.task_id, body.name, now.date())


@router.put("/templates/{name}/recurrence", response_model=TemplateCard)
def set_recurrence(name: str, body: RuleIn, c: Context = Depends(ctx),
                   now: datetime = Depends(moment)):
    return core_templates.set_recurrence(c, name, body, now.date())


@router.delete("/templates/{name}/recurrence", response_model=TemplateCard)
def clear_recurrence(name: str, c: Context = Depends(ctx), now: datetime = Depends(moment)):
    return core_templates.set_recurrence(c, name, None, now.date())


@router.post("/recurrence/parse", response_model=RuleParseResult)
def parse_recurrence(body: TextIn):
    return _bracketed(core_templates.parse_rule(body.text))


@router.post("/recurrence/preview", response_model=RulePreviewResult)
def preview_recurrence(body: RulePreviewIn, c: Context = Depends(ctx),
                       now: datetime = Depends(moment)):
    return _bracketed(core_templates.preview_rule(body.anchor, body.rule, now.date(), c.work()))
