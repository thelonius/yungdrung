"""Отметки шага: сделан, не сделан, перенос, провал, снятие — и отмена промаха.

Жизнь шага описана в `CONTRACT.md`; здесь она исполняется. Каждая операция
проверяет ввод, читает одну задачу по `task_id`, меняет шаг, пишет через
`core.persist.save_task` и возвращает `MarkResult` с новым состоянием.
"""
from datetime import timedelta

import store
from core import feed as core_feed
from core.context import Context
from core.errors import Conflict, NotFound, Unmarkable, ValidationError
from core.models import MarkResult
from core.persist import save_task
from domain.ru_dates import as_date, format_control
from domain.steps import (
    DONE, FAILED, OPEN, SKIPPED, current_steps, is_closed, is_group, stall_count,
    steps_of, task_status,
)

OPS = ("done", "notdone", "defer", "fail", "skip")

# Причина обязательна только у провала: «не будет сделано» без объяснения
# оставляет в истории дыру. У «не сделано» она необязательна (R15 ТЗ называет
# комментарий к «не сделано» необязательным), и это то, что делает возможной
# отметку одним нажатием. Если причина указана — она обязана быть из
# справочника (раздел 5.4), у трёх операций, которые её вообще пишут.
REASON_REQUIRED = ("fail",)
REASON_CHECKED = ("notdone", "defer", "fail")

# События журнала, которые `undo` умеет откатывать. Имена событий не совпадают
# с именами операций: `fail` пишет "failed", `skip` — "skipped". Делятся на два
# рода по тому, что именно они изменили в шаге.
CLOSING_EVENTS = ("done", "skipped", "failed")
MOVING_EVENTS = ("not_done", "defer", "mass_defer")


def load_task(ctx: Context, task_id: int) -> dict:
    task = ctx.store.task_by_id(task_id)
    if task is None:
        raise NotFound(f"нет задачи с id {task_id}")
    return task


def find_step(task, step_id) -> dict:
    """Найти шаг для отметки. Группа отметки не принимает — done/defer и
    остальные работают по её подшагам, а закрытие группы вычисляется."""
    for step in steps_of(task):
        if str(step.get("id")) == str(step_id):
            if is_group(step):
                raise Unmarkable(f"шаг {step_id} — группа, отмечаются её подшаги")
            return step
    raise NotFound(f"нет шага {step_id} в «{task['path'].stem}»")


def step_snapshot(step) -> dict:
    """Значения шага в момент чтения — сторож для `save(expected_step=...)`
    против гонки при одновременной отметке (issue #11). Все поля, которые
    операции над одним шагом вообще меняют: если хоть одно успело измениться
    в базе между чтением и записью — чужая запись уже выиграла, и переписывать
    её нельзя."""
    return {"status": step.get("status", OPEN),
            "control_date": step.get("control_date"),
            "completed_date": step.get("completed_date"),
            "note": step.get("note")}


def log_event(step, event, today, **fields) -> None:
    step.setdefault("log", [])
    entry = {"date": today, "event": event}
    entry.update({k: v for k, v in fields.items() if v is not None})
    step["log"].append(entry)


def reason_error(ctx: Context, reason):
    """Причина обязана быть словом из справочника — раздел 5.4 ТЗ прямо
    говорит «из справочника», не «любой текст». Сравнение без регистра, тем же
    приёмом, что у тегов и шаблонов. Пустая причина сюда не относится:
    обязательна она или нет, решает операция (`REASON_REQUIRED`)."""
    if not reason:
        return None
    if reason.strip().lower() not in {r.lower() for r in ctx.reasons()}:
        return {"field": "reason", "error": f"Причина «{reason}» не из справочника"}
    return None


def _save(ctx, task, today, expected_step, step_id) -> None:
    try:
        save_task(ctx, task, today, expected_step=expected_step)
    except store.StepConflict as e:
        raise Conflict(f"шаг {step_id} уже {e.actual_status}", e.actual_status) from e


