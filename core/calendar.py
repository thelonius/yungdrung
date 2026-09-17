"""Сетка календаря на диапазон дней: выходной ли день и сколько контролей на него
приходится.

Пикер даты красит клетки месяца и обязан получить оба признака готовыми
(`CONTRACT.md`: оболочка не вычисляет даже «выходной ли»). Выходной считается по
настройкам рабочего времени, а не по номеру дня недели: при включённой работе по
выходным суббота рабочая. Отвечает на это `worktime.is_workday` — тот же вызов,
что в предпросмотре шаблона (`templates.preview`) и в повторениях, поэтому
календарь и предпросмотр не могут разойтись.
"""
from collections import Counter
from datetime import date, timedelta

import worktime
from core.context import Context
from core.errors import ValidationError
from core.models import CalendarDay, CalendarResult
from domain.ru_dates import as_date
from domain.steps import current_steps

# Предел диапазона. Пикеру хватает месяца с запасом на соседний, а без предела
# запрос на десять лет означал бы полное чтение стора ради 3652 строк ответа.
MAX_DAYS = 62

НЕ_ДАТА = "Нужна дата в виде ГГГГ-ММ-ДД"


def _day(value: object, field: str) -> date:
    """ISO-дата из параметра запроса. Пикер шлёт машинный формат, поэтому
    человеческий разбор («завтра», «+3») здесь не нужен: клетку сетки он
    получает не от человека, а от своего же расчёта месяца."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        raise ValidationError.single(field, НЕ_ДАТА) from None


def _controls(ctx: Context) -> Counter:
    """Дата контроля → сколько активных листьев на неё назначено.

    Набор шагов тот же, что у ленты (`core.feed.collect_open`): задачи с
    открытыми листьями и без отмены, из них — активные листья. Считать шире
    значило бы красить дни шагами, которых в ленте в этот день не будет:
    закрытыми, отменёнными и теми, до которых очередь в цепочке ещё не дошла.
    """
    счёт: Counter = Counter()
    for task in ctx.store.tasks_with_open_steps():
        for step in current_steps(task):
            день = as_date(step.get("control_date"))
            if день is not None:
                счёт[день] += 1
    return счёт


def days(ctx: Context, start: object, end: object, work: dict) -> CalendarResult:
    """Строка на каждый день диапазона, обе границы включительно."""
    первый = _day(start, "from")
    последний = _day(end, "to")
    if последний < первый:
        raise ValidationError.single("to", "Конец диапазона раньше начала")
    длина = (последний - первый).days + 1
    if длина > MAX_DAYS:
        raise ValidationError.single("to", f"Диапазон длиннее {MAX_DAYS} дней")

    счёт = _controls(ctx)
    return CalendarResult(days=[
        CalendarDay(
            date=день.isoformat(),
            weekend=not worktime.is_workday(день, work),
            controls=счёт.get(день, 0),
        )
        for день in (первый + timedelta(days=i) for i in range(длина))
    ])
