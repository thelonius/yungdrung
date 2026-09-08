"""Момент «сейчас» как зависимость, а не как вызов `datetime.now()` в глубине.

Тесты и утренние прогоны воспроизводимы, когда дата задана снаружи; живой
запуск точен, когда время берётся настоящее. `derive_now` соединяет оба
случая так же, как это делал `engine._now`: заданный момент — как есть;
заданная только дата — с текущим временем суток.
"""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

import worktime


class Clock(Protocol):
    def today(self) -> date: ...

    def now(self) -> datetime: ...


class SystemClock:
    def today(self) -> date:
        return date.today()

    def now(self) -> datetime:
        return datetime.now()


@dataclass(frozen=True)
class FixedClock:
    """Часы, остановленные на заданном моменте — для тестов и `--today`."""

    moment: datetime

    def today(self) -> date:
        return self.moment.date()

    def now(self) -> datetime:
        return self.moment


def derive_now(today: date, now=None) -> datetime:
    """`--today` задаёт дату, время берём текущее — так тесты и утренние прогоны
    воспроизводимы, а живой запуск точен. Явный `now` (строка или datetime)
    побеждает."""
    if now:
        return worktime.as_datetime(now)
    сейчас = datetime.now()
    return сейчас if today == сейчас.date() else datetime.combine(today, сейчас.time())
