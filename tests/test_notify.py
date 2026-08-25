#!/usr/bin/env python3
"""Тесты доставки напоминаний — раздел 8.4 ТЗ.

Проверяется решение «кого будить и когда повторить». Способ показа (системный
тост) здесь не проверяется: он требует живой Windows, и на маке его не прогнать.
Поэтому логика намеренно отделена от канала — она вся тестируется, а непроверяемым
остаётся только вызов PowerShell.

Календарь: 17 августа 2026 — понедельник. Рабочее время 09:00–21:00.
"""
import json
import sys
from datetime import date, datetime, timedelta
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


# --- remind.py: настройки вольта доходят до напоминаний ----------------------
#
# У `remind.py` не было ни одного теста, и обе найденные при сверке ошибки
# жили именно там: рабочие часы и повтор брались из констант, а не из файла
# настроек. Настройка была, кнопка была, эффекта не было.

import settings as cfg  # noqa: E402
import engine  # noqa: E402
import remind  # noqa: E402


@pytest.fixture
def вольт(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    monkeypatch.setattr(cfg, "VAULT", tmp_path)
    return tmp_path


def test_рабочие_часы_напоминаний_берутся_из_настроек(вольт):
    """`remind.py` жил на зашитых 09:00–21:00: заказчик ставил конец дня в
    18:00, лента и завал его слушались, а тосты шли до девяти вечера."""
    данные = cfg.defaults()
    данные["notifications"]["start"] = "10:00"
    данные["notifications"]["end"] = "18:00"
    cfg.save(данные, cfg.settings_path(вольт))

    work = engine._work(None)
    assert (work["start"].hour, work["end"].hour) == (10, 18)
    # Ровно то, что теперь зовёт remind.main() вместо worktime.settings():
    # зашитые значения дали бы 09:00–21:00 и тосты после конца рабочего дня.
    зашитые = worktime.settings()
    assert (work["start"], work["end"]) != (зашитые["start"], зашитые["end"])


def test_повтор_берётся_из_настроек_а_аргумент_его_перекрывает(вольт):
    """`repeat_minutes` лежал в файле мёртвым ключом: аргумент `--repeat` имел
    значение по умолчанию, поэтому «никто не просил» было неотличимо от
    «человек попросил 15», и файл не спрашивали никогда."""
    данные = cfg.defaults()
    данные["notifications"]["repeat_minutes"] = 45
    cfg.save(данные, cfg.settings_path(вольт))

    from types import SimpleNamespace
    assert remind.повтор_минут(SimpleNamespace(repeat=None)) == 45
    assert remind.повтор_минут(SimpleNamespace(repeat=5)) == 5


def test_битый_файл_настроек_не_отменяет_напоминание(вольт):
    """Молчать вместо напоминания из-за испорченного JSON нельзя: почему файл
    битый, разбирается в настройках-интерфейсе."""
    cfg.settings_path(вольт).write_text("{ не json", encoding="utf-8")
    from types import SimpleNamespace
    assert remind.повтор_минут(SimpleNamespace(repeat=None)) == notify.ПОВТОР_МИНУТ


# --- звук тоста и автобэкап по расписанию (сверка покрытия) ------------------

def test_тост_без_звука_несёт_silent_audio(monkeypatch):
    """`notifications.sound=False` должен реально выключать звук, а не просто
    валидироваться и лежать в файле."""
    захвачено = {}

    class Заглушка:
        returncode = 0
        stderr = ""

    def перехват(cmd, **kw):
        захвачено["script"] = cmd[-1]
        return Заглушка()

    monkeypatch.setattr("subprocess.run", перехват)
    канал = notify.WindowsToast()
    канал.send([{"task": "Т", "title": "Ш", "postponed": 0}], sound=False)
    assert '<audio silent="true"/>' in захвачено["script"]

    канал.send([{"task": "Т", "title": "Ш", "postponed": 0}], sound=True)
    assert '<audio silent="true"/>' not in захвачено["script"]


def test_автобэкап_снимает_копию_когда_пора(вольт):
    (вольт / "Задачи").mkdir()
    данные = cfg.defaults()
    данные["backup"]["frequency_hours"] = 24
    данные["backup"]["folder"] = str(вольт / "копии")
    cfg.save(данные, cfg.settings_path(вольт))

    remind.автобэкап(date(2026, 8, 24))
    копии = list((вольт / "копии").glob("*.zip"))
    assert len(копии) == 1


def test_автобэкап_на_битом_файле_настроек_падает_на_дефолт(вольт):
    """`_backup_settings` ловит `SettingsError` и подставляет дефолты (частота
    24 часа) — автобэкап не должен упасть из-за испорченного JSON, он же не
    виноват в том, что файл настроек сломан руками."""
    (вольт / "Задачи").mkdir()
    cfg.settings_path(вольт).write_text("{ не json", encoding="utf-8")

    remind.автобэкап(date(2026, 8, 24))
    копии = list(engine.default_backup_dir().glob("*.zip"))
    assert len(копии) == 1


def test_автобэкап_не_снимает_вторую_копию_раньше_срока(вольт):
    (вольт / "Задачи").mkdir()
    dest = вольт / "копии"
    данные = cfg.defaults()
    данные["backup"]["frequency_hours"] = 24
    данные["backup"]["folder"] = str(dest)
    cfg.save(данные, cfg.settings_path(вольт))

    remind.автобэкап(date(2026, 8, 24))
    remind.автобэкап(date(2026, 8, 24))
    assert len(list(dest.glob("*.zip"))) == 1
