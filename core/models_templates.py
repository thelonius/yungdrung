"""Модели запросов и ответов для шаблонов и правил повторения (§2.2
спецификации среза 2). Владелец B2.

Отдельный файл, а не дописка в `core/models.py`: три агента среза не правят
один файл, а `api/` и `engine.py` импортируют модели по имени модуля. Модели
запросов (`*In`) лежат рядом с ответами: они часть контракта и уходят в
`schema.d.ts` вместе с ним.

Даты, которые клиент может вернуть назад (`anchor`, `start`, `until` в
`RuleIn`), принимаются строкой — человеческий ввод разбирает ядро от `today`
вызывающего. Даты только для чтения (`RecurrenceView.anchor`, `until`,
`TemplatePreview.start`) типизированы `date` и уходят ISO.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from core.models_tasks import FieldError


class TemplateStepIn(BaseModel):
    title: str = ""
    # Сырая строка допускается намеренно: форма шлёт нечисловой промежуток
    # как есть, и на него ядро отвечает ошибкой поля `steps[i].offset_days`
    # (решение CLAUDE.md о приращениях). Pydantic здесь не должен отбраковать
    # ввод раньше валидатора шаблона с его текстом.
    offset_days: int | str | None = None
    time_of_day: str | None = None
    position: int | None = None


class RuleIn(BaseModel):
    """Правило повторения как его вводит форма. В ядро уходит
    `model_dump(exclude_unset=True)`: незаданное поле не затирает дефолты
    `recurrence.normalize_rule`, а поля без виджета (`holiday_shift`,
    `lead_days`, `until`, `paused`) возвращаются такими, какими пришли из
    `GET` — `describe` их проговаривает, терять нельзя."""

    model_config = ConfigDict(extra="forbid")

    anchor: str | None = None
    freq: str | None = None
    interval: int | None = None
    byweekday: list[int] | None = None
    bymonthday: list[int] | None = None
    bysetpos: list[int] | None = None
    bymonth: list[int] | None = None
    holiday_shift: str | None = None
    lead_days: int | None = None
    until: str | None = None
    paused: bool | None = None


class TemplateIn(BaseModel):
    name: str
    tags: list[str] = Field(default_factory=list)
    body: str = ""
    steps: list[TemplateStepIn]
    recurrence: RuleIn | None = None


class TemplateStep(BaseModel):
    position: int
    title: str
    offset_days: int
    time_of_day: str | None


class RecurrenceView(BaseModel):
    """Нормализованное правило со склада плюс подпись `description` —
    её считает `recurrence.describe`, а не морда: оболочка правило словами
    не пересказывает."""

    anchor: date
    freq: str
    interval: int
    byweekday: list[int]
    bymonthday: list[int]
    bysetpos: list[int]
    bymonth: list[int]
    holiday_shift: str
    lead_days: int
    until: date | None
    paused: bool
    description: str


class TemplateCard(BaseModel):
    ok: bool = True
    name: str
    tags: list[str]
    body: str
    steps: list[TemplateStep]
    steps_count: int
    attachments_count: int
    recurrence: RecurrenceView | None


class TemplateList(BaseModel):
    templates: list[TemplateCard]
    count: int


class PreviewRow(BaseModel):
    position: int
    title: str
    offset_days: int
    control_date: str      # `templates.control_text`, с пробелом — этот вид понимает разбор дат
    control_text: str
    weekday: str
    on_weekend: bool
    show_at: str


class TemplatePreviewIn(BaseModel):
    template: TemplateIn
    start: str | None = None


class TemplatePreview(BaseModel):
    """Живой предпросмотр: плохой ввод — это ответ `ok: false`, а не ошибка
    запроса, потому что форма спрашивает на каждое нажатие."""

    ok: bool
    start: date | None
    start_text: str | None
    steps: list[PreviewRow] = Field(default_factory=list)
    errors: list[FieldError] = Field(default_factory=list)


class InstantiateIn(BaseModel):
    start: str | None = None
    title: str | None = None


class FromTemplateResult(BaseModel):
    ok: bool = True
    task_id: int
    task: str
    template: str
    steps: int
    attachments: int
    task_status: str       # английский, как везде в v1


class FromTaskIn(BaseModel):
    task_id: int
    name: str | None = None


class TemplateDeleteResult(BaseModel):
    ok: bool = True
    template: str
    deleted: bool = True


class RuleParseResult(BaseModel):
    ok: bool
    rule: RuleIn | None = None
    description: str | None = None
    errors: list[FieldError] = Field(default_factory=list)


class RulePreviewIn(BaseModel):
    anchor: str | None = None
    rule: RuleIn


class RuleDate(BaseModel):
    date: date
    text: str


class RulePreviewResult(BaseModel):
    ok: bool
    description: str | None = None
    anchor: date | None = None
    preview: list[RuleDate] = Field(default_factory=list)
    errors: list[FieldError] = Field(default_factory=list)
