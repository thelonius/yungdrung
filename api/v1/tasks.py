"""Задачи: создание, карточка, правка, отмена, закрытие, переоткрытие,
удаление, живой предпросмотр плана (§1.1 спецификации среза 2). Владелец B1.

Тонкий слой над `core.tasks`: разбор `now`/`today`, перевод путей полей из
точечной формы ядра в скобочную для клиента (`api.errors.bracket_path`, Р1) —
исключения `core.errors` переводит в 404/409/422 общий обработчик
(`api/errors.py::install`), здесь их ловить не нужно.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from api.deps import ctx, moment
from api.errors import bracket_path
from core import tasks as core_tasks
from core.context import Context
from core.models_tasks import (
    CancelIn, CloseResult, PlanIn, PlanResult, PlannedStep, QuickIn, TaskCard, TaskDeleteResult,
    TaskEditIn, TaskIn, TaskRef, TaskSaveResult,
)
from domain import steps_plan

router = APIRouter(prefix="/api/v1", tags=["tasks"])


def _bracket_warnings(warnings):
    return [w.model_copy(update={"field": bracket_path(w.field)}) for w in warnings]


def _bracket_save_result(result: TaskSaveResult) -> TaskSaveResult:
    return result.model_copy(update={"warnings": _bracket_warnings(result.warnings)})


def _bracket_step(step: PlannedStep) -> PlannedStep:
    return step.model_copy(update={
        "path": bracket_path(step.path),
        "steps": [_bracket_step(c) for c in step.steps]})


def _bracket_plan(result: PlanResult) -> PlanResult:
    return result.model_copy(update={
        "errors": [e.model_copy(update={"field": bracket_path(e.field)}) for e in result.errors],
        "warnings": _bracket_warnings(result.warnings),
        "steps": [_bracket_step(s) for s in result.steps]})


@router.post("/tasks", response_model=TaskSaveResult)
def create(body: TaskIn, c: Context = Depends(ctx), now: datetime = Depends(moment)):
    today = now.date()
    data = body.model_dump()
    task = core_tasks.create_task(c, data, today)
    warnings = steps_plan.soft_warnings(data, today)
    return _bracket_save_result(
        core_tasks.save_result(c, task, today, now, c.work(), created=True, warnings=warnings))


@router.post("/tasks/quick", response_model=TaskSaveResult)
def quick(body: QuickIn, c: Context = Depends(ctx), now: datetime = Depends(moment)):
    result = core_tasks.quick_create(c, body.text, now.date(), now, c.work())
    return _bracket_save_result(result)


@router.post("/tasks/plan", response_model=PlanResult)
def plan(body: PlanIn, c: Context = Depends(ctx), now: datetime = Depends(moment)):
    # Живая проверка (Р9): плохой ввод — не 422, а 200 с `ok:false`, форма
    # опрашивает на каждое нажатие клавиши. `core.tasks.plan` не бросает
    # `ValidationError` — она копит ошибки в `PlanResult.errors`; 404 на чужой
    # `task_id` (режим правки) поднимается как есть, его ловит общий обработчик.
    return _bracket_plan(core_tasks.plan(c, body, now.date(), now))


@router.get("/tasks/resolve", response_model=TaskRef)
def resolve(title: str, c: Context = Depends(ctx)):
    return core_tasks.resolve_title(c, title)


@router.get("/tasks/{task_id}", response_model=TaskCard)
def show(task_id: int, c: Context = Depends(ctx), now: datetime = Depends(moment)):
    return core_tasks.show(c, task_id, now.date(), now, c.work())


@router.put("/tasks/{task_id}", response_model=TaskSaveResult)
def update(task_id: int, body: TaskEditIn, c: Context = Depends(ctx),
          now: datetime = Depends(moment)):
    result = core_tasks.update_task(c, task_id, body, now.date(), now, c.work())
    return _bracket_save_result(result)


@router.post("/tasks/{task_id}/cancel", response_model=TaskCard)
def cancel(task_id: int, body: CancelIn, c: Context = Depends(ctx),
          now: datetime = Depends(moment)):
    return core_tasks.cancel(c, task_id, body.reason, now.date(), now, c.work())


@router.post("/tasks/{task_id}/close", response_model=CloseResult)
def close(task_id: int, c: Context = Depends(ctx), now: datetime = Depends(moment)):
    return core_tasks.close(c, task_id, now.date(), now, c.work())


@router.post("/tasks/{task_id}/steps/{step_id}/reopen", response_model=TaskCard)
def reopen(task_id: int, step_id: int, c: Context = Depends(ctx),
          now: datetime = Depends(moment)):
    return core_tasks.reopen(c, task_id, step_id, now.date(), now, c.work())


@router.delete("/tasks/{task_id}", response_model=TaskDeleteResult)
def delete(task_id: int, c: Context = Depends(ctx)):
    return core_tasks.delete(c, task_id)
