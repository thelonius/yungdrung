#!/usr/bin/env python3
"""Тесты рабочего времени и просрочки — требование R19, разделы 8.1–8.4 ТЗ.

Все ожидаемые значения посчитаны руками по календарю, а не получены из самого
модуля: иначе тест проверял бы, что код согласен с собой.

Календарь августа 2026, который используется ниже:
  пн 17 · вт 18 · ср 19 · чт 20 · пт 21 · сб 22 · вс 23 · пн 24
"""
import sys
from datetime import date, datetime, time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import worktime  # noqa: E402

ПН = date(2026, 8, 17)
СБ = date(2026, 8, 22)


@pytest.fixture
def work():
    """Рабочее время по умолчанию: 09:00–21:00, выходные выключены."""
    return worktime.settings()


# --- настройки -------------------------------------------------------------

def test_перевёрнутый_интервал_отвергается():
    with pytest.raises(ValueError):
        worktime.settings(start=time(21, 0), end=time(9, 0))


def test_выходные_можно_включить():
    assert worktime.is_workday(СБ, worktime.settings()) is False
    assert worktime.is_workday(СБ, worktime.settings(weekends=True)) is True


# --- момент показа: три примера дословно из раздела 8.1 ---------------------

def test_контроль_в_субботу_уезжает_на_понедельник(work):
    """«Контроль в субботу 09:00, выходные выключены → уведомление в понедельник
    в 09:00». Дословный пример из ТЗ."""
    assert worktime.show_at(datetime(2026, 8, 22, 9, 0), work) == datetime(2026, 8, 24, 9, 0)


def test_контроль_поздно_вечером_уезжает_на_утро(work):
    """«Контроль в 23:30 буднего дня → уведомление на следующий день в 09:00»."""
    assert worktime.show_at(datetime(2026, 8, 17, 23, 30), work) == datetime(2026, 8, 18, 9, 0)


def test_контроль_в_рабочее_время_показывается_сразу(work):
    """«Контроль в 14:00 буднего дня → уведомление сразу»."""
    assert worktime.show_at(datetime(2026, 8, 17, 14, 0), work) == datetime(2026, 8, 17, 14, 0)


def test_контроль_до_начала_дня_ждёт_начала(work):
    """07:00 буднего — ждём девяти утра того же дня, а не следующего."""
    assert worktime.show_at(datetime(2026, 8, 17, 7, 0), work) == datetime(2026, 8, 17, 9, 0)


def test_ровно_конец_дня_уже_вне_рабочего_времени(work):
    """21:00 — рабочий день закончился, значит показ уезжает на утро."""
    assert worktime.show_at(datetime(2026, 8, 17, 21, 0), work) == datetime(2026, 8, 18, 9, 0)


def test_дата_без_времени_считается_от_начала_суток(work):
    """Шаг, назначенный «на 18 августа» без часа, показывается в начало рабочего
    дня, а не в полночь."""
    assert worktime.show_at(date(2026, 8, 18), work) == datetime(2026, 8, 18, 9, 0)


def test_воскресенье_при_включённых_выходных_показывается_сразу():
    work = worktime.settings(weekends=True)
    assert worktime.show_at(datetime(2026, 8, 23, 12, 0), work) == datetime(2026, 8, 23, 12, 0)


# --- тихий режим -----------------------------------------------------------

def test_тихий_режим_отодвигает_показ():
    """«Не беспокоить до 24 августа»: контроль 18-го показывается 24-го утром."""
    work = worktime.settings(quiet_until=datetime(2026, 8, 24, 9, 0))
    assert worktime.show_at(datetime(2026, 8, 18, 10, 0), work) == datetime(2026, 8, 24, 9, 0)


def test_тихий_режим_не_трогает_то_что_позже_него():
    work = worktime.settings(quiet_until=datetime(2026, 8, 20, 9, 0))
    assert worktime.show_at(datetime(2026, 8, 21, 10, 0), work) == datetime(2026, 8, 21, 10, 0)


# --- просрочка: раздел 8.2 -------------------------------------------------

def test_время_прошло_но_день_идёт_не_просрочка(work):
    """Ключевое место. Контроль в 10:00, сейчас 14:00 того же дня — НЕ просрочено.
    Именно из этого следует, что лента не засоряется просрочкой."""
    assert worktime.is_overdue(datetime(2026, 8, 17, 10, 0),
                               datetime(2026, 8, 17, 14, 0), work) is False


def test_рабочий_день_кончился_без_ответа_просрочка(work):
    assert worktime.is_overdue(datetime(2026, 8, 17, 10, 0),
                               datetime(2026, 8, 17, 21, 0), work) is True


def test_до_наступления_рабочего_времени_просрочка_не_начисляется(work):
    """«Пока рабочее время не наступило, просрочка не начисляется». Контроль в
    субботу, сейчас воскресенье — показ ещё не состоялся, просрочки нет."""
    assert worktime.is_overdue(datetime(2026, 8, 22, 9, 0),
                               datetime(2026, 8, 23, 12, 0), work) is False


def test_будущий_шаг_не_просрочен(work):
    assert worktime.is_overdue(datetime(2026, 8, 20, 10, 0),
                               datetime(2026, 8, 17, 14, 0), work) is False


def test_субботний_контроль_просрочивается_только_к_вечеру_понедельника(work):
    """Показ уезжает на понедельник 09:00, значит просрочка — с 21:00 понедельника,
    а не с воскресенья."""
    контроль = datetime(2026, 8, 22, 9, 0)
    assert worktime.is_overdue(контроль, datetime(2026, 8, 24, 20, 0), work) is False
    assert worktime.is_overdue(контроль, datetime(2026, 8, 24, 21, 0), work) is True


# --- состояние для ленты ---------------------------------------------------

@pytest.mark.parametrize("контроль, сейчас, ожидаем", [
    (datetime(2026, 8, 20, 10, 0), datetime(2026, 8, 17, 14, 0), "waiting"),
    (datetime(2026, 8, 17, 10, 0), datetime(2026, 8, 17, 14, 0), "due"),
    (datetime(2026, 8, 17, 14, 0), datetime(2026, 8, 17, 14, 0), "due"),
    (datetime(2026, 8, 17, 10, 0), datetime(2026, 8, 18, 10, 0), "overdue"),
    (None, datetime(2026, 8, 17, 14, 0), "no_date"),
])
def test_состояние_элемента(контроль, сейчас, ожидаем, work):
    assert worktime.due_state(контроль, сейчас, work) == ожидаем


# --- горизонт ленты: раздел 6.1 --------------------------------------------

def test_лента_берёт_сегодняшнее(work):
    assert worktime.in_horizon(datetime(2026, 8, 17, 16, 0),
                               datetime(2026, 8, 17, 10, 0), work) is True


def test_лента_не_берёт_завтрашнее(work):
    assert worktime.in_horizon(datetime(2026, 8, 18, 10, 0),
                               datetime(2026, 8, 17, 10, 0), work) is False


def test_лента_не_берёт_просроченное(work):
    """«Просроченное в ленту не выводится списком» — оно живёт отдельной плашкой."""
    assert worktime.in_horizon(datetime(2026, 8, 17, 10, 0),
                               datetime(2026, 8, 18, 10, 0), work) is False


def test_горизонт_можно_расширить(work):
    """Горизонт настраивается: с ним завтрашнее в ленту попадает."""
    assert worktime.in_horizon(datetime(2026, 8, 18, 10, 0),
                               datetime(2026, 8, 17, 10, 0), work,
                               horizon=datetime(2026, 8, 18, 23, 59)) is True
