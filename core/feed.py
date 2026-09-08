"""Лента «Что сегодня», разбор завала и «ждут» — три разреза одного набора
открытых шагов. Считается одним проходом: считать дважды значит однажды
разойтись (`CONTRACT.md`: лента и завал должны сходиться всегда).
"""
from datetime import datetime

import worktime
from core.context import Context
from core.models import BacklogResult, Counts, FeedResult, FeedRow
from domain.steps import current_steps, stall_count, steps_of


def feed_row(task, step, now: datetime, work, group=None) -> FeedRow:
    control = step.get("control_date")
    показ = worktime.show_at(control, work) if control else None
    return FeedRow(
        task_id=task["path"].id,
        task=task["path"].stem,
        step=step["id"],
        title=step.get("title"),
        group=group,
        note=step.get("note"),
        control_at=str(control) if control else None,
        show_at=показ.isoformat() if показ else None,
        state=worktime.due_state(control, now, work),
        postponed=stall_count(step),
        stalled=stall_count(step) >= 3,
        tags=task["meta"].get("tags") or [],
        last_reason=next(
            (e.get("reason") for e in reversed(step.get("log") or []) if e.get("reason")),
            None),
        actions=["done", "notdone", "defer", "skip"],
    )


def collect_open(ctx: Context, now: datetime, work):
    """Открытые шаги всех задач, разложенные по состоянию: (лента, завал, ждут).

    Читаются только задачи с открытыми листьями и без отмены — остальным здесь
    взяться неоткуда по определению набора (`store.tasks_with_open_steps`,
    частичный индекс `idx_steps_open`): объём текущей работы не зависит от
    того, насколько вырос архив.
    """
    лента: list[FeedRow] = []
    завал: list[FeedRow] = []
    ждут: list[FeedRow] = []
    for task in ctx.store.tasks_with_open_steps():
        по_id = {s["id"]: s for s in steps_of(task)}
        # Параллельная группа даёт несколько активных листьев — и несколько
        # строк ленты: у каждого свой срок, прятать их друг за друга нечестно.
        # Название группы едет в строку контекстом.
        for step in current_steps(task):
            родитель = по_id.get(step.get("parent"))
            row = feed_row(task, step, now, work,
                           group=родитель.get("title") if родитель else None)
            if row.state == "overdue":
                завал.append(row)
            elif worktime.in_horizon(step.get("control_date"), now, work):
                лента.append(row)
            else:
                ждут.append(row)

    def ключ(r: FeedRow):
        return (r.show_at or "9999", r.task, r.step)

    return sorted(лента, key=ключ), sorted(завал, key=ключ), sorted(ждут, key=ключ)


def _counts(лента, завал, ждут) -> Counts:
    return Counts(overdue=len(завал), today=len(лента), waiting=len(ждут))


def feed(ctx: Context, now: datetime, work) -> FeedResult:
    лента, завал, ждут = collect_open(ctx, now, work)
    return FeedResult(
        now=now.isoformat(),
        feed=лента,
        overdue_count=len(завал),
        counts=_counts(лента, завал, ждут),
        next_ahead=ждут[0] if ждут else None,
        stalled_count=sum(1 for r in лента + завал if r.stalled),
    )


def backlog(ctx: Context, now: datetime, work) -> BacklogResult:
    """Разбор завала — раздел 6.9 ТЗ. Сортировка: сначала самое давнее, по
    `show_at` возрастанием — пункт ТЗ явный и без исключений для буксующих."""
    _, завал, _ = collect_open(ctx, now, work)
    завал.sort(key=lambda r: r.show_at or "9999")
    return BacklogResult(now=now.isoformat(), backlog=завал, count=len(завал))


def state_after(ctx: Context, now: datetime, work, task_id: int, step_id: int):
    """Счётчики и строка данного шага после записи — для ответа на отметку.
    Строка ищется во всех трёх разрезах: перенесённый шаг мог уйти из ленты в
    «ждут», а «не сделан» без даты остаётся где был."""
    лента, завал, ждут = collect_open(ctx, now, work)
    row = next((r for r in лента + завал + ждут
                if r.task_id == task_id and r.step == step_id), None)
    return _counts(лента, завал, ждут), row
