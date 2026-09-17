"""Модели ответов ядра. Это и есть контракт с оболочками: `api/` отдаёт их как
есть (и строит из них OpenAPI), `engine.py` раскладывает в прежние JSON-формы
CLI. Поля названы так, как их видит клиент (`CONTRACT.md`: код английский).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class FeedRow(BaseModel):
    """Строка ленты или завала. Всё вычислено здесь: оболочка только показывает.
    Раздел 6.1 ТЗ перечисляет, что видно в строке: название шага, название
    задачи, время контроля, теги, счётчик переносов, если больше нуля."""

    task_id: int
    task: str
    step: int
    title: str | None
    group: str | None = None
    note: str | None = None
    control_at: str | None
    show_at: str | None
    state: str
    postponed: int
    stalled: bool
    tags: list[str]
    last_reason: str | None
    actions: list[str]


class Counts(BaseModel):
    overdue: int
    today: int
    waiting: int


class FeedResult(BaseModel):
    """Лента «Что сегодня». Просроченное в строки не попадает: по 6.1 оно живёт
    отдельной плашкой — пятнадцать красных строк парализуют экран."""

    now: str
    feed: list[FeedRow]
    overdue_count: int
    counts: Counts
    next_ahead: FeedRow | None
    stalled_count: int
    broken: list[str] = Field(default_factory=list)


class BacklogResult(BaseModel):
    now: str
    backlog: list[FeedRow]
    count: int
    broken: list[str] = Field(default_factory=list)


class MarkResult(BaseModel):
    """Ответ на отметку шага. Несёт новое состояние — обновлённую строку ленты
    (`row`, None если шаг из ленты и завала ушёл) и счётчики, — чтобы клиенту
    не нужен был второй круг до сервера. Это контрактно чистая версия
    оптимистичного интерфейса: оболочка по-прежнему ничего не вычисляет."""

    ok: bool = True
    op: str
    task_id: int
    task: str
    step: int
    status: str
    task_status: str
    next_step_id: int | None = None
    next_step_title: str | None = None
    dates_assigned: list[int] = Field(default_factory=list)
    next_check: str | None = None
    stalled: int = 0
    hint: str | None = None
    undone: str | None = None
    control_date: str | None = None
    row: FeedRow | None = None
    counts: Counts


class WhenResult(BaseModel):
    """Во что превратился человеческий ввод даты — подсказка под полем формы.
    Подпись («завтра в 09:30», «18.10.2026, через 40 дн.») считается здесь,
    а не в браузере: по контракту оболочка не пересказывает данные ядра
    своими словами. Нераспознанный ввод — это ответ, а не ошибка запроса:
    поле опрашивается на каждое нажатие клавиши."""

    ok: bool
    date: str | None = None
    label: str | None = None
    past: bool = False
    error: str | None = None


class ExtractResult(BaseModel):
    """Быстрый ввод одной строкой: название, дата и границы распознанного куска
    в исходном тексте — для подсветки и вырезания из названия."""

    title: str
    date: str | None = None
    label: str | None = None
    past: bool = False
    span: tuple[int, int] | None = None


class SearchResult(BaseModel):
    ok: bool = True
    query: str
    count: int
    results: list[dict]


class ReasonsResult(BaseModel):
    """Справочник причин переноса и провала (раздел 5.4 ТЗ). Только активные:
    архивная причина для новой записи не годится, а в истории остаётся."""

    reasons: list[str]


class CalendarDay(BaseModel):
    """Клетка сетки месяца. `weekend` — по настройкам рабочего времени, а не по
    номеру дня недели: при работе по выходным суббота рабочая. `controls` —
    сколько активных шагов ждут контроля в этот день, по счёту ленты."""

    date: str
    weekend: bool
    controls: int


class CalendarResult(BaseModel):
    days: list[CalendarDay]
