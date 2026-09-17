"""Модели запросов и ответов для задач (§2.1 спецификации среза 2). Владелец B1.

Три общие крошечные модели ниже импортируют также B2 и B3: `FieldError` —
элемент `errors[]` контракта, `FieldWarning` — мягкое предупреждение
(`soft_warnings`, ключ `warning`, не `error`), `OkResult` — ответ операции без
данных. `core/models.py` остаётся за лентой и отметками и не правится.

Пути полей здесь точечные (`steps.1.steps.0.control_date`, как их отдаёт
`domain`) — в скобки их переводит `api/errors.py::bracket_path` в
`api/v1/tasks.py` перед отдачей клиенту (Р1). Даты, которые клиент может
вернуть назад в поле формы (`start_date`, `control_date` шагов, `start_date`
задачи), — строки через `domain.ru_dates.format_control` (Р2); даты только
для чтения (`created`, `completed_date`, даты журнала) — типа `date`.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from core.models import FeedRow


class FieldError(BaseModel):
    field: str | None
    error: str


class FieldWarning(BaseModel):
    field: str
    warning: str


class OkResult(BaseModel):
    ok: bool = True


# --- запросы -----------------------------------------------------------

class StepIn(BaseModel):
    title: str = ""
    control_date: str | None = None      # человеческий ввод, разбирает ядро
    start_date: str | None = None
    note: str | None = None
    mode: str | None = None              # 'par' | 'seq'; проверяет domain
    steps: list[StepIn] = []             # непустой список или mode → группа


class StepEditIn(StepIn):
    id: int | None = None                # None → новый шаг
    # Переопределяем тип детей: без этого группа в режиме правки теряла бы id
    # своих подшагов (унаследованный `StepIn.steps` типизирован `list[StepIn]`,
    # там `id` нет вовсе) — правка состава внутри группы не смогла бы удержать
    # отметки на месте. Отклонение от §2.1 записано в отчёте B1.
    steps: list[StepEditIn] = []  # type: ignore[assignment]  # намеренная замена типа детей, см. выше


class TaskIn(BaseModel):
    title: str
    start_date: str | None = None        # по умолчанию today (ядро)
    tags: list[str] = []
    body: str = ""
    steps: list[StepIn]


class TaskEditIn(BaseModel):
    title: str
    start_date: str | None = None
    tags: list[str] | None = None        # None → не трогать (как apply_task_edit)
    body: str | None = None
    steps: list[StepEditIn]
    force: bool = False


class QuickIn(BaseModel):
    text: str


class CancelIn(BaseModel):
    reason: str | None = None


class PlanIn(BaseModel):
    task_id: int | None = None           # режим правки: сохранённые start_date известных id
    start_date: str | None = None
    steps: list[StepEditIn]


# --- ответы --------------------------------------------------------------

class PlannedStep(BaseModel):
    path: str                            # 'steps.1.steps.0' (в HTTP → 'steps[1].steps[0]')
    id: int | None
    mode: str | None
    start: str | None                    # format_control; дефолт или явный
    control: str | None
    explicit_start: bool
    steps: list[PlannedStep] = []


class PlanResult(BaseModel):
    ok: bool                             # errors пуст
    errors: list[FieldError]
    warnings: list[FieldWarning]
    steps: list[PlannedStep]


class LogEntry(BaseModel):
    step_id: int
    step_title: str | None
    date: date
    event: str
    reason: str | None = None
    was: str | None = None
    to: str | None = None


class CardStep(BaseModel):
    id: int
    title: str | None
    status: str
    start_date: str | None
    control_date: str | None             # format_control
    completed_date: date | None
    note: str | None
    mode: str | None
    closed: bool
    active: bool
    stalled: int
    state: str | None                    # worktime.due_state; None у группы и закрытого
    row: FeedRow | None = None           # у открытого листа: для ControlDialog
    # 'done','notdone','defer','fail','skip' у открытого листа; 'reopen' у
    # done; 'undo' если журнал сегодняшний и отменяемый.
    actions: list[str] = []
    steps: list[CardStep] = []


class TaskCard(BaseModel):
    ok: bool = True
    task_id: int
    task: str
    task_status: str                     # английский
    status_ru: str                       # STATUS_RU[task_status]
    body: str                            # без блока шагов
    created: date
    start_date: str                      # format_control
    tags: list[str]
    cancelled: bool = False
    cancelled_reason: str | None = None
    template_name: str | None = None
    cycle_key: str | None = None
    control_date: str | None
    current_step: str | None
    progress: str | None
    stalled: int                         # из domain.steps.task_summary
    steps: list[CardStep]
    history: list[LogEntry]              # все события всех шагов, по убыванию даты
    # 'close' (открытая), 'cancel' (не отменённая), 'delete', 'to_template'.
    actions: list[str]


class TaskSaveResult(BaseModel):
    ok: bool = True
    task_id: int
    task: str
    task_status: str
    steps: int
    created: bool                        # True у create/quick
    renamed_from: str | None = None
    warnings: list[FieldWarning] = []    # soft_warnings
    card: TaskCard


class CloseResult(BaseModel):
    ok: bool = True
    task_id: int
    closed_steps: int
    card: TaskCard


class TaskDeleteResult(BaseModel):
    ok: bool = True
    task_id: int
    task: str
    deleted: bool = True


class TaskRef(BaseModel):
    task_id: int
    task: str
