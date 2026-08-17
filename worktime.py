#!/usr/bin/env python3
"""Рабочее время и просрочка. Требование R19, определения из разделов 8.1–8.4 ТЗ.

Отдельным модулем, потому что это чистые вычисления над датами: ни файлов, ни базы,
ни интерфейса. Смена хранилища этот код не тронет.

Главное правило, которое легко перепутать: **время контроля не сдвигается никогда**.
Вне рабочих часов сдвигается только момент показа уведомления. В журнал это не
попадает, счётчик переносов не растёт — заказчик не переносил шаг, это система
промолчала до утра.

Второе правило: просрочка начисляется не по наступлению времени контроля, а по
окончании рабочего дня без ответа (раздел 8.2). Шаг с контролем в 10:00, не отвеченный
в 14:00 того же дня, ещё не просрочен и остаётся в ленте. Отсюда же следует, что
в ленте просроченного не бывает вовсе: оно уходит в завал.
"""
from datetime import date, datetime, time, timedelta

# Значения по умолчанию из раздела 8.1. Заказчик их пока не подтвердил — вопрос 7
# в письме, поэтому держим одним словарём, а не константами по коду.
DEFAULT = {
    "start": time(9, 0),
    "end": time(21, 0),
    "weekends": False,     # показывать ли уведомления в субботу и воскресенье
    "quiet_until": None,   # тихий режим: «не беспокоить до даты», отпуск и болезнь
}

СУББОТА = 5


def settings(**overrides):
    """Настройки рабочего времени с подстановкой значений по умолчанию."""
    сетка = dict(DEFAULT)
    сетка.update({k: v for k, v in overrides.items() if v is not None})
    if сетка["start"] >= сетка["end"]:
        raise ValueError("начало рабочего дня должно быть раньше конца")
    return сетка


def as_datetime(value, *, start_of_day=time(0, 0)):
    """Дата без времени означает весь день: считаем её от начала суток.

    Так шаг, назначенный «на 18 августа» без часа, попадёт в показ в начало
    рабочего дня, а не в полночь.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, start_of_day)
    return datetime.fromisoformat(str(value).strip())


def is_workday(day, work):
    return work["weekends"] or day.weekday() < СУББОТА


def is_working_moment(moment, work):
    """Попадает ли момент в рабочее время. Границы включительно по началу и
    исключительно по концу: в 21:00 рабочий день уже закончился."""
    return (is_workday(moment.date(), work)
            and work["start"] <= moment.time() < work["end"])


def next_working_start(moment, work):
    """Ближайшее начало рабочего времени не раньше момента."""
    день = moment.date()
    # Если сегодня рабочий и до начала ещё не дошли — сегодня же.
    if is_workday(день, work) and moment.time() < work["start"]:
        return datetime.combine(день, work["start"])
    день += timedelta(days=1)
    for _ in range(14):  # запас: даже при выключенных выходных хватает с лихвой
        if is_workday(день, work):
            return datetime.combine(день, work["start"])
        день += timedelta(days=1)
    raise ValueError("не нашлось рабочего дня — проверь настройки")


def show_at(control_at, work):
    """Когда система имеет право показать уведомление.

    Значение control_at не меняется: возвращается только момент показа.
    Примеры из раздела 8.1:
      контроль в субботу 09:00, выходные выключены → понедельник 09:00
      контроль в 23:30 буднего дня               → следующий день 09:00
      контроль в 14:00 буднего дня               → сразу, 14:00
    """
    moment = as_datetime(control_at)
    if moment is None:
        return None
    тихо_до = work.get("quiet_until")
    if тихо_до is not None:
        порог = as_datetime(тихо_до)
        if moment < порог:
            moment = порог
    if is_working_moment(moment, work):
        return moment
    return next_working_start(moment, work)


def working_day_end(moment, work):
    """Конец рабочего дня, к которому относится момент."""
    return datetime.combine(moment.date(), work["end"])


def is_overdue(control_at, now, work):
    """Просрочен ли элемент. Раздел 8.2: только если рабочий день, в который его
    показали, закончился без ответа.

    Пока рабочее время не наступило, просрочка не начисляется — поэтому шаг,
    назначенный на послезавтра, никогда не просрочен, а шаг на сегодня 10:00 не
    просрочен до 21:00 того же дня.
    """
    показ = show_at(control_at, work)
    if показ is None:
        return False
    return working_day_end(показ, work) <= as_datetime(now)


def due_state(control_at, now, work):
    """Состояние элемента для ленты и завала.

    waiting — показывать ещё рано
    due     — пора показывать, ответа ждём
    overdue — рабочий день кончился без ответа, элемент уходит в завал
    """
    if control_at is None:
        return "no_date"
    if is_overdue(control_at, now, work):
        return "overdue"
    return "due" if show_at(control_at, work) <= as_datetime(now) else "waiting"


def in_horizon(control_at, now, work, horizon=None):
    """Попадает ли элемент в ленту «Что сегодня» (раздел 6.1).

    Горизонт по умолчанию — до конца текущего дня. Просроченное в ленту не
    попадает: по 6.1 оно живёт отдельной плашкой, а не строками.
    """
    if control_at is None:
        return False
    сейчас = as_datetime(now)
    предел = as_datetime(horizon) if horizon else datetime.combine(
        сейчас.date(), time(23, 59, 59))
    состояние = due_state(control_at, сейчас, work)
    if состояние == "overdue":
        return False
    return show_at(control_at, work) <= предел
