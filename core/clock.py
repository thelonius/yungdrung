"""Момент «сейчас» для операций ядра.

`derive_now` соединяет два случая так же, как это делал `engine._now`: заданный
момент — как есть; заданная только дата (`--today` в CLI) — с текущим временем
суток. Так тесты и утренние прогоны воспроизводимы, а живой запуск точен.
"""
from datetime import date, datetime

import worktime


def derive_now(today: date, now=None) -> datetime:
    """Явный `now` (строка или datetime) побеждает; иначе сегодняшняя дата с
    текущим временем."""
    if now:
        return worktime.as_datetime(now)
    сейчас = datetime.now()
    return сейчас if today == сейчас.date() else datetime.combine(today, сейчас.time())
