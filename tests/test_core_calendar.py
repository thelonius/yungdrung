#!/usr/bin/env python3
"""Тесты `core.calendar` — сетки для пикера даты.

Разметка выходных и подсчёт контролей нужны пикеру готовыми: по `CONTRACT.md`
оболочка не вычисляет даже «выходной ли день». Здесь проверяется, что выходной
берётся из настроек заказчика (тот же `worktime.is_workday`, что метит строку
предпросмотра шаблона), что в `controls` попадают ровно те шаги, которые
считает лента, и что диапазон ограничен.
"""
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import settings as cfg  # noqa: E402
import store  # noqa: E402
from core import calendar as core_calendar  # noqa: E402
from core import feed as core_feed  # noqa: E402
from core.context import Context  # noqa: E402
from core.errors import ValidationError  # noqa: E402

ПН = date(2026, 8, 3)     # понедельник
СБ = date(2026, 8, 8)
ВС = date(2026, 8, 9)
NOW = datetime(2026, 8, 3, 11, 0)


@pytest.fixture
def ctx(tmp_path):
    return Context(tmp_path)


def завести(ctx, title, steps, *, cancelled=0):
    """Задача с шагами прямо в базе: `core.tasks.create_task` тянет за собой
    разбор дат и базу знаний, а здесь проверяется выборка, а не создание."""
    conn = sqlite3.connect(str(ctx.vault / "стор.db"))
    store.migrate_schema(conn)
    cur = conn.execute(
        "INSERT INTO tasks (title, schema, created, start_date, cancelled, body) "
        "VALUES (?, 1, '2026-08-01', '2026-08-01', ?, '')", (title, cancelled))
    tid = cur.lastrowid
    conn.executemany(
        "INSERT INTO steps (task_id, step_id, position, title, status, control_date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(tid, i, i - 1, f"Шаг {i}", статус, str(день) if день else None)
         for i, (статус, день) in enumerate(steps, start=1)])
    conn.commit()
    conn.close()
    return tid


def по_датам(result):
    return {d.date: d.controls for d in result.days if d.controls}


# --- выходные по настройкам, а не по номеру дня недели ----------------------

def test_суббота_и_воскресенье_выходные_по_умолчанию(ctx):
    дни = {d.date: d.weekend for d in core_calendar.days(ctx, ПН, ВС, ctx.work()).days}
    assert дни["2026-08-08"] is True
    assert дни["2026-08-09"] is True
    assert дни["2026-08-03"] is False


def test_при_работе_по_выходным_суббота_рабочая(ctx):
    """Заказчик включил работу по выходным — сетка обязана перекраситься вслед
    за настройкой, иначе пикер метит субботу серым, а показ уведомления
    назначается на ту же субботу."""
    cfg.save({"notifications": {"weekends": True}}, ctx.settings_path())
    дни = {d.date: d.weekend for d in core_calendar.days(ctx, ПН, ВС, ctx.work()).days}
    assert дни["2026-08-08"] is False
    assert дни["2026-08-09"] is False


# --- controls: те же шаги, что в ленте --------------------------------------

def test_контроли_раскладываются_по_своим_дням(ctx):
    завести(ctx, "Грант", [("pending", ПН)])
    завести(ctx, "Отчёт", [("pending", ПН)])
    завести(ctx, "Письмо", [("pending", СБ)])
    assert по_датам(core_calendar.days(ctx, ПН, ВС, ctx.work())) == {
        "2026-08-03": 2, "2026-08-08": 1}


def test_шаг_без_даты_не_попадает_никуда(ctx):
    завести(ctx, "Грант", [("pending", None)])
    assert по_датам(core_calendar.days(ctx, ПН, ВС, ctx.work())) == {}


def test_закрытые_и_отменённые_шаги_не_считаются(ctx):
    """Цепочка: первый шаг сделан, второй активен, третий ждёт своей очереди.
    В сетке обязан быть виден только второй — ровно то, что в этот день
    покажет лента."""
    завести(ctx, "Грант", [("done", ПН), ("pending", date(2026, 8, 4)),
                           ("pending", date(2026, 8, 5))])
    завести(ctx, "Отменённая", [("pending", ПН)], cancelled=1)
    assert по_датам(core_calendar.days(ctx, ПН, ВС, ctx.work())) == {"2026-08-04": 1}


def test_счёт_сходится_с_лентой(ctx):
    """Лента и сетка считают один и тот же набор: разойдись они — пикер красил
    бы дни, в которых заказчику ничего не покажут."""
    завести(ctx, "Грант", [("pending", ПН)])
    завести(ctx, "Отчёт", [("skipped", ПН), ("pending", ПН)])
    лента = core_feed.feed(ctx, NOW, ctx.work())
    сетка = core_calendar.days(ctx, ПН, ПН, ctx.work())
    assert сетка.days[0].controls == len(лента.feed) == 2


# --- границы диапазона ------------------------------------------------------

def test_обе_границы_включаются(ctx):
    дни = core_calendar.days(ctx, ПН, date(2026, 8, 5), ctx.work()).days
    assert [d.date for d in дни] == ["2026-08-03", "2026-08-04", "2026-08-05"]


def test_один_день_даёт_одну_строку(ctx):
    дни = core_calendar.days(ctx, ПН, ПН, ctx.work()).days
    assert [d.date for d in дни] == ["2026-08-03"]


def test_предельный_диапазон_проходит(ctx):
    конец = ПН + timedelta(days=core_calendar.MAX_DAYS - 1)
    assert len(core_calendar.days(ctx, ПН, конец, ctx.work()).days) == core_calendar.MAX_DAYS


def test_слишком_длинный_диапазон_даёт_ошибку_поля(ctx):
    конец = ПН + timedelta(days=core_calendar.MAX_DAYS)
    with pytest.raises(ValidationError) as e:
        core_calendar.days(ctx, ПН, конец, ctx.work())
    assert e.value.errors[0]["field"] == "to"


def test_конец_раньше_начала_даёт_ошибку_поля(ctx):
    with pytest.raises(ValidationError) as e:
        core_calendar.days(ctx, ВС, ПН, ctx.work())
    assert e.value.errors[0]["field"] == "to"


@pytest.mark.parametrize("плохая", ["чепуха", "2026-13-01", "08.08.2026", ""])
def test_кривая_дата_даёт_ошибку_поля_from(ctx, плохая):
    """Пикер шлёт машинный формат; человеческий ввод («завтра», «+3») разбирает
    `/parse-date`, а здесь любое отклонение — ошибка запроса, а не догадка."""
    with pytest.raises(ValidationError) as e:
        core_calendar.days(ctx, плохая, ВС, ctx.work())
    assert e.value.errors[0]["field"] == "from"
