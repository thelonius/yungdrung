"""Прикладной слой шаблонов над расчётом и складом `templates.py` в корне
(§3.4 спецификации среза 2). Владелец B2.

Корневой `templates.py` остаётся чистым расчётом и складом (после шага 0 он
не зависит от `engine`), здесь — то, что нужно оболочкам сверх него: контекст
стора, ошибки ядра вместо `TemplateError`, `today` вызывающего вместо
системных часов, настройки выходных заказчика в предпросмотре и типизированные
ответы из `core.models_templates`. Задачи из шаблона создаёт `core.tasks`,
файлы переносит `core.attachments`, журнал повторений ведёт `core.recur`.
"""
from contextlib import contextmanager
from datetime import date, datetime

from pydantic import BaseModel

import templates as tpl
from core import attachments as core_attachments
from core import mark as core_mark
from core import tasks as core_tasks
from core import recur as core_recur
from core.context import Context
from core.dates import НЕ_ПОНЯЛ
from core.errors import NotFound, ValidationError
from core.models_tasks import FieldError
from core.models_templates import (
    FromTemplateResult, PreviewRow, RecurrenceView, RuleDate, RuleIn, RuleParseResult,
    RulePreviewResult, TemplateCard, TemplateDeleteResult, TemplateList, TemplatePreview,
    TemplateStep,
)
from domain import recurrence as rec
from domain.ru_dates import as_date, parse_date_input
from domain.steps import leaves_of, task_status

ANCHOR_REQUIRED = "Нужна дата, от которой считать первый цикл"
ANCHOR_UNPARSED = "Дату не понял, нужен формат 2026-08-18"


def store(ctx: Context) -> tpl.JsonStore:
    return tpl.JsonStore(ctx.vault)


@contextmanager
def _template_errors():
    """`TemplateError` склада и `ValidationError` ядра несут один и тот же
    список `{"field", "error"}`, но HTTP-слой переводит только исключения ядра.
    Переупаковка здесь, на границе, а не наследованием: корневой `templates.py`
    не должен зависеть от `core`, иначе снова кольцо."""
    try:
        yield
    except tpl.TemplateError as e:
        raise ValidationError(e.errors) from e


def _not_found(name) -> NotFound:
    return NotFound(f"нет шаблона «{name}»")


def load(ctx: Context, name) -> dict:
    with _template_errors():
        шаблон = store(ctx).get(name)
    if not шаблон:
        raise _not_found(name)
    return шаблон


def _payload(data) -> dict:
    """Pydantic-модель или сырой словарь (CLI-адаптер) → данные для склада.
    `exclude_unset`: незаданные поля правила не затирают дефолты
    `normalize_rule`, а незаданные `tags`/`body` склад и так считает пустыми."""
    if isinstance(data, BaseModel):
        return data.model_dump(exclude_unset=True)
    if not isinstance(data, dict):
        raise ValidationError.single(None, "Шаблон задаётся объектом с полями")
    return dict(data)


def _rule_payload(rule) -> dict:
    """Правило от формы: `None` в полях означает «не задано», а не «пусто» —
    `RuleParseResult.rule` уходит клиенту со всеми полями, и он возвращает
    их как есть. Иначе `interval: null` доехал бы до валидатора правила."""
    сырое = rule.model_dump(exclude_unset=True) if isinstance(rule, BaseModel) else dict(rule or {})
    return {k: v for k, v in сырое.items() if v is not None}


СПИСОЧНЫЕ_ПОЛЯ = ("byweekday", "bymonthday", "bysetpos", "bymonth")


def recurrence_view(template: dict) -> RecurrenceView | None:
    """Правило со склада плюс подпись. Подпись считает `recurrence.describe`,
    а не морда: оболочка правило словами не пересказывает.

    `domain.recurrence._normalized` хранит «поле не ограничивает цикл» как
    `None` (см. `_numbers`), а не пустым списком — для расчёта это то же
    самое, но `RecurrenceView` типизирует список без вариантов, чтобы клиенту
    не пришлось на каждое поле отдельно проверять `null` (`byweekday ?? []`
    в четырёх местах формы против одного здесь)."""
    правило = template.get("recurrence")
    if not правило:
        return None
    заполненное = {**правило, **{k: правило.get(k) or [] for k in СПИСОЧНЫЕ_ПОЛЯ}}
    return RecurrenceView(**заполненное, description=rec.describe(
        {k: v for k, v in правило.items() if k != "anchor"}))


