#!/usr/bin/env python3
"""Тесты доставки напоминаний — раздел 8.4 ТЗ.

Проверяется решение «кого будить и когда повторить». Способ показа (системный
тост) здесь не проверяется: он требует живой Windows, и на маке его не прогнать.
Поэтому логика намеренно отделена от канала — она вся тестируется, а непроверяемым
остаётся только вызов PowerShell.

Календарь: 17 августа 2026 — понедельник. Рабочее время 09:00–21:00.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify  # noqa: E402
import worktime  # noqa: E402


@pytest.fixture
def work():
    return worktime.settings()


def элемент(task="Оплатить аренду", step=1, show="2026-08-17T10:00:00",
            control="2026-08-17 10:00", postponed=0):
    return {"task": task, "step": step, "title": "Свериться с суммой",
            "show_at": show, "control_at": control, "postponed": postponed}


# --- когда показывать ------------------------------------------------------

def test_новый_элемент_показывается(work):
    assert notify.should_send(элемент(), {}, datetime(2026, 8, 17, 10, 0), work)


def test_до_времени_показа_молчим(work):
    assert not notify.should_send(элемент(), {}, datetime(2026, 8, 17, 9, 30), work)


def test_после_конца_рабочего_дня_молчим(work):
    """Раздел 8.4: повторяем до ответа или до конца рабочего дня. Дальше элемент
    просрочен и уходит в завал — там его показывает разбор, а не уведомления."""
    assert not notify.should_send(элемент(), {}, datetime(2026, 8, 17, 21, 0), work)


def test_повтор_не_раньше_пятнадцати_минут(work):
    now = datetime(2026, 8, 17, 10, 0)
    state = notify.record({}, элемент(), now)
    assert not notify.should_send(элемент(), state, now + timedelta(minutes=14), work)
    assert notify.should_send(элемент(), state, now + timedelta(minutes=15), work)


def test_перенос_вперёд_снимает_показ_до_нового_времени(work):
    """Показали в 10:00, человек перенёс на 14:00. До 14:00 молчим, хотя запись о
    показе есть и пятнадцать минут прошли: это уже про другое время."""
    now = datetime(2026, 8, 17, 10, 0)
    state = notify.record({}, элемент(), now)
    перенесённый = элемент(show="2026-08-17T14:00:00", control="2026-08-17 14:00")
    assert not notify.should_send(перенесённый, state, datetime(2026, 8, 17, 13, 0), work)
    assert notify.should_send(перенесённый, state, datetime(2026, 8, 17, 14, 0), work)


def test_смена_даты_контроля_не_ждёт_пятнадцати_минут(work):
    """Дата изменилась — значит человек ответил, и это уже другое уведомление.
    Ждать четверть часа от показа про старое время незачем.

    Случай узкий, но достижимый: время сдвинули назад, на уже прошедший момент.
    """
    state = notify.record({}, элемент(), datetime(2026, 8, 17, 10, 0))
    сдвинутый = элемент(show="2026-08-17T09:30:00", control="2026-08-17 09:30")
    assert notify.should_send(сдвинутый, state, datetime(2026, 8, 17, 10, 5), work)


def test_элемент_без_времени_показа_пропускается(work):
    assert not notify.should_send(элемент(show=None), {}, datetime(2026, 8, 17, 10, 0), work)


# --- отметки о показе ------------------------------------------------------

def test_счётчик_растёт_на_повторах():
    now = datetime(2026, 8, 17, 10, 0)
    state = {}
    for i in range(3):
        notify.record(state, элемент(), now + timedelta(minutes=15 * i))
    assert state["Оплатить аренду::1"]["count"] == 3


def test_счётчик_сбрасывается_после_ответа():
    """Перенесли — начинается новая история показов, а не продолжается старая."""
    state = notify.record({}, элемент(), datetime(2026, 8, 17, 10, 0))
    notify.record(state, элемент(control="2026-08-18 10:00"), datetime(2026, 8, 18, 10, 0))
    assert state["Оплатить аренду::1"]["count"] == 1


def test_закрытые_шаги_забываются():
    """Иначе файл доставки растёт вечно и хранит задачи, закрытые полгода назад."""
    state = notify.record({}, элемент(), datetime(2026, 8, 17, 10, 0))
    notify.record(state, элемент(task="Заявка на грант"), datetime(2026, 8, 17, 10, 0))
    осталось = notify.forget_closed(state, [элемент()])
    assert list(осталось) == ["Оплатить аренду::1"]


# --- отбор пачкой ----------------------------------------------------------

def test_отбирает_только_подошедшие(work):
    now = datetime(2026, 8, 17, 10, 0)
    items = [
        элемент(task="Аренда", show="2026-08-17T09:00:00"),
        элемент(task="Грант", show="2026-08-17T16:00:00"),   # ещё рано
        элемент(task="Страховка", show="2026-08-17T10:00:00"),
    ]
    отобраны = [i["task"] for i in notify.pick(items, {}, now, work)]
    assert отобраны == ["Аренда", "Страховка"]


# --- хранение отметок ------------------------------------------------------

def test_битый_файл_доставки_не_роняет(tmp_path):
    """Хуже показать лишний раз, чем упасть в планировщике, где никто не увидит."""
    notify.state_path(tmp_path).write_text("{это не json", encoding="utf-8")
    assert notify.load_state(tmp_path) == {}


def test_отметки_переживают_запись_и_чтение(tmp_path):
    state = notify.record({}, элемент(), datetime(2026, 8, 17, 10, 0))
    notify.save_state(tmp_path, state)
    assert notify.load_state(tmp_path) == state


# --- текст уведомления -----------------------------------------------------

def test_один_элемент_показывается_подробно():
    заголовок, текст = notify.подпись([элемент(postponed=2)])
    assert заголовок == "Оплатить аренду"
    assert "Свериться с суммой" in текст and "переносов 2" in текст


def test_несколько_собираются_в_одно_уведомление():
    """Пять отдельных тостов подряд человек закроет не глядя."""
    заголовок, текст = notify.подпись([элемент(task=f"Задача {i}") for i in range(6)])
    assert заголовок == "Задач на сейчас: 6"
    assert "и ещё 2" in текст


def test_опасные_символы_в_названии_экранируются():
    """Название задачи попадает в XML внутри PowerShell — «Иванов & Ко» не должно
    ломать разметку тоста."""
    assert notify.WindowsToast.escape('Иванов & Ко <"тест">') == \
        "Иванов &amp; Ко &lt;&quot;тест&quot;&gt;"
