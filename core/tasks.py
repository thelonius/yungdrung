"""Прикладной слой задач: создание, карточка, правка, отмена, закрытие,
переоткрытие шага, удаление, живой предпросмотр плана (§3.3 спецификации
среза 2). Владелец B1.

Образец — `core/mark.py`: ввод → `load_task` → мутация → `core.persist.save_task`
→ результат Pydantic-моделью из `core.models_tasks`. Единственный путь записи —
`core.persist.save_task`; этот модуль сам `store.save_task` не зовёт.
"""
from __future__ import annotations

import store
import worktime
from core import feed as core_feed
from core import mark as core_mark
from core.context import Context
from core.errors import Conflict, NotFound, ValidationError
from core.models_tasks import (
    CardStep, CloseResult, FieldError, FieldWarning, LogEntry, PlanIn, PlanResult,
    PlannedStep, TaskCard, TaskDeleteResult, TaskEditIn, TaskRef, TaskSaveResult,
)
from core.persist import save_task
from domain import steps_plan
from domain.ru_dates import as_date, extract_when, format_control, parse_date_input
from domain.steps import (
    DONE, OPEN, STATUS_RU, _closure, current_steps, is_group, stall_count, steps_of,
    task_status, task_summary,
)
from domain.steps_plan import ДАТУ_НЕ_ПОНЯЛ, strip_steps_block

# `undo` в `core.mark` отменяет последнее сегодняшнее действие над шагом ровно
# на этих же событиях — тот же список, чтобы «отменяемость» в карточке и в
# `core.mark.undo` не разошлись по двум спискам.
_UNDOABLE_EVENTS = core_mark.CLOSING_EVENTS + core_mark.MOVING_EVENTS


# --- создание --------------------------------------------------------------

def create_task(ctx: Context, data: dict, today, *, existing: list[str] | None = None,
                template_name: str | None = None, cycle_key: str | None = None) -> dict:
    """Общий путь записи новой задачи — из формы, из шаблона, из повторения.

    `existing` передают, когда список названий уже прочитан вызывающим
    (движок повторений заводит несколько задач подряд, и читать стор заново
    перед каждой незачем); иначе читаются только названия (`store.titles()`),
    без шагов и журнала — они здесь не нужны.
    """
    names = existing if existing is not None else [t for _, t in ctx.store.titles()]
    errors = steps_plan.validate_new_task(data, names, today)
    if errors:
        raise ValidationError(errors)

    meta = steps_plan.build_task(data, today)
    if template_name:
        meta["template_name"] = template_name
        meta["cycle_key"] = cycle_key
    task = {"path": None, "meta": meta, "body": (data.get("body") or "").strip() + "\n"}
    try:
        save_task(ctx, task, today)
    except store.DuplicateTitle:
        raise ValidationError.single(
            "title", "Задача с таким названием уже есть") from None
    return task


def quick_create(ctx: Context, text: str, today, now, work) -> TaskSaveResult:
    """Быстрый ввод одной строкой (Р8): один шаг, название шага = название
    задачи, дата — из `extract_when`, без распознанной — контроль сегодня.
    Разбор общий с окном контроля и подсказкой формы: бот и форма понимают
    ввод одинаково."""
    text = (text or "").strip()
    if not text:
        raise ValidationError.single("text", "Нужен текст")
    title, moment, _span = extract_when(text, today, now=now)
    title = title.strip() or text
    control = format_control(moment) if moment is not None else format_control(today)
    data = {"title": title, "steps": [{"title": title, "control_date": control}]}
    task = create_task(ctx, data, today)
    return save_result(ctx, task, today, now, work, created=True)


# --- карточка ----------------------------------------------------------------

def _control_text(value) -> str | None:
    return format_control(value) if value else None


def _step_actions(step, today) -> list[str]:
    status = step.get("status", OPEN)
    if status == OPEN:
        acts = ["done", "notdone", "defer", "fail", "skip"]
    elif status == DONE:
        acts = ["reopen"]
    else:
        acts = []
    log = step.get("log") or []
    if log:
        последнее = log[-1]
        if (последнее.get("event") in _UNDOABLE_EVENTS
                and as_date(последнее.get("date")) == today):
            acts = acts + ["undo"]
    return acts


def _card_step(task, step, children, closed_fn, active_ids, by_id, today, now, work) -> CardStep:
    kids = [_card_step(task, c, children, closed_fn, active_ids, by_id, today, now, work)
            for c in children.get(step["id"], [])]
    if is_group(step):
        return CardStep(
            id=step["id"], title=step.get("title"), status=step.get("status", OPEN),
            start_date=None, control_date=None, completed_date=None,
            note=step.get("note"), mode=step.get("mode"),
            closed=closed_fn(step), active=step["id"] in active_ids,
            stalled=stall_count(step), state=None, row=None, actions=[], steps=kids)

    closed = closed_fn(step)
    status = step.get("status", OPEN)
    row = None
    if status == OPEN:
        родитель = by_id.get(step.get("parent"))
        row = core_feed.feed_row(task, step, now, work,
                                 group=родитель.get("title") if родитель else None)
    return CardStep(
        id=step["id"], title=step.get("title"), status=status,
        start_date=_control_text(step.get("start_date")),
        control_date=_control_text(step.get("control_date")),
        completed_date=as_date(step.get("completed_date")),
        note=step.get("note"), mode=None,
        closed=closed, active=step["id"] in active_ids,
        stalled=stall_count(step),
        state=None if closed else worktime.due_state(step.get("control_date"), now, work),
        row=row, actions=_step_actions(step, today), steps=[])