def card(ctx: Context, template: dict) -> TemplateCard:
    """Карточка из уже прочитанного (нормализованного) шаблона."""
    шаги = template.get("steps") or []
    return TemplateCard(
        name=template["name"],
        tags=list(template.get("tags") or []),
        body=template.get("body") or "",
        steps=[TemplateStep(**s) for s in шаги],
        steps_count=len(шаги),
        attachments_count=len(ctx.store.list_attachments("template", template["name"])),
        recurrence=recurrence_view(template),
    )


def list_all(ctx: Context) -> TemplateList:
    with _template_errors():
        шаблоны = store(ctx).all()
    карточки = [card(ctx, t) for t in шаблоны]
    return TemplateList(templates=карточки, count=len(карточки))


def get(ctx: Context, name) -> TemplateCard:
    return card(ctx, load(ctx, name))


def _parse_start(start, today):
    """Дата старта: человеческий текст, ISO или уже готовая дата. Пусто —
    сегодня. Непонятный текст → ValueError, решает вызывающий."""
    if start is None or (isinstance(start, str) and not start.strip()):
        return today
    if isinstance(start, (date, datetime)):
        return as_date(start)
    return as_date(parse_date_input(str(start), today))


def preview(ctx: Context, template: dict | BaseModel, start, today) -> TemplatePreview:
    """Какие даты дадут шаги шаблона от даты старта. Раздел 5.6 ТЗ.

    Название здесь не проверяется намеренно: даты от него не зависят, а
    человек набирает шаги раньше, чем придумывает имя; повторение тоже не
    участвует — предпросмотр про шаги. Плохой ввод — это ответ `ok: false`,
    а не исключение: форма спрашивает на каждое нажатие.

    Выходные берутся из настроек заказчика (`ctx.work()`), а не из дефолтов:
    иначе при включённой работе по выходным строка метила бы субботу выходным
    и тут же назначала показ на ту же субботу.
    """
    try:
        старт = _parse_start(start, today)
    except (ValueError, TypeError):
        return TemplatePreview(ok=False, start=None, start_text=None,
                               errors=[FieldError(field="start", error=НЕ_ПОНЯЛ)])
    данные = _payload(template)
    пробный = {**данные,
               "name": str(данные.get("name") or "").strip() or "—", "recurrence": None}
    ошибки = [FieldError(**e) for e in tpl.validate_template(пробный, today=today)
              if not str(e.get("field") or "").startswith("name")]
    if ошибки:
        return TemplatePreview(ok=False, start=старт, start_text=tpl.human_moment(старт),
                               errors=ошибки)
    return TemplatePreview(
        ok=True, start=старт, start_text=tpl.human_moment(старт),
        steps=[PreviewRow(**r) for r in tpl.preview(пробный, старт, ctx.work())])


def save(ctx: Context, data, today, *, expect_name: str | None = None,
         create_only: bool = False) -> TemplateCard:
    """Создать шаблон или переписать существующий целиком (`PUT`).

    `expect_name` — имя из пути `PUT /templates/{name}`: шаблон обязан
    существовать, а имя в теле — совпадать с ним без регистра. Переименование
    через `PUT` отвергается (Р4): имя шаблона везде ключ — вложения, журнал
    повторений, `put` по имени; переименование потребовало бы id шаблона.

    `create_only` — для `POST /templates`: `templates.Store.save` сам по себе
    upsert (тест `test_templates.py` пиннит, что пересохранить шаблон под тем
    же именем не ошибка — иначе он не сохранялся бы «сам себе дубликатом»), а
    контракт (§1.3) требует, чтобы создание нового шаблона отвергало занятое
    имя 422 по полю `name`. Проверка здесь, до `Store.save`, а не переключением
    поведения самого склада — у `PUT` (правка существующего) конфликт по
    определению не может возникнуть, `expect_name` уже это гарантирует.

    Якорь повторения разбирается от `today` вызывающего, не от системных часов.
    """
    данные = _payload(data)
    if expect_name is not None:
        load(ctx, expect_name)
        if not tpl.same_name(данные.get("name"), expect_name):
            raise ValidationError.single(
                "name", "Переименование шаблона пока не поддерживается")
    if create_only:
        with _template_errors():
            занято = store(ctx).get(данные.get("name"))
        if занято:
            raise ValidationError.single("name", "Шаблон с таким названием уже есть")
    with _template_errors():
        шаблон = store(ctx).save(данные, today)
    return card(ctx, шаблон)