def _assign_dates(task, today) -> tuple[list, list]:
    """Шаг без даты никогда не всплывёт в ленте — открывшимся листьям ставим
    сегодня, человек увидит их и при необходимости перенесёт. Открыться могла
    параллельная группа, то есть листьев несколько — дату получает каждый."""
    активные = current_steps(task)
    assigned = [s["id"] for s in активные if not s.get("control_date")]
    for s in активные:
        if not s.get("control_date"):
            s["control_date"] = today
    return активные, assigned


def defer_step(ctx: Context, task, step, today, to, reason, *, event="defer",
               expected_step=None) -> None:
    """Общая механика переноса: событие в журнал, новая дата, запись. Массовый
    перенос из разбора завала (R20) передаёт `event="mass_defer"`, чтобы запись
    в журнале была отличима от обычного переноса. `store.StepConflict`
    пробрасывается как есть: у одиночного и массового переноса он оформляется
    по-разному."""
    log_event(step, event, today, reason=reason,
              was=as_date(step.get("control_date")), to=to)
    step["control_date"] = to
    save_task(ctx, task, today, expected_step=expected_step)


def _result(ctx, op, task, step, today, now, work, **fields) -> MarkResult:
    counts, row = core_feed.state_after(ctx, now, work, task["path"].id, step["id"])
    return MarkResult(op=op, task_id=task["path"].id, task=task["path"].stem,
                      step=step["id"], status=step.get("status", OPEN),
                      task_status=task_status(task, today), row=row, counts=counts,
                      **fields)


def mark(ctx: Context, op: str, task_id: int, step_id, *, reason=None, to=None,
         today, now, work) -> MarkResult:
    """Одна из пяти отметок шага. `to` — уже разобранная дата или момент
    (`date | datetime`): человеческий ввод («+3», «завтра в 9») разбирает
    `domain.ru_dates.parse_date_input` у вызывающего, а не эта функция, — тогда
    форма и бот понимают одно и то же."""
    if op not in OPS:
        raise ValidationError.single("op", f"неизвестное действие: {op}")
    reason = (reason or "").strip() or None
    if op in REASON_REQUIRED and not reason:
        raise ValidationError.single("reason", "причина обязательна")
    if op in REASON_CHECKED:
        ошибка = reason_error(ctx, reason)
        if ошибка:
            raise ValidationError([ошибка])
    if op == "defer" and to is None:
        raise ValidationError.single("to", "нужна новая дата")

    task = load_task(ctx, task_id)
    step = find_step(task, step_id)
    expected = (step["id"], step_snapshot(step))

    if op == "done":
        if step.get("status") != OPEN:
            raise Conflict(f"шаг {step_id} уже {step.get('status')}", step.get("status"))
        step["status"] = DONE
        step["completed_date"] = today
        log_event(step, "done", today, reason=reason)
        активные, assigned = _assign_dates(task, today)
        _save(ctx, task, today, expected, step_id)
        return _result(ctx, op, task, step, today, now, work,
                       next_step_id=активные[0].get("id") if активные else None,
                       next_step_title=активные[0].get("title") if активные else None,
                       dates_assigned=assigned)

    if op == "notdone":
        # Шаг остаётся открытым: причина обычно внешняя, решать всё равно надо.
        if step.get("status") != OPEN:
            raise Conflict(f"шаг {step_id} уже {step.get('status')}", step.get("status"))
        new_date = to if to is not None else today + timedelta(days=1)
        log_event(step, "not_done", today, reason=reason,
                  was=as_date(step.get("control_date")), to=new_date)
        step["control_date"] = new_date
        _save(ctx, task, today, expected, step_id)
        count = stall_count(step)
        return _result(ctx, op, task, step, today, now, work,
                       next_check=format_control(new_date), stalled=count,
                       hint="шаг буксует, нужен другой ход" if count >= 3 else None)

    if op == "defer":
        if step.get("status") != OPEN:
            raise Conflict(f"шаг {step_id} уже {step.get('status')}", step.get("status"))
        try:
            defer_step(ctx, task, step, today, to, reason, expected_step=expected)
        except store.StepConflict as e:
            raise Conflict(f"шаг {step_id} уже {e.actual_status}", e.actual_status) from e
        return _result(ctx, op, task, step, today, now, work,
                       next_check=format_control(to), stalled=stall_count(step))

    if op == "fail":
        # «Не будет сделано» — шаг закрывается проваленным, задача идёт дальше.
        # Четвёртый исход из раздела 6.4 ТЗ, намеренно менее заметный в
        # интерфейсе: он не должен становиться лёгким путём отмахнуться.
        # Отличается от «снят» тем, что снятый шаг перестал быть нужен, а
        # проваленный был нужен и не случился — в истории это разные вещи.
        if is_closed(step):
            raise Conflict(f"шаг {step_id} уже {step.get('status')}", step.get("status"))
        step["status"] = FAILED
        log_event(step, "failed", today, reason=reason)
        активные, assigned = _assign_dates(task, today)
        _save(ctx, task, today, expected, step_id)
        return _result(ctx, op, task, step, today, now, work,
                       next_step_id=активные[0].get("id") if активные else None,
                       next_step_title=активные[0].get("title") if активные else None,
                       dates_assigned=assigned)

    # skip: шаг снят — задача пошла другим путём. Закрытый шаг повторно не
    # трогаем, иначе снятие уже сделанного оставило бы completed_date и событие
    # «сделан» рядом со статусом «снят» — запись, противоречащая сама себе.
    if is_closed(step):
        raise Conflict(f"шаг {step_id} уже {step.get('status')}", step.get("status"))
    step["status"] = SKIPPED
    log_event(step, "skipped", today, reason=reason)
    активные, assigned = _assign_dates(task, today)
    _save(ctx, task, today, expected, step_id)
    return _result(ctx, op, task, step, today, now, work,
                   next_step_id=активные[0].get("id") if активные else None,
                   next_step_title=активные[0].get("title") if активные else None,
                   dates_assigned=assigned)