def _history(task) -> list[LogEntry]:
    """Все события всех шагов, по убыванию даты; при равенстве дат — в
    порядке записи (шаги по порядку хранения, внутри шага — порядок журнала).
    Сортировка Python устойчивая, `reverse=True` порядок равных не трогает."""
    entries = []
    for step in steps_of(task):
        for e in step.get("log") or []:
            entries.append(LogEntry(
                step_id=step["id"], step_title=step.get("title"),
                date=as_date(e.get("date")), event=e.get("event"),
                reason=e.get("reason"), was=_control_text(e.get("was")),
                to=_control_text(e.get("to"))))
    entries.sort(key=lambda le: le.date, reverse=True)
    return entries


def _card_actions(status: str) -> list[str]:
    acts = []
    if status not in ("cancelled", "done"):
        acts.append("close")
    if status != "cancelled":
        acts.append("cancel")
    acts += ["delete", "to_template"]
    return acts


def card(ctx: Context, task, today, now, work) -> TaskCard:
    """Карточка задачи целиком, из уже прочитанной задачи. `state` шага
    считается тут же — карточка не знает про рабочее время (тот же принцип,
    что у ленты); блок шагов срезается из заметки, а не показывается как есть."""
    body, _restore = strip_steps_block(task["body"])
    meta = task["meta"]
    status = task_status(task, today)
    summary = task_summary(task, today)
    closed_fn, children = _closure(steps_of(task))
    active_ids = {s["id"] for s in current_steps(task)}
    by_id = {s["id"]: s for s in steps_of(task)}
    tree = [_card_step(task, s, children, closed_fn, active_ids, by_id, today, now, work)
            for s in children.get(None, [])]
    return TaskCard(
        task_id=task["path"].id, task=task["path"].stem,
        task_status=status, status_ru=STATUS_RU[status],
        body=body.strip(),
        created=as_date(meta.get("created")),
        start_date=format_control(meta.get("start_date")),
        tags=list(meta.get("tags") or []),
        cancelled=bool(meta.get("cancelled")),
        cancelled_reason=meta.get("cancelled_reason"),
        template_name=meta.get("template_name"),
        cycle_key=meta.get("cycle_key"),
        control_date=_control_text(summary.get("control_date")),
        current_step=summary.get("current_step"),
        progress=summary.get("progress"),
        stalled=summary.get("stalled", 0),
        steps=tree,
        history=_history(task),
        actions=_card_actions(status),
    )


def show(ctx: Context, task_id: int, today, now, work) -> TaskCard:
    task = core_mark.load_task(ctx, task_id)
    return card(ctx, task, today, now, work)


def resolve_title(ctx: Context, title: str) -> TaskRef:
    """Задача по точному названию — не по куску, как `find_task` у CLI: клиент
    здесь один раз переводит название в `task_id` (например, ссылка базы
    знаний), дальше адресуется id."""
    title = (title or "").strip()
    if not title:
        raise ValidationError.single("title", "Нужно название")
    task_id = ctx.store.find_task_id(title)
    if task_id is None:
        raise NotFound(f"нет задачи по «{title}»")
    return TaskRef(task_id=task_id, task=title)


# --- предпросмотр плана (Р9) --------------------------------------------------

def _parse_plan_start(raw, today, errors: list[dict]):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return parse_date_input(raw, today)
    except (ValueError, TypeError):
        errors.append({"field": "start_date", "error": ДАТУ_НЕ_ПОНЯЛ})
        return None


def plan(ctx: Context, data: PlanIn, today, now) -> PlanResult:
    """Живой предпросмотр формы: дефолт даты начала — сохранённый старт задачи
    в режиме правки (`task_id`), иначе сегодня, как при создании."""
    errors: list[dict] = []
    old = None
    if data.task_id is not None:
        task = core_mark.load_task(ctx, data.task_id)
        old = {s["id"]: s for s in steps_of(task)}
        дефолт = as_date(task["meta"].get("start_date")) or today
    else:
        дефолт = today
    start = _parse_plan_start(data.start_date, today, errors) or дефолт

    steps_data = [s.model_dump() for s in data.steps]
    nodes, plan_errors, warnings = steps_plan.plan(steps_data, start, today, old=old)
    all_errors = errors + plan_errors
    planned = [PlannedStep.model_validate(n) for n in steps_plan.to_planned(nodes)]
    return PlanResult(
        ok=not all_errors,
        errors=[FieldError(**e) for e in all_errors],
        warnings=[FieldWarning(**w) for w in warnings],
        steps=planned)