def delete(ctx: Context, name) -> TemplateDeleteResult:
    """Файлы вложений шаблона на диске не трогаем: они адресованы своим sha256,
    и удаление задачи строки в `attachments` тоже не чистит — тот же приём
    для единообразия."""
    with _template_errors():
        удалён = store(ctx).delete(name)
    if not удалён:
        raise _not_found(name)
    return TemplateDeleteResult(template=name)


def set_recurrence(ctx: Context, name, rule, today) -> TemplateCard:
    """Прикрепить (`rule` с `anchor` внутри) или снять (`None`) правило.

    Идёт через `Store.save` целиком, а не отдельным полем: у шаблона один путь
    записи, тот же, что у формы шагов, — иначе однажды разойдутся форматом.
    """
    шаблон = dict(load(ctx, name))
    шаблон["recurrence"] = None if rule is None else _rule_payload(rule)
    with _template_errors():
        обновлённый = store(ctx).save(шаблон, today)
    return card(ctx, обновлённый)


def instantiate(ctx: Context, name, start, title, today, now, work) -> FromTemplateResult:
    """Завести задачу из шаблона: развернуть даты, записать через `core.tasks`,
    перенести файлы шаблона, отметить цикл в журнале повторений (issue #2).

    Порядок тот же, что был в `cmd_from_template`: задача пишется первой, и
    только для записанной переносятся файлы и правится журнал.
    """
    шаблон = load(ctx, name)
    try:
        старт = _parse_start(start, today)
    except (ValueError, TypeError):
        raise ValidationError.single("start", НЕ_ПОНЯЛ) from None
    данные = tpl.expand(шаблон, старт, title=title)
    задача = core_tasks.create_task(
        ctx, данные, today, template_name=шаблон["name"])
    название = задача["path"].stem
    файлы = core_attachments.copy_template_to_task(
        ctx, шаблон["name"], название, today)
    core_recur.record_manual_cycle(ctx, шаблон, название, today)
    return FromTemplateResult(
        task_id=задача["path"].id, task=название, template=шаблон["name"],
        steps=len(задача["meta"]["steps"]), attachments=файлы,
        task_status=task_status(задача, today))


def from_task(ctx: Context, task_id: int, name, today) -> TemplateCard:
    """«Сохранить как шаблон»: даты контроля задачи обратно в сдвиги.

    В шаблон идут только листья: шаблон групп не умеет, а групповой узел без
    даты, попав в список плоско, получил бы сдвиг предыдущего листа и стал бы
    шагом-пустышкой в каждой заведённой задаче (риск 7 карты шаблонов).
    """
    задача = core_mark.load_task(ctx, task_id)
    meta = {**задача["meta"], "steps": leaves_of(задача)}
    with _template_errors():
        шаблон = store(ctx).save(tpl.template_from_task(meta, name=name), today)
    return card(ctx, шаблон)


def parse_rule(text) -> RuleParseResult:
    """Текст «каждый вторник» → правило. Требование R23. Разбирает
    `recurrence.parse_text` без модели; наружу уходит и правило, и его
    подпись — разобрав текст, надо показать, как система его поняла."""
    try:
        правило = rec.parse_text(text)
    except rec.RuleError as e:
        return RuleParseResult(ok=False, errors=e.errors)
    описание = rec.describe(правило)
    if правило.get("until") is not None:
        правило["until"] = правило["until"].isoformat()
    return RuleParseResult(ok=True, rule=RuleIn(**правило), description=описание)


def preview_rule(anchor, rule, today, work) -> RulePreviewResult:
    """Проверить и описать правило без сохранения — живая подпись в форме,
    та же роль, что у `parse-date` для одиночной даты."""
    if not anchor or not str(anchor).strip():
        return RulePreviewResult(ok=False, errors=[
            FieldError(field="recurrence.anchor", error=ANCHOR_REQUIRED)])
    try:
        якорь = as_date(parse_date_input(str(anchor), today))
    except (ValueError, TypeError):
        return RulePreviewResult(ok=False, errors=[
            FieldError(field="recurrence.anchor", error=ANCHOR_UNPARSED)])
    правило = _rule_payload(rule)
    ошибки = rec.validate_rule(правило, start=якорь)
    if ошибки:
        return RulePreviewResult(ok=False, errors=[
            FieldError(field=f"recurrence.{e['field']}" if e.get("field") else "recurrence",
                       error=e["error"]) for e in ошибки])
    return RulePreviewResult(
        ok=True, description=rec.describe(правило), anchor=якорь,
        preview=[RuleDate(date=s["date"], text=s["text"])
                 for s in rec.preview(правило, якорь, count=5, work=work)])