def undo(ctx: Context, task_id: int, step_id, *, today, now, work) -> MarkResult:
    """Отменить последнее сегодняшнее действие над шагом.

    Зачем отдельная операция, а не хитрость на стороне морды: к моменту, когда
    человек понял, что промахнулся, запись уже в журнале. Оптимистичный
    интерфейс с кнопкой «Отменить» без неё был бы обманом — он показал бы
    откат, которого в сторе не произошло.

    **Запись из журнала удаляется, а не гасится обратной.** Промах — это не
    событие, которое случилось: оставшись строкой, он навсегда портил бы
    счётчик буксования, а тот здесь сигнал «нужен другой ход», а не украшение.

    **Только последнее и только сегодняшнее.** Это отмена промаха, а не правка
    истории. Граница «сегодня», потому что в журнале лежит дата без времени;
    минутное окно потребует колонки с временем в `step_log` (REFACTOR.md,
    срез 4).

    Оговорка, зафиксированная тестом: `done`, `skip` и `fail` попутно ставят
    дату контроля открывшимся шагам без даты, и откат этих дат не снимает — в
    журнале не записано, каким шагам они достались, а угадывать по совпадению
    даты значило бы иногда затирать дату, поставленную руками.
    """
    task = load_task(ctx, task_id)
    step = find_step(task, step_id)
    журнал = step.get("log") or []
    if not журнал:
        raise ValidationError.single(None, "по этому шагу нечего отменять")

    последнее = журнал[-1]
    событие = последнее.get("event")
    if событие not in CLOSING_EVENTS + MOVING_EVENTS:
        raise ValidationError.single(
            None, f"последнее в журнале — «{событие}», такое не отменяется")
    if as_date(последнее.get("date")) != today:
        raise ValidationError.single(None, "отменить можно только сегодняшнее действие")

    expected = (step["id"], step_snapshot(step))
    if событие in CLOSING_EVENTS:
        step["status"] = OPEN
        step["completed_date"] = None
    else:
        # `was` — дата контроля до переноса. Её может не быть вовсе: шаг без
        # даты переносить можно, и тогда откат возвращает то же отсутствие.
        step["control_date"] = последнее.get("was")
    журнал.pop()
    _save(ctx, task, today, expected, step_id)

    контроль = step.get("control_date")
    return _result(ctx, "undo", task, step, today, now, work,
                   undone=событие, control_date=str(контроль) if контроль else None,
                   stalled=stall_count(step))
