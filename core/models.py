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