# --- правка, отмена, закрытие, переоткрытие, удаление ------------------------

def save_result(ctx: Context, task, today, now, work, *, created: bool,
                 renamed_from: str | None = None,
                 warnings: list[dict] | None = None) -> TaskSaveResult:
    return TaskSaveResult(
        task_id=task["path"].id, task=task["path"].stem,
        task_status=task_status(task, today), steps=len(steps_of(task)),
        created=created, renamed_from=renamed_from,
        warnings=[FieldWarning(**w) for w in (warnings or [])],
        card=card(ctx, task, today, now, work))


def _edit_dict(data: TaskEditIn) -> dict:
    """`TaskEditIn` → сырой dict для `domain.steps_plan`. `tags`/`body` кладутся,
    только если их прислали (`None` — «не трогать», как ждёт `apply_task_edit`);
    Pydantic же всегда даёт оба ключа, поэтому здесь их приходится вырезать
    обратно, когда значение — дефолтный `None`."""
    d = data.model_dump(exclude={"force"})
    if d.get("tags") is None:
        d.pop("tags", None)
    if d.get("body") is None:
        d.pop("body", None)
    return d


def update_task(ctx: Context, task_id: int, data: TaskEditIn, today, now, work) -> TaskSaveResult:
    task = core_mark.load_task(ctx, task_id)
    прежнее_имя = task["path"].stem
    d = _edit_dict(data)
    names = [t for _, t in ctx.store.titles()]

    errors = steps_plan.validate_task_edit(task, d, names, today)
    if errors:
        raise ValidationError(errors)

    пропали = steps_plan.missing_step_ids(task, d)
    if пропали and not data.force:
        raise ValidationError([
            {"field": "steps",
             "error": f"Шаг {sid} пропал из данных — сначала «снять», не убирать так"}
            for sid in пропали])

    warnings = steps_plan.soft_warnings(d, today)
    steps_plan.apply_task_edit(task, d, today)
    try:
        save_task(ctx, task, today)
    except store.DuplicateTitle:
        raise ValidationError.single(
            "title", "Задача с таким названием уже есть") from None

    новое_имя = task["path"].stem
    renamed_from = None
    if новое_имя != прежнее_имя:
        # Строку индекса и владение вложениями/базой знаний адресует название
        # (Р3): переименованная задача иначе оставила бы их за старым словом.
        ctx.store.search_forget("task", прежнее_имя)
        ctx.store.rename_owner(прежнее_имя, новое_имя)
        renamed_from = прежнее_имя

    return save_result(ctx, task, today, now, work, created=False,
                        renamed_from=renamed_from, warnings=warnings)


def cancel(ctx: Context, task_id: int, reason: str | None, today, now, work) -> TaskCard:
    task = core_mark.load_task(ctx, task_id)
    if task["meta"].get("cancelled"):
        raise Conflict(f"задача «{task['path'].stem}» уже отменена")
    task["meta"]["cancelled"] = True
    task["meta"]["cancelled_reason"] = (reason or "").strip() or None
    save_task(ctx, task, today)
    return card(ctx, task, today, now, work)


def close(ctx: Context, task_id: int, today, now, work) -> CloseResult:
    """Закрыть все открытые шаги разом. Идемпотентно — уже закрытая задача
    просто отдаёт `closed_steps=0`, той же логикой, что `delete_attachment`."""
    task = core_mark.load_task(ctx, task_id)
    if task["meta"].get("cancelled"):
        raise Conflict(f"задача «{task['path'].stem}» отменена, а не открыта")
    закрыто = 0
    while True:
        активные = current_steps(task)
        if not активные:
            break
        for s in активные:
            s["status"] = DONE
            s["completed_date"] = today
            core_mark.log_event(s, "done", today, reason=None)
            закрыто += 1
    save_task(ctx, task, today)
    return CloseResult(task_id=task["path"].id, closed_steps=закрыто,
                       card=card(ctx, task, today, now, work))


def reopen(ctx: Context, task_id: int, step_id, today, now, work) -> TaskCard:
    """Отменить закрытие конкретного шага — раздел 6.3.5 ТЗ. `find_step`
    (`core.mark`) сам отказывает `Unmarkable` на группе и `NotFound` на
    неизвестном id."""
    task = core_mark.load_task(ctx, task_id)
    step = core_mark.find_step(task, step_id)
    if step.get("status") != DONE:
        raise Conflict(f"шаг {step_id} не был сделан (сейчас: {step.get('status')})")
    step["status"] = OPEN
    step["completed_date"] = None
    core_mark.log_event(step, "reopened", today)
    save_task(ctx, task, today)
    return card(ctx, task, today, now, work)


def delete(ctx: Context, task_id: int) -> TaskDeleteResult:
    """Удалить насовсем. Подтверждение — дело оболочки (Р15), не ядра."""
    task = core_mark.load_task(ctx, task_id)
    title = task["path"].stem
    склад = ctx.store
    склад.delete_task(task_id)
    склад.search_forget("task", title)
    склад.forget_owner(title)
    return TaskDeleteResult(task_id=task_id, task=title)
