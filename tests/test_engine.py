#!/usr/bin/env python3
"""Тесты движка шагов.

Проверяем то, на чём держится стор: запись YAML без порчи, вычисление статуса
из шагов, отметки шагов и идемпотентность утреннего `refresh`. Код скоро уедет
на чужой ноутбук, где чинить придётся вслепую, поэтому тесты идут по инвариантам
из CLAUDE.md и PROTOCOL.md, а не по строчкам движка.

Изоляция. Путь к стору движок читает из `YUNGDRUNG_VAULT` один раз, на уровне
модуля (`VAULT`), — менять переменную окружения после импорта бесполезно.
Подменяем саму модульную переменную через monkeypatch (`db_path()` в engine.py
читает `VAULT` заново при каждом вызове ровно ради этого). Так тесты зовут
ровно те функции, что и CLI, проверяют возвращённый словарь напрямую, а при
падении показывают assert, а не разбор чужого stdout; pytest сам вернёт
переменную на место. Полный запуск через subprocess честнее к CLI, но платить
процессом за каждую проверку дорого, поэтому им закрыт только тот кусок, до
которого подмена не достаёт: чтение `YUNGDRUNG_VAULT` и связка argparse —
два теста в самом конце файла.

Хранилище задач — SQLite (`стор.db`), база знаний пока остаётся markdown
(`База/*.md`) — переезд на неё отдельным этапом. Реальный стор репозитория не
участвует: свой `стор.db` заводится заново в tmp_path на каждый тест, в обход
движка — `task()` пишет строки в БД напрямую тем же приёмом, что раньше был у
прямой записи YAML: код под тестом не должен готовить свои же данные.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backup  # noqa: E402
import engine  # noqa: E402
import kb  # noqa: E402
import settings as cfg  # noqa: E402
import store  # noqa: E402

TODAY = date(2026, 8, 15)
TOMORROW = date(2026, 8, 16)


# --- обвязка ---------------------------------------------------------------

class NoAliasDumper(yaml.SafeDumper):
    """Свой, а не engine.PlainDumper: исходные файлы для тестов (заметки базы
    знаний, всё ещё markdown) должны готовиться независимо от проверяемого
    кода, иначе тест на якоря проверял бы сам себя."""

    def ignore_aliases(self, data):
        return True


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Временный стор вместо репозитория.

    KB_DIR патчится отдельно от VAULT: без этого kb-команды читали бы «База»
    настоящего репозитория, а не тестовую папку. TASKS_DIR ушёл вместе с
    markdown-хранилищем задач — стор.db заводится в tmp_path сам, лениво,
    при первом обращении Store.
    """
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    monkeypatch.setattr(engine, "KB_DIR", tmp_path / "База")
    return tmp_path


def step(number, title, *, status="pending", control_date=None,
        completed_date=None, log=None, parent=None, mode=None):
    return {"id": number, "title": title, "status": status,
            "control_date": control_date, "completed_date": completed_date,
            "parent": parent, "mode": mode,
            "log": log if log is not None else []}


def task(vault, name, steps, *, body="Тело заметки, его пишет заказчик.\n", **fields):
    """Кладёт задачу прямо в стор.db, в обход движка. Сводку верхнего уровня
    (status/current_step/...) намеренно не пишем — в БД для неё нет колонок,
    её каждый раз считает движок заново, а тесты проверяют, что он это делает.

    Возвращает `store.TaskRef`, а не путь: `.stem` называется так же, как у
    Path, поэтому большинство мест ниже, где раньше был файл, не изменились.
    """
    created = fields.pop("created", date(2026, 8, 1))
    start_date = fields.pop("start_date", created)
    tags = fields.pop("tags", [])
    cancelled = fields.pop("cancelled", False)
    cancelled_reason = fields.pop("cancelled_reason", None)
    assert not fields, f"task(): неизвестные поля {list(fields)}"

    conn = sqlite3.connect(str(vault / "стор.db"))
    store.migrate_schema(conn)
    cur = conn.execute(
        "INSERT INTO tasks (title, schema, created, start_date, cancelled, "
        "cancelled_reason, body) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, 1, store._iso(created), store._iso(start_date),
         int(bool(cancelled)), cancelled_reason, body))
    task_id = cur.lastrowid
    for i, s in enumerate(steps):
        conn.execute(
            "INSERT INTO steps (task_id, step_id, position, title, status, "
            "start_date, control_date, completed_date, note, parent_id, mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, s["id"], i, s["title"], s.get("status", "pending"),
             store._iso(s.get("start_date")), store._iso(s.get("control_date")),
             store._iso(s.get("completed_date")), s.get("note"),
             s.get("parent"), s.get("mode")))
        for e in s.get("log") or []:
            conn.execute(
                "INSERT INTO step_log (task_id, step_id, date, event, reason, "
                "was, to_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, s["id"], store._iso(e.get("date")), e["event"],
                 e.get("reason"), store._iso(e.get("was")), store._iso(e.get("to"))))
    for name_ in tags:
        row = conn.execute("SELECT id FROM tags WHERE name=?", (name_,)).fetchone()
        tag_id = row[0] if row else conn.execute(
            "INSERT INTO tags (name, color, pinned) VALUES (?, '#999999', 0)",
            (name_,)).lastrowid
        conn.execute("INSERT OR IGNORE INTO task_tags (task_id, tag_id) VALUES (?, ?)",
                     (task_id, tag_id))
    conn.commit()
    conn.close()
    return store.TaskRef(task_id, name)


def run(command, today=TODAY, **fields):
    """Вызов команды движка без argparse."""
    fields.setdefault("force", False)
    fields.setdefault("reason", None)
    fields.setdefault("to", None)
    return command(SimpleNamespace(**fields), today)


def read(ref, today=TODAY):
    """Задача целиком, как раньше отдавал разбор файла: сырые поля из БД плюс
    сводка верхнего уровня, которую раньше вычисляла и писала save(), а
    теперь при чтении её просто негде взять — колонок нет.

    `today` нужен ровно там, где статус специально смотрят в другой день, чем
    TODAY (например про то, что статус «устаревает сам по себе»); везде
    остальном берётся дефолт.
    """
    found = engine._find_task_by_stem(ref.stem)
    assert found is not None, f"нет задачи «{ref.stem}»"
    meta = dict(found["meta"])
    meta.update(engine.task_summary(found, today))
    return meta, found["body"]


def snapshot_db(vault):
    """Содержимое всех задач и их шагов — для проверки, что второй прогон
    команды ничего не изменил. `step_log.id` из сравнения исключён: это
    autoincrement, а не часть данных, и он не обязан совпадать после
    пересохранения с тем же содержимым."""
    conn = sqlite3.connect(str(vault / "стор.db"))
    conn.row_factory = sqlite3.Row
    попытка = {}
    for row in conn.execute("SELECT * FROM tasks ORDER BY id"):
        задача = dict(row)
        задача["_steps"] = [dict(s) for s in conn.execute(
            "SELECT * FROM steps WHERE task_id=? ORDER BY position", (row["id"],))]
        задача["_log"] = [
            {k: v for k, v in dict(e).items() if k != "id"}
            for e in conn.execute(
                "SELECT * FROM step_log WHERE task_id=? ORDER BY step_id, date, event",
                (row["id"],))]
        попытка[row["title"]] = задача
    conn.close()
    return попытка


# --- 1. Круговой прогон запись-чтение ---------------------------------------

def test_round_trip_survives_second_pass(vault):
    """Читаем и пишем то же самое — содержимое БД должно стать прежним:
    те же шаги, те же события журнала (без учёта их autoincrement id,
    который меняется при любой перезаписи и данными не является)."""
    task(vault, "Замена подшипника", [
        step(1, "Заказать подшипник [[6805]]", status="done",
            control_date=date(2026, 7, 20), completed_date=date(2026, 7, 20),
            log=[{"date": date(2026, 7, 20), "event": "done"}]),
        step(2, "Снять колесо", control_date=date(2026, 8, 12)),
    ])
    run(engine.cmd_refresh)
    first = snapshot_db(vault)
    run(engine.cmd_refresh)
    assert snapshot_db(vault) == first


def test_customer_text_in_body_untouched(vault):
    """Заметка — поле заказчика, и ни одна команда над шагами не должна её
    трогать. Раньше это же проверялось через блок между маркерами, который
    движок сам вписывал в тело для Obsidian — без Obsidian вписывать в тело
    вообще нечего, и заметка теперь просто лежит колонкой рядом с шагами."""
    body = "Позвонить [[Василий Говнов]].\n\n- пункт\n- ещё пункт\n"
    path = task(vault, "Грант", [step(1, "Позвонить", control_date=TODAY)], body=body)
    run(engine.cmd_done, task="Грант", step="1")
    assert read(path)[1] == body


# --- 2. Якоря YAML ----------------------------------------------------------
#
# PlainDumper/write_file больше не пишет задачи — писали frontmatter-файл под
# Obsidian, теперь задачи в БД. Модуль остался (заметки базы знаний пока
# markdown, см. kb_note() ниже), тест на сам дампер — прямая проверка класса,
# без похода через сохранение задачи.

def test_custom_dumper_not_luck(vault):
    """Проверка, что предыдущий тест не пустой: обычный SafeDumper на тех же
    данных якоря как раз ставит, их убирает именно PlainDumper движка."""
    one_date = date(2026, 8, 15)
    meta = {"control_date": one_date,
            "steps": [{"id": 1, "control_date": one_date,
                       "log": [{"date": one_date, "event": "done"}]}]}

    standard = yaml.dump(meta, Dumper=yaml.SafeDumper, sort_keys=False)
    ours = yaml.dump(meta, Dumper=engine.PlainDumper, sort_keys=False)

    assert re.search(r"[&*]id\d+", standard), "SafeDumper перестал ставить якоря"
    assert re.search(r"[&*]id\d+", ours) is None


# --- 3. Даты — датами, не строками -----------------------------------------
#
# Раньше здесь же проверялось отсутствие кавычек вокруг даты в тексте YAML —
# у SQLite нет своего текстового формата, который можно случайно закавычить,
# поэтому эта часть инварианта закрыта самой схемой (колонка TEXT с ISO-строкой,
# store._parse_date_or_datetime всегда возвращает date/datetime, не str) и
# отдельной проверки не требует. Здесь — что круглый путь через БД не роняет
# тип даты до строки, тот самый инвариант, который раньше ловил `str(datetime)`
# с секундами (см. store._parse_date_or_datetime).

def test_dates_written_as_dates_not_strings(vault):
    path = task(vault, "Грант", [
        step(1, "Собрать", control_date=date(2026, 8, 10)),
        step(2, "Отправить"),
    ])
    run(engine.cmd_done, task="Грант", step="1")

    meta, _ = read(path)
    assert isinstance(meta["control_date"], date)
    assert isinstance(meta["steps"][0]["completed_date"], date)
    assert isinstance(meta["steps"][0]["log"][0]["date"], date)


def test_dates_as_dates_after_defer_too(vault):
    """Тот же инвариант на ветке defer: дату туда приносит аргумент-строка."""
    path = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    run(engine.cmd_defer, task="Грант", step="1", to="2026-08-20")

    meta, _ = read(path)
    assert meta["steps"][0]["control_date"] == date(2026, 8, 20)
    assert isinstance(meta["steps"][0]["log"][0]["to"], date)


# --- 4. done ---------------------------------------------------------------

def test_done_closes_step_and_sets_date(vault):
    path = task(vault, "Грант", [
        step(1, "Собрать", control_date=TODAY),
        step(2, "Отправить", control_date=date(2026, 8, 20)),
    ])
    result = run(engine.cmd_done, task="Грант", step="1", reason="всё собрал")

    assert result["ok"] and result["status"] == "done"
    assert result["next_step"] == "Отправить"

    meta, _ = read(path)
    first = meta["steps"][0]
    assert first["status"] == "done"
    assert first["completed_date"] == TODAY
    assert first["log"][-1] == {"date": TODAY, "event": "done", "reason": "всё собрал"}


def test_done_gives_next_step_todays_date(vault):
    """Шаг без даты не всплывёт ни в одной сборке — задача молча пропадёт."""
    path = task(vault, "Грант", [
        step(1, "Собрать", control_date=TODAY),
        step(2, "Отправить"),          # даты нет
        step(3, "Ждать решения"),
    ])
    result = run(engine.cmd_done, task="Грант", step="1")

    assert result["date_assigned_to_step"] == 2
    assert result["task_status"] == "due"
    meta, _ = read(path)
    assert meta["steps"][1]["control_date"] == TODAY
    assert meta["steps"][2]["control_date"] is None   # через один не заглядываем
    assert meta["control_date"] == TODAY
    assert meta["current_step"] == "Отправить"

    # задача осталась видимой в сборке
    assert [v["step"] for v in run(engine.cmd_next)["due"]] == [2]


def test_done_does_not_override_existing_date(vault):
    """Дату ставим только если её нет: назначенную заказчиком не двигаем."""
    path = task(vault, "Грант", [
        step(1, "Собрать", control_date=TODAY),
        step(2, "Отправить", control_date=date(2026, 9, 1)),
    ])
    result = run(engine.cmd_done, task="Грант", step="1")

    assert result["date_assigned_to_step"] is None
    assert read(path)[0]["steps"][1]["control_date"] == date(2026, 9, 1)


def test_done_on_last_step_closes_task(vault):
    path = task(vault, "Грант", [
        step(1, "Собрать", status="done", control_date=date(2026, 8, 1),
            completed_date=date(2026, 8, 1)),
        step(2, "Отправить", control_date=TODAY),
    ])
    result = run(engine.cmd_done, task="Грант", step="2")

    assert result["task_status"] == "done"
    assert result["next_step"] is None
    meta, _ = read(path)
    assert meta["status"] == "закрыта"
    assert meta["current_step"] is None
    assert meta["control_date"] is None
    assert meta["progress"] == "2/2"


# --- 5. notdone ------------------------------------------------------------

def test_notdone_leaves_step_open(vault):
    """Ключевое отличие от «сделан»: причина внешняя, решать всё равно надо."""
    path = task(vault, "Подшипник", [step(1, "Снять колесо", control_date=TODAY)])
    result = run(engine.cmd_notdone, task="Подшипник", step="1",
                    reason="внешние обстоятельства")

    assert result["status"] == "pending"
    assert result["next_check"] == "2026-08-16"
    assert result["stalled"] == 1
    assert result["hint"] is None

    meta, _ = read(path)
    step1 = meta["steps"][0]
    assert step1["status"] == "pending"
    assert step1.get("completed_date") is None
    assert step1["control_date"] == TOMORROW          # по умолчанию едет на завтра
    assert step1["log"][-1] == {"date": TODAY, "event": "not_done",
                               "reason": "внешние обстоятельства",
                               "was": TODAY, "to": TOMORROW}
    assert meta["status"] == "ждёт"


def test_notdone_accumulates_counter(vault):
    """Каждая отметка ложится в log, счётчик считается по нему, а не хранится."""
    path = task(vault, "Подшипник", [step(1, "Снять колесо", control_date=TODAY)])

    run(engine.cmd_notdone, today=date(2026, 8, 15), task="Подшипник",
              step="1", reason="не было времени")
    run(engine.cmd_notdone, today=date(2026, 8, 16), task="Подшипник",
              step="1", reason="жду ответа от другого человека")

    meta, _ = read(path)
    assert len(meta["steps"][0]["log"]) == 2
    assert [e["reason"] for e in meta["steps"][0]["log"]] == [
        "не было времени", "жду ответа от другого человека"]
    assert meta["stalled"] == 2
    assert engine.stall_count(meta["steps"][0]) == 2


def test_notdone_respects_explicit_date(vault):
    task(vault, "Подшипник", [step(1, "Снять колесо", control_date=TODAY)])
    result = run(engine.cmd_notdone, task="Подшипник", step="1", to="2026-09-01")
    assert result["next_check"] == "2026-09-01"


# --- 6. Буксование ---------------------------------------------------------

def test_three_marks_in_a_row_is_stalling(vault):
    """Четвёртый перенос подряд означает, что нужен другой ход, а не новая дата."""
    path = task(vault, "Подшипник", [
        step(1, "Снять колесо", control_date=TODAY, log=[
            {"date": date(2026, 8, 1), "event": "not_done", "reason": "не было времени"},
            {"date": date(2026, 8, 8), "event": "not_done", "reason": "жду ответа от другого человека"},
        ]),
    ])
    result = run(engine.cmd_notdone, task="Подшипник", step="1",
                    reason="внешние обстоятельства")

    assert result["stalled"] == 3
    assert result["hint"] == "шаг буксует, нужен другой ход"
    assert read(path)[0]["stalled"] == 3

    # в сборке следующего дня шаг попадает в отдельный список
    build = run(engine.cmd_next, today=TOMORROW)
    assert [v["task"] for v in build["stalled"]] == ["Подшипник"]
    assert build["stalled"][0]["last_reason"] == "внешние обстоятельства"


def test_two_marks_not_yet_stalling(vault):
    task(vault, "Подшипник", [
        step(1, "Снять колесо", control_date=TODAY, log=[
            {"date": date(2026, 8, 1), "event": "not_done", "reason": "некогда"},
        ]),
    ])
    result = run(engine.cmd_notdone, task="Подшипник", step="1")
    assert result["stalled"] == 2 and result["hint"] is None
    assert run(engine.cmd_next, today=TOMORROW)["stalled"] == []


def test_defer_does_not_count_as_stalling(vault):
    """`defer` выбирает заказчик — это не то же, что «опять не сделал»."""
    task(vault, "Грант", [
        step(1, "Собрать", control_date=TODAY, log=[
            {"date": date(2026, 8, 1), "event": "defer", "to": date(2026, 8, 8)},
            {"date": date(2026, 8, 8), "event": "defer", "to": date(2026, 8, 12)},
            {"date": date(2026, 8, 12), "event": "defer", "to": TODAY},
        ]),
    ])
    assert run(engine.cmd_next)["stalled"] == []
    assert run(engine.cmd_next)["due"][0]["stalled"] == 0


# --- 7. defer --------------------------------------------------------------

def test_defer_sets_given_date(vault):
    path = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    result = run(engine.cmd_defer, task="Грант", step="1",
                    to="2026-08-20", reason="внешние обстоятельства")

    assert result["next_check"] == "2026-08-20"
    meta, _ = read(path)
    assert meta["steps"][0]["control_date"] == date(2026, 8, 20)   # не завтра
    assert meta["steps"][0]["control_date"] != TOMORROW
    assert meta["steps"][0]["status"] == "pending"
    assert meta["steps"][0]["log"][-1] == {
        "date": TODAY, "event": "defer", "reason": "внешние обстоятельства",
        "was": TODAY, "to": date(2026, 8, 20)}
    assert meta["status"] == "ждёт"
    assert meta["control_date"] == date(2026, 8, 20)


def test_defer_can_go_backwards(vault):
    """Перенести можно и на более раннюю дату — движок дат не оценивает."""
    path = task(vault, "Грант", [step(1, "Собрать", control_date=date(2026, 9, 1))])
    run(engine.cmd_defer, task="Грант", step="1", to="2026-08-10")
    meta, _ = read(path)
    assert meta["steps"][0]["control_date"] == date(2026, 8, 10)
    assert meta["status"] == "просрочена"


# --- 7b. Массовые действия в разборе завала (R20) ---------------------------

def test_bulk_defer_increases_counter_and_writes_distinct_log_entry(vault):
    """R20 ТЗ: массовый перенос — не то же самое, что одиночный. Счётчик у
    каждого элемента растёт, и в журнале остаётся запись, отличимая от
    обычного `defer` (regression-страховка на неё — test_defer_does_not_count_as_stalling)."""
    a = task(vault, "Грант", [step(1, "Собрать", control_date=date(2026, 8, 10))])
    b = task(vault, "Договор", [step(1, "Подписать", control_date=date(2026, 8, 12))])

    result = run(engine.cmd_backlog_bulk, op="defer",
                items=[{"task": "Грант", "step": "1"}, {"task": "Договор", "step": "1"}],
                reason="не было времени", to="2026-08-25")

    assert result["ok_count"] == 2 and result["fail_count"] == 0
    for item in result["items"]:
        assert item["ok"] is True
        assert item["next_check"] == "2026-08-25"
        assert item["stalled"] == 1

    for ref in (a, b):
        meta, _ = read(ref)
        step1 = meta["steps"][0]
        assert step1["control_date"] == date(2026, 8, 25)
        entry = step1["log"][-1]
        assert entry["event"] == "mass_defer"          # не "defer" — отличимо
        assert entry["reason"] == "не было времени"
        assert entry["to"] == date(2026, 8, 25)
        assert engine.stall_count(step1) == 1


def test_bulk_done_closes_steps_across_tasks_and_opens_next(vault):
    """Массовое «сделано» работает по разным задачам разом и открывает
    следующий шаг там, где он есть — та же механика, что у одиночного done."""
    a = task(vault, "Грант", [
        step(1, "Собрать", control_date=date(2026, 8, 10)),
        step(2, "Отправить"),
    ])
    b = task(vault, "Договор", [step(1, "Подписать", control_date=date(2026, 8, 12))])

    result = run(engine.cmd_backlog_bulk, op="done",
                items=[{"task": "Грант", "step": 1}, {"task": "Договор", "step": 1}])

    assert result["ok_count"] == 2 and result["fail_count"] == 0

    meta_a, _ = read(a)
    assert meta_a["steps"][0]["status"] == "done"
    assert meta_a["steps"][1]["control_date"] == TODAY     # следующий шаг открылся
    assert meta_a["status"] == "сегодня"

    meta_b, _ = read(b)
    assert meta_b["steps"][0]["status"] == "done"
    assert meta_b["status"] == "закрыта"                   # был последним шагом


def test_bulk_fail_marks_step_failed_and_opens_next(vault):
    a = task(vault, "Грант", [
        step(1, "Собрать", control_date=date(2026, 8, 10)),
        step(2, "Отправить"),
    ])
    result = run(engine.cmd_backlog_bulk, op="fail",
                items=[{"task": "Грант", "step": "1"}], reason="внешние обстоятельства")

    assert result["ok_count"] == 1
    meta, _ = read(a)
    assert meta["steps"][0]["status"] == "failed"
    assert meta["steps"][0]["log"][-1] == {"date": TODAY, "event": "failed",
                                           "reason": "внешние обстоятельства"}
    assert meta["steps"][1]["control_date"] == TODAY


def test_bulk_batch_with_one_closed_step_does_not_abort_others(vault):
    """Один плохой элемент (шаг уже закрыт кем-то другим за это время) не
    роняет пачку — остальные элементы обрабатываются, ошибка структурная."""
    a = task(vault, "Грант", [step(1, "Собрать", status="done",
                                   completed_date=date(2026, 8, 1))])
    b = task(vault, "Договор", [step(1, "Подписать", control_date=date(2026, 8, 12))])

    result = run(engine.cmd_backlog_bulk, op="done",
                items=[{"task": "Грант", "step": "1"}, {"task": "Договор", "step": "1"}])

    assert result["ok_count"] == 1 and result["fail_count"] == 1
    bad, good = result["items"]
    assert bad["ok"] is False
    assert bad["errors"][0] == {"field": None, "error": "шаг 1 уже done"}
    assert good["ok"] is True and good["task"] == "Договор"

    meta_b, _ = read(b)
    assert meta_b["steps"][0]["status"] == "done"           # второй всё же прошёл


def test_bulk_item_missing_fields_becomes_structural_error(vault):
    task(vault, "Грант", [step(1, "Собрать", control_date=date(2026, 8, 10))])
    result = run(engine.cmd_backlog_bulk, op="done",
                items=[{"task": "", "step": ""}, {"task": "Грант", "step": "1"}])

    assert result["ok_count"] == 1 and result["fail_count"] == 1
    assert result["items"][0]["ok"] is False
    assert result["items"][0]["errors"][0]["field"] is None


def test_bulk_defer_requires_reason_and_date(vault):
    """Причина обязательна для переноса, как и у одиночного defer; дата — тоже,
    только у массового переноса это проверяется до похода по элементам."""
    task(vault, "Грант", [step(1, "Собрать", control_date=date(2026, 8, 10))])

    без_причины = run(engine.cmd_backlog_bulk, op="defer",
                      items=[{"task": "Грант", "step": "1"}], to="2026-08-20")
    assert без_причины["ok"] is False
    assert без_причины["errors"][0]["field"] == "reason"

    без_даты = run(engine.cmd_backlog_bulk, op="defer",
                   items=[{"task": "Грант", "step": "1"}], reason="не было времени")
    assert без_даты["ok"] is False
    assert без_даты["errors"][0]["field"] == "to"


# --- 8. Статус задачи считается из шагов ------------------------------------

def status_set(vault):
    task(vault, "Просрочена", [step(1, "Шаг", control_date=date(2026, 8, 10))])
    task(vault, "Сегодня", [step(1, "Шаг", control_date=TODAY)])
    task(vault, "Ждёт", [step(1, "Шаг", control_date=date(2026, 8, 20))])
    task(vault, "Без даты", [step(1, "Шаг")])
    task(vault, "Закрыта", [
        step(1, "Раз", status="done", control_date=date(2026, 8, 1),
            completed_date=date(2026, 8, 1)),
        step(2, "Два", status="skipped", control_date=date(2026, 8, 2)),
    ])
    task(vault, "Пустая", [])


def test_status_in_json_is_english(vault):
    """JSON наружу — интерфейс для бота, ключи английские."""
    status_set(vault)
    statuses = {t["task"]: t["status"] for t in run(engine.cmd_list)["tasks"]}
    assert statuses == {
        "Просрочена": "overdue", "Сегодня": "due", "Ждёт": "waiting",
        "Без даты": "no_date", "Закрыта": "done", "Пустая": "empty",
    }


def test_status_in_file_is_russian(vault):
    """Статус в meta — по-русски: раньше это был текст в файле, который читает
    заказчик напрямую, теперь то же самое поле в ответе движка."""
    status_set(vault)
    statuses = {t["path"].stem: engine.task_summary(t, TODAY)["status"]
               for t in engine.load_tasks()}
    assert statuses == {
        "Просрочена": "просрочена", "Сегодня": "сегодня", "Ждёт": "ждёт",
        "Без даты": "без даты", "Закрыта": "закрыта", "Пустая": "нет шагов",
    }


def test_skipped_step_counts_as_closed(vault):
    """Задача закрыта, если все шаги done или skipped — не только done."""
    path = task(vault, "Грант", [
        step(1, "Раз", status="done", completed_date=date(2026, 8, 1)),
        step(2, "Два", control_date=TODAY),
    ])
    result = run(engine.cmd_skip, task="Грант", step="2", reason="пошли другим путём")
    assert result["task_status"] == "done"
    meta, _ = read(path)
    assert meta["status"] == "закрыта" and meta["progress"] == "2/2"


def test_current_step_is_first_open_one(vault):
    task(vault, "Грант", [
        step(1, "Раз", status="done", completed_date=date(2026, 8, 1)),
        step(2, "Два", status="skipped"),
        step(3, "Три", control_date=date(2026, 8, 20)),
        step(4, "Четыре", control_date=date(2026, 8, 10)),
    ])
    tasks = run(engine.cmd_list)["tasks"]
    assert tasks[0]["current"] == "Три"          # дата четвёртого на статус не влияет
    assert tasks[0]["status"] == "waiting"
    assert tasks[0]["steps_done"] == 2 and tasks[0]["steps_total"] == 4


def test_list_status_filter_is_computed_by_engine(vault):
    """Issue #4: бакет «ждут» был виден только числом в счётчике ленты. Фильтр
    в `cmd_list` — тот же ключ, что уже даёт `task_status`, оболочка ничего не
    пересчитывает, только просит нужный срез."""
    status_set(vault)
    ждут = run(engine.cmd_list, status="waiting")["tasks"]
    assert [t["task"] for t in ждут] == ["Ждёт"]

    без_даты = run(engine.cmd_list, status="no_date")["tasks"]
    assert [t["task"] for t in без_даты] == ["Без даты"]

    просрочены = run(engine.cmd_list, status="overdue")["tasks"]
    assert [t["task"] for t in просрочены] == ["Просрочена"]

    без_фильтра = run(engine.cmd_list, status=None)["tasks"]
    assert len(без_фильтра) == 6


def test_list_carries_control_date_and_russian_status_text(vault):
    """Карточка списка не должна сама переводить статус или решать, есть ли
    дата — оба поля уже готовы у ядра (see CONTRACT.md)."""
    status_set(vault)
    by_task = {t["task"]: t for t in run(engine.cmd_list)["tasks"]}
    assert by_task["Ждёт"]["control_date"] == "2026-08-20"
    assert by_task["Ждёт"]["status_text"] == "ждёт"
    assert by_task["Без даты"]["control_date"] is None
    assert by_task["Без даты"]["status_text"] == "без даты"
    assert by_task["Без даты"]["status"] == "no_date"  # не путается с "waiting"


def test_list_sorted_by_control_date_dateless_last(vault):
    """Минимальная планка issue #4: «ждёт неделю» и «нет даты вовсе» — разные
    ситуации, дальний срок должен идти раньше пустой даты, а не вперемешку."""
    task(vault, "Через неделю", [step(1, "Шаг", control_date=date(2026, 8, 22))])
    task(vault, "Завтра", [step(1, "Шаг", control_date=date(2026, 8, 16))])
    task(vault, "Совсем без даты", [step(1, "Шаг")])
    tasks = run(engine.cmd_list)["tasks"]
    assert [t["task"] for t in tasks] == ["Завтра", "Через неделю", "Совсем без даты"]


def test_build_includes_only_what_needs_attention(vault):
    """`waiting` и `done` в утренней сборке не нужны, `no_date` — нужен."""
    status_set(vault)
    build = run(engine.cmd_next)
    assert [v["task"] for v in build["due"]] == ["Просрочена", "Без даты", "Сегодня"]
    assert build["due"][0]["overdue_days"] == 5


# --- 9. refresh ------------------------------------------------------------
#
# Раньше второй прогон в тот же день не трогал ни одного файла: сводка
# сравнивалась с тем, что уже лежало на диске (экономило запись и не
# заставляло Obsidian переиндексировать стор впустую). В БД сравнивать не с
# чем — сводка нигде не хранится, поэтому refresh честно пересчитывает и
# отдаёт всё заново при каждом вызове; см. docstring cmd_refresh в engine.py.

def test_refresh_repeated_call_keeps_data_identical(vault):
    """Второй прогон подряд отчитывается по всем задачам (сравнивать не с
    чем), но содержимое БД от этого не меняется."""
    status_set(vault)

    first = run(engine.cmd_refresh)
    assert first["count"] == 6
    assert all(t["changed"] for t in first["written"])

    before = snapshot_db(vault)
    second = run(engine.cmd_refresh)

    assert second["count"] == 6
    assert snapshot_db(vault) == before


def test_refresh_force_rewrites_everything(vault):
    """`--force` раньше отличался от обычного прогона тем, что переписывал
    файлы, даже когда сводка не поменялась. В БД оба прогона и так переписывают
    всё каждый раз — `force` остался в контракте ответа ради обратной
    совместимости, а не потому что меняет запись."""
    status_set(vault)
    run(engine.cmd_refresh)
    before = snapshot_db(vault)

    result = run(engine.cmd_refresh, force=True)

    assert result["forced"] is True and result["count"] == 6
    assert snapshot_db(vault) == before


def test_refresh_idempotent_with_control_time_too(vault):
    """Свойство «дата и время» приходит как datetime, а не голая дата —
    повторный прогон не должен терять время суток."""
    path = task(vault, "Грант", [
        step(1, "Созвон", control_date=datetime(2026, 8, 20, 10, 30)),
    ])
    assert run(engine.cmd_refresh)["count"] == 1
    assert read(path)[0]["status"] == "ждёт"
    assert isinstance(read(path)[0]["steps"][0]["control_date"], datetime)

    before = snapshot_db(vault)
    assert run(engine.cmd_refresh)["count"] == 1
    assert snapshot_db(vault) == before


def test_refresh_sets_summary_from_scratch(vault):
    """Файл, заведённый руками без сводки, после refresh пригоден для Bases."""
    path = task(vault, "Грант", [
        step(1, "Раз", status="done", completed_date=date(2026, 8, 1)),
        step(2, "Два", control_date=date(2026, 8, 10), log=[
            {"date": date(2026, 8, 5), "event": "not_done", "reason": "некогда"}]),
    ])
    run(engine.cmd_refresh)
    meta, _ = read(path)
    assert meta["schema"] == 1
    assert meta["status"] == "просрочена"
    assert meta["current_step"] == "Два"
    assert meta["control_date"] == date(2026, 8, 10)
    assert meta["progress"] == "1/2"
    assert meta["stalled"] == 1


# --- 10. Статус устаревает сам по себе -------------------------------------

def test_status_goes_stale_as_day_passes(vault):
    """Тот же стор, другой день — другой статус. Без этого утренняя сборка
    показывала бы вчерашнюю картину."""
    path = task(vault, "Грант", [step(1, "Собрать", control_date=date(2026, 8, 20))])

    run(engine.cmd_refresh, today=date(2026, 8, 15))
    assert read(path, today=date(2026, 8, 15))[0]["status"] == "ждёт"
    assert run(engine.cmd_next, today=date(2026, 8, 15))["due"] == []

    result = run(engine.cmd_refresh, today=date(2026, 8, 20))
    assert result["count"] == 1
    assert read(path, today=date(2026, 8, 20))[0]["status"] == "сегодня"

    run(engine.cmd_refresh, today=date(2026, 8, 25))
    assert read(path, today=date(2026, 8, 25))[0]["status"] == "просрочена"
    build = run(engine.cmd_next, today=date(2026, 8, 25))
    assert build["due"][0]["overdue_days"] == 5


# --- 11. Свои поля не теряются ----------------------------------------------
#
# Раньше это был вопрос порядка полей в YAML под редактор свойств Obsidian —
# без Obsidian порядок ничего не значит. Инвариант, который остался: то, чего
# движок не знает (frontmatter, который заказчик добавил руками), не должно
# потеряться при следующей записи. Миграция кладёт такие поля в колонку
# `extra`; здесь проверяется именно этот путь, а не то, что кладёт туда сама
# миграция (см. migrate_to_sqlite.py и его тесты).

def test_unknown_meta_field_survives_save(vault):
    conn = sqlite3.connect(str(vault / "стор.db"))
    store.migrate_schema(conn)
    conn.execute(
        "INSERT INTO tasks (title, schema, created, start_date, extra) "
        "VALUES (?, 1, ?, ?, ?)",
        ("Грант", store._iso(date(2026, 8, 1)), store._iso(date(2026, 8, 1)),
         json.dumps({"приоритет": "высокий"})))
    conn.execute(
        "INSERT INTO steps (task_id, step_id, position, title, control_date) "
        "VALUES (last_insert_rowid(), 1, 0, 'Собрать', ?)", (store._iso(TODAY),))
    conn.commit()
    conn.close()

    run(engine.cmd_refresh, force=True)
    meta = read(store.TaskRef(1, "Грант"))[0]
    assert meta["приоритет"] == "высокий"


# --- Ошибки ----------------------------------------------------------------

def test_nonexistent_task(vault):
    task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    with pytest.raises(SystemExit) as e:
        run(engine.cmd_show, task="подшипник")
    assert "нет задачи" in str(e.value)


def test_ambiguous_name_fragment(vault):
    task(vault, "Заявка на грант ФПГ", [step(1, "Собрать", control_date=TODAY)])
    task(vault, "Заявка на субсидию", [step(1, "Собрать", control_date=TODAY)])

    with pytest.raises(SystemExit) as e:
        run(engine.cmd_show, task="заявка")
    assert "подходит несколько" in str(e.value)
    assert "Заявка на грант ФПГ" in str(e.value)

    # однозначный фрагмент находится, регистр не важен
    assert run(engine.cmd_show, task="ГРАНТ")["task"] == "Заявка на грант ФПГ"


def test_nonexistent_step(vault):
    task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    with pytest.raises(SystemExit) as e:
        run(engine.cmd_done, task="Грант", step="7")
    assert "нет шага 7" in str(e.value)


@pytest.mark.parametrize("command", ["cmd_done", "cmd_notdone", "cmd_defer"])
def test_closed_step_not_touched_again(vault, command):
    """Иначе отметка задним числом перепишет дату выполнения и сломает историю."""
    path = task(vault, "Грант", [
        step(1, "Собрать", status="done", control_date=date(2026, 8, 1),
            completed_date=date(2026, 8, 1),
            log=[{"date": date(2026, 8, 1), "event": "done"}]),
        step(2, "Отправить", control_date=TODAY),
    ])
    before = read(path)

    with pytest.raises(SystemExit) as e:
        run(getattr(engine, command), task="Грант", step="1", to="2026-08-20")
    assert "уже done" in str(e.value)
    assert read(path) == before      # ничего не записано


def test_concurrent_done_and_notdone_loser_gets_honest_error(vault, monkeypatch):
    """Issue #11: два потока отмечают один и тот же открытый шаг одновременно —
    два окна браузера или двойной клик до того, как кнопка успела задизейблиться
    (server.py — ThreadingHTTPServer, см. его заголовок). Раньше оба читали
    статус до того, как другой успевал записать, оба проходили проверку `!=
    OPEN` и оба репортовали `ok: true` — при этом эффекты проигравшего (счётчик
    буксования, своя запись в журнале) пропадали без следа, а кто именно
    победил, решал просто порядок записи.

    Настоящая гонка, не последовательные вызовы — но и не голый `Barrier`: если
    просто отпустить оба потока разом, GIL почти всегда успевает прогнать
    `notdone` целиком (чтение-проверка-запись-коммит) раньше, чем `done` вообще
    дойдёт до чтения — тогда `done` честно прочитает уже обновлённый шаг и
    оба легитимно завершатся `ok` без всякой гонки. Чтобы воспроизводить
    именно гонку, а не эту благополучную последовательность, `store.save_task`
    патчится так, что поток `done` придерживается ровно между чтением шага и
    записью — окно, в котором `notdone` должен успеть целиком прочитать,
    проверить и записать свою версию. Это то самое окно из issue #11: `done`
    уже прочитал шаг открытым до того, как `notdone` его тронул, и обязан
    получить конфликт, а не молча переписать шаг своей устаревшей копией.
    """
    имя = "Тест гонки"
    ref = task(vault, имя, [step(1, "Собрать", control_date=TODAY)])
    # Прогрев: первое подключение к свежему файлу переключает journal_mode на
    # WAL — отдельная, не относящаяся к делу гонка (сама смена режима требует
    # эксклюзивного лока). Прогоняем её один раз вне потоков.
    engine.load_tasks()

    done_прочитал = threading.Event()
    notdone_записал = threading.Event()
    настоящий_save_task = store.Store.save_task

    def задержанный_save_task(self, task, today, expected_step=None):
        # Именно здесь, а не раньше: к этому моменту cmd_done уже прочитал шаг
        # и посчитал expected_step в Python — ровно момент, который в issue
        # был уязвим, потому что до записи никто соединение не держал.
        if threading.current_thread().name == "поток-done":
            done_прочитал.set()
            assert notdone_записал.wait(timeout=5), "notdone не отозвался — дедлок в тесте"
        return настоящий_save_task(self, task, today, expected_step=expected_step)

    monkeypatch.setattr(store.Store, "save_task", задержанный_save_task)

    results = {}

    def запустить_done():
        try:
            results["done"] = ("ok", run(engine.cmd_done, task=имя, step="1"))
        except SystemExit as e:
            results["done"] = ("error", str(e))

    def запустить_notdone():
        assert done_прочитал.wait(timeout=5), "done не отозвался — дедлок в тесте"
        try:
            results["notdone"] = ("ok", run(engine.cmd_notdone, task=имя, step="1",
                                            reason="внешние обстоятельства"))
        finally:
            notdone_записал.set()

    t1 = threading.Thread(target=запустить_done, name="поток-done")
    t2 = threading.Thread(target=запустить_notdone, name="поток-notdone")
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # notdone читал и писал в чистом окне (done ещё не решился к тому
    # моменту) — обязан пройти без сучка.
    assert results["notdone"][0] == "ok"
    # done прочитал шаг открытым, но пока он спал, notdone его уже поменял —
    # обязан получить честную ошибку, а не ok:true поверх устаревшей копии.
    assert results["done"][0] == "error"
    assert "уже" in results["done"][1]

    meta, _ = read(ref)
    шаг = meta["steps"][0]
    # В базе стоит ровно то, что записал победитель — ни полу-состояния,
    # ни следов проигравшего done (который заново открыл бы шаг, поставил
    # completed_date и своё событие "done" в журнал).
    assert шаг["status"] == "pending"
    assert шаг["completed_date"] is None
    assert шаг["control_date"] == TODAY + timedelta(days=1)
    assert [e["event"] for e in шаг["log"]] == ["not_done"]


# Раньше здесь стояли пять тестов на BROKEN/KB_BROKEN — сборка не падает
# целиком от одного файла с битым YAML, ошибка видна в JSON-ответе, а не
# только в stderr. Для задач это устройство хранилища и было заплаткой на
# то, что стор правится руками мимо движка: SQLite такого файла не пропустит
# мимо INSERT вовсе, разбираться после записи ужe не с чем. Список остаётся в
# ответе — пустым — ради стабильности формы; следующий тест это фиксирует.

def test_broken_list_stays_in_response_shape_but_empty(vault):
    task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    for command in (engine.cmd_list, engine.cmd_next, engine.cmd_feed,
                     engine.cmd_backlog, engine.cmd_refresh):
        assert run(command)["broken"] == []


def test_empty_vault(vault):
    assert run(engine.cmd_list) == {"today": "2026-08-15", "tasks": [],
                                          "broken": []}
    assert run(engine.cmd_next)["due"] == []
    assert run(engine.cmd_refresh)["count"] == 0


# --- Починенные баги --------------------------------------------------------
#
# Оба нашлись при написании тестов и были исправлены здесь же. Тесты остаются
# регрессионными: они описывают ровно то поведение, которого раньше не было.

def test_unknown_step_status_does_not_break_build(vault):
    """Стор правится руками, и `status: сделан` вместо `done` — вопрос времени.

    Раньше это валило list/next/refresh/export целиком: «закрыт» и «открыт»
    проверялись разными правилами и на незнакомом статусе расходились.
    """
    task(vault, "Кривая", [step(1, "Шаг", status="сделан",
                                 control_date=date(2026, 8, 10))])
    task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    tasks = run(engine.cmd_list)["tasks"]
    assert "Грант" in [t["task"] for t in tasks]


def test_cannot_skip_closed_step(vault):
    """Снятие закрытого шага оставляло бы completed_date и событие «сделан»
    рядом со статусом «снят» — запись, противоречащая сама себе."""
    task(vault, "Грант", [
        step(1, "Собрать", status="done", control_date=date(2026, 8, 1),
            completed_date=date(2026, 8, 1),
            log=[{"date": date(2026, 8, 1), "event": "done"}]),
    ])
    with pytest.raises(SystemExit):
        run(engine.cmd_skip, task="Грант", step="1")


# --- CLI как его увидит чужой ноутбук ---------------------------------------
#
# Отсюда и до конца — настоящий процесс: только так проверяется, что путь к
# стору вообще берётся из YUNGDRUNG_VAULT и что argparse связан с командами.
# Подмена модульных переменных этот кусок обходит стороной.

def run_cli(vault, *args):
    """Движок всегда печатает UTF-8 — значит и читать его надо как UTF-8.

    Без явного encoding subprocess декодирует вывод в кодировке локали: на маке
    это UTF-8 и всё сходится случайно, а на Windows — cp1251, и «Грант»
    превращается в «Ð“Ñ€Ð°Ð½Ñ‚». Поймано настоящим прогоном на машине заказчика.
    То же правило действует для любого, кто вызывает движок и разбирает JSON.
    """
    result = subprocess.run(
        [sys.executable, str(ROOT / "engine.py"), *args],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "YUNGDRUNG_VAULT": str(vault)},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_cli_reads_env_var(tmp_path):
    task(tmp_path, "Грант", [step(1, "Собрать", control_date=TODAY)])

    result = run_cli(tmp_path, "--today", "2026-08-15", "list")
    assert [t["task"] for t in result["tasks"]] == ["Грант"]
    assert result["tasks"][0]["status"] == "due"
    # реальный стор репозитория при этом не читается
    assert "Заявка на грант ФПГ" not in [t["task"] for t in result["tasks"]]


def _real_repo_db_bytes():
    """Байты настоящего стор.db репозитория, если он уже мигрирован — или
    None, пока миграция (этап d) не прошла. Оба случая проверяют одно и то
    же: CLI с другим YUNGDRUNG_VAULT не должен коснуться этого файла."""
    настоящий = ROOT / "стор.db"
    return настоящий.read_bytes() if настоящий.is_file() else None


def test_cli_writes_to_its_own_vault(tmp_path):
    task(tmp_path, "Грант", [
        step(1, "Собрать", control_date=TODAY),
        step(2, "Отправить"),
    ])
    repo_before = _real_repo_db_bytes()

    result = run_cli(tmp_path, "--today", "2026-08-15", "done", "грант", "1",
                 "--reason", "сдал")
    assert result["ok"] and result["date_assigned_to_step"] == 2

    # Читаем сырым sqlite3, не через engine._find_task_by_stem: этот тест не
    # использует фикстуру `vault`, `engine.VAULT` в текущем процессе на
    # tmp_path не подменён нарочно — весь смысл теста в том, что запись
    # виден через переменную окружения субпроцесса, а не через monkeypatch.
    conn = sqlite3.connect(str(tmp_path / "стор.db"))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT id FROM tasks WHERE title='Грант'").fetchone()
    steps = {s["step_id"]: s for s in conn.execute(
        "SELECT * FROM steps WHERE task_id=?", (row["id"],))}
    conn.close()

    assert steps[1]["status"] == "done"
    assert steps[2]["status"] == "pending"
    assert _real_repo_db_bytes() == repo_before, "движок тронул настоящий стор.db"


# --- разбор человеческой даты ----------------------------------------------

# Разбор живёт в движке, а не в браузере: форма и CLI обязаны понимать дату
# одинаково, иначе через форму заведётся задача, которую движок не прочитает.

@pytest.mark.parametrize("текст, ожидаем", [
    ("", None),
    ("сегодня", date(2026, 8, 17)),
    ("завтра", date(2026, 8, 18)),
    ("послезавтра", date(2026, 8, 19)),
    ("+3", date(2026, 8, 20)),
    ("+14", date(2026, 8, 31)),
    ("вт", date(2026, 8, 18)),
    ("пятница", date(2026, 8, 21)),
    ("18.08", date(2026, 8, 18)),
    ("18.08.2026", date(2026, 8, 18)),
    ("18.08.26", date(2026, 8, 18)),
    ("2026-08-18", date(2026, 8, 18)),
    ("  Завтра  ", date(2026, 8, 18)),
])
def test_разбор_даты(текст, ожидаем):
    assert engine.parse_date_input(текст, date(2026, 8, 17)) == ожидаем


def test_день_недели_совпал_с_сегодня_даёт_следующую():
    """17.08.2026 — понедельник. «пн» должен дать следующий понедельник:
    иначе «пн» в понедельник молча означал бы «прямо сейчас»."""
    assert engine.parse_date_input("пн", date(2026, 8, 17)) == date(2026, 8, 24)


def test_день_и_месяц_без_года_не_уезжают_в_прошлое():
    """«18.08» в сентябре — это август следующего года, а не позади."""
    assert engine.parse_date_input("18.08", date(2026, 9, 1)) == date(2027, 8, 18)


@pytest.mark.parametrize("мусор", ["вчера", "abc", "32.13", "18.08.2026 и ещё", "++5"])
def test_мусорная_дата_отвергается(мусор):
    with pytest.raises((ValueError, TypeError)):
        engine.parse_date_input(мусор, date(2026, 8, 17))


# --- предлоги и «через N дней»: без модели диапазона все схлопываются в одну
# точку времени, потому что у шага одна дата контроля, а не начало и конец. ---

@pytest.mark.parametrize("текст, ожидаем", [
    ("через 3 дня", date(2026, 8, 20)),
    ("через 1 день", date(2026, 8, 18)),
    ("через 5 дней", date(2026, 8, 22)),
    ("через 3 дня 18:00", datetime(2026, 8, 20, 18, 0)),
    ("до 18:00", datetime(2026, 8, 17, 18, 0)),
    ("после 14", datetime(2026, 8, 17, 14, 0)),
    ("к 18", datetime(2026, 8, 17, 18, 0)),
    ("до завтра 14:00", datetime(2026, 8, 18, 14, 0)),
    ("после 14.08", date(2027, 8, 14)),  # предлог снят, дальше обычный разбор даты
    ("9", datetime(2026, 8, 17, 9, 0)),
    ("0", datetime(2026, 8, 17, 0, 0)),
])
def test_предлоги_и_голый_час(текст, ожидаем):
    assert engine.parse_date_input(текст, date(2026, 8, 17)) == ожидаем


@pytest.mark.parametrize("мусор", ["24", "через дня", "через 3"])
def test_предлоги_мусор_отвергается(мусор):
    """«через 3» без слова «дня» — не число дней, а недописанная мысль; «24» —
    часа с таким номером не бывает. Оба должны отказать, а не угадать смысл."""
    with pytest.raises((ValueError, TypeError)):
        engine.parse_date_input(мусор, date(2026, 8, 17))


# --- предлог «в» между датой и временем: как человек и пишет ----------------
#
# 17.08.2026 — понедельник.

@pytest.mark.parametrize("текст, ожидаем", [
    ("завтра в 9-30", datetime(2026, 8, 18, 9, 30)),
    ("завтра в 9:30", datetime(2026, 8, 18, 9, 30)),
    ("завтра в 9.30", datetime(2026, 8, 18, 9, 30)),
    ("завтра в 9", datetime(2026, 8, 18, 9, 0)),
    ("в 9-30", datetime(2026, 8, 17, 9, 30)),
    ("сегодня в 18", datetime(2026, 8, 17, 18, 0)),
    ("18.08 в 9-30", datetime(2026, 8, 18, 9, 30)),
    ("пн в 10:00", datetime(2026, 8, 24, 10, 0)),
    ("через 3 дня в 9-30", datetime(2026, 8, 20, 9, 30)),
    ("на 18.08", date(2026, 8, 18)),
    ("до завтра в 14:00", datetime(2026, 8, 18, 14, 0)),
])
def test_предлог_в_не_ломает_разбор(текст, ожидаем):
    """Живой отзыв заказчика: «завтра в 9-30 вообще не распознаётся». Слово «в»
    роняло разбор целиком, хотя и дата, и время в строке написаны."""
    assert engine.parse_date_input(текст, date(2026, 8, 17)) == ожидаем


@pytest.mark.parametrize("текст, ожидаем", [
    ("в 7 вечера", datetime(2026, 8, 17, 19, 0)),
    ("завтра в 7 вечера", datetime(2026, 8, 18, 19, 0)),
    ("в 9 утра", datetime(2026, 8, 17, 9, 0)),
    ("в 3 дня", datetime(2026, 8, 17, 15, 0)),
    ("12 ночи", datetime(2026, 8, 17, 0, 0)),
    ("12 дня", datetime(2026, 8, 17, 12, 0)),
    ("завтра в 8 вечера", datetime(2026, 8, 18, 20, 0)),
    ("в 19 вечера", datetime(2026, 8, 17, 19, 0)),  # уже суточный — не доворачиваем
    ("завтра в полдень", datetime(2026, 8, 18, 12, 0)),
    ("полночь", datetime(2026, 8, 17, 0, 0)),
])
def test_время_суток_словом(текст, ожидаем):
    assert engine.parse_date_input(текст, date(2026, 8, 17)) == ожидаем


# --- месяцы словом ----------------------------------------------------------
#
# 17.08.2026 — понедельник. Прошедшая в этом году дата переезжает на следующий,
# та же логика, что у «18.08».

@pytest.mark.parametrize("текст, ожидаем", [
    ("15 марта", date(2027, 3, 15)),          # март 2026 уже прошёл
    ("20 августа", date(2026, 8, 20)),
    ("1 сентября", date(2026, 9, 1)),
    ("31 декабря", date(2026, 12, 31)),
    ("15 марта 2027", date(2027, 3, 15)),
    ("5 июля 2026", date(2026, 7, 5)),        # явный год не переезжает
    ("15 марта в 9:30", datetime(2027, 3, 15, 9, 30)),
    ("15 марта в полдесятого", datetime(2027, 3, 15, 9, 30)),
])
def test_месяц_словом(текст, ожидаем):
    assert engine.parse_date_input(текст, date(2026, 8, 17)) == ожидаем


@pytest.mark.parametrize("текст", ["1 мая", "1 мае", "1 май"])
def test_май_не_путается_с_мартом(текст):
    """Основа «ма» короче любой другой и хвостом съедает «марта» — в исходном
    коде, откуда взят приём, это живой баг. У нас май перечислен формами."""
    assert engine.parse_date_input(текст, date(2026, 8, 17)) == date(2027, 5, 1)


@pytest.mark.parametrize("мусор", [
    "в мае",          # месяц без числа — это не дата, а тридцать один вариант
    "мартышка",
    "15 мартышка",    # хвост длиннее двух букв — не падеж
    "15 маминого",    # начинается на «ма», но это не май
    "31 февраля",     # такого дня нет
])
def test_месяц_словом_мусор_отвергается(мусор):
    with pytest.raises((ValueError, TypeError)):
        engine.parse_date_input(мусор, date(2026, 8, 17))


# --- разговорное время: «полдесятого» и родня -------------------------------
#
# 17.08.2026 — понедельник. Живой отзыв: «завтра в полдесятого» не понималось,
# хотя так пишут чаще, чем «9:30».

@pytest.mark.parametrize("текст, ожидаем", [
    ("завтра в полдесятого", datetime(2026, 8, 18, 9, 30)),
    ("полдесятого", datetime(2026, 8, 17, 9, 30)),
    ("пол десятого", datetime(2026, 8, 17, 9, 30)),
    ("пол-десятого", datetime(2026, 8, 17, 9, 30)),
    ("в половине десятого", datetime(2026, 8, 17, 9, 30)),
    ("половина шестого", datetime(2026, 8, 17, 5, 30)),
    ("полвторого", datetime(2026, 8, 17, 1, 30)),
    ("полдвенадцатого", datetime(2026, 8, 17, 11, 30)),
    ("четверть десятого", datetime(2026, 8, 17, 9, 15)),
    ("без четверти десять", datetime(2026, 8, 17, 9, 45)),
    ("без пятнадцати десять", datetime(2026, 8, 17, 9, 45)),
    ("без двадцати пять", datetime(2026, 8, 17, 4, 40)),
    ("без двадцати пяти шесть", datetime(2026, 8, 17, 5, 35)),
    ("пн в полдесятого", datetime(2026, 8, 24, 9, 30)),
    ("18.08 в полшестого", datetime(2026, 8, 18, 5, 30)),
])
def test_разговорное_время(текст, ожидаем):
    """«Полдесятого» — половина ДЕСЯТОГО часа, 9:30, а не 10:30: порядковое
    число называет час, который идёт. Считается от следующего часа назад, и у
    «без пятнадцати» с «четвертью» правило то же."""
    assert engine.parse_date_input(текст, date(2026, 8, 17)) == ожидаем


@pytest.mark.parametrize("текст, ожидаем", [
    ("полдесятого вечера", datetime(2026, 8, 17, 21, 30)),
    ("завтра в полдесятого вечера", datetime(2026, 8, 18, 21, 30)),
    ("полвторого дня", datetime(2026, 8, 17, 13, 30)),
    ("без четверти восемь утра", datetime(2026, 8, 17, 7, 45)),
])
def test_разговорное_время_с_частью_суток(текст, ожидаем):
    """Разворот в «ЧЧ:ММ» идёт до разбора, поэтому «вечера» доворачивает час
    обычным путём — отдельной ветки под разговорную форму нет."""
    assert engine.parse_date_input(текст, date(2026, 8, 17)) == ожидаем


def test_разговорное_время_не_угадывает_половину_суток():
    """Половина суток не додумывается: «полпервого» — 00:30, ровно как «в 1»
    даёт 01:00. Угадывать по рабочему дню значит иногда молча промахнуться на
    двенадцать часов; кому нужен день, тот пишет «полпервого дня»."""
    assert engine.parse_date_input("полпервого", date(2026, 8, 17)) == \
        datetime(2026, 8, 17, 0, 30)
    assert engine.parse_date_input("полпервого дня", date(2026, 8, 17)) == \
        datetime(2026, 8, 17, 12, 30)


@pytest.mark.parametrize("мусор", [
    "полтринадцатого",     # такого часа не бывает
    "пол",                 # половина чего
    "без пятнадцати",      # без пятнадцати чего
    "четверть",
    "без десятого",        # «без» просит количественное, не порядковое
])
def test_разговорное_время_мусор_отвергается(мусор):
    """Недописанная разговорная форма — это отказ, а не повод угадать час."""
    with pytest.raises((ValueError, TypeError)):
        engine.parse_date_input(мусор, date(2026, 8, 17))


def test_полдень_и_полночь_не_сломались():
    """Оба начинаются на «пол» и разбираются раньше, отдельной таблицей —
    проверяем, что новая ветка их не перехватила."""
    assert engine.parse_date_input("полдень", date(2026, 8, 17)) == \
        datetime(2026, 8, 17, 12, 0)
    assert engine.parse_date_input("завтра в полночь", date(2026, 8, 17)) == \
        datetime(2026, 8, 18, 0, 0)


def test_время_через_дефис_без_даты():
    """«9-30» датой быть не может — тридцатого месяца нет, — значит это время.
    Разбор пробует время только когда датой строка не читается: «18.08» так и
    остаётся восемнадцатым августа, а не восемнадцатью ноль восемью."""
    assert engine.parse_date_input("9-30", date(2026, 8, 17)) == datetime(2026, 8, 17, 9, 30)
    assert engine.parse_date_input("18.08", date(2026, 8, 17)) == date(2026, 8, 18)


@pytest.mark.parametrize("мусор", [
    "в", "во в на", "вечера", "завтра в 25", "завтра в 9-99", "в 9 послезавтра",
])
def test_связки_не_угадывают_смысл(мусор):
    """Одни предлоги без даты, время суток без часа и «завтра в 25» — это не
    ввод, который надо доугадать, а ввод, на который надо ответить отказом."""
    with pytest.raises((ValueError, TypeError)):
        engine.parse_date_input(мусор, date(2026, 8, 17))


# --- создание задачи через контракт формы ----------------------------------

def test_create_заводит_задачу_с_разобранными_датами(vault):
    result = run(engine.cmd_create, json=json.dumps({
        "title": "Продлить страховку",
        "tags": ["быт"],
        "steps": [{"title": "Собрать документы", "control_date": "завтра"},
                  {"title": "Оплатить полис", "control_date": "+5"}],
        "body": "Заметка",
    }))
    assert result["ok"]

    meta, body = read(vault / "Задачи" / "Продлить страховку.md")
    assert [s["control_date"] for s in meta["steps"]] == [TOMORROW, date(2026, 8, 20)]
    assert [s["id"] for s in meta["steps"]] == [1, 2], "id раздаёт движок, по порядку"
    assert meta["status"] == "ждёт"
    assert "Заметка" in body


def test_create_регистрирует_новый_тег_в_справочнике_настроек(vault):
    """Issue #7: тег из свободного поля карточки должен попасть в справочник
    settings.py, иначе tags-rename/tags-merge не находят теги, которые реально
    стоят на задачах (справочник заполнялся только явным tags-add)."""
    result = run(engine.cmd_create, json=json.dumps({
        "title": "Продлить страховку",
        "tags": ["быт"],
        "steps": [{"title": "Собрать документы", "control_date": "завтра"}],
    }))
    assert result["ok"]

    теги = cfg.list_tags(cfg.settings_path(vault))
    assert [t["name"] for t in теги] == ["быт"]


def test_create_повторный_тег_не_дублируется_в_справочнике(vault):
    """Тег, уже заведённый в справочнике (в том числе с другим цветом или
    закреплением), не должен ни упасть, ни размножиться при повторном
    использовании на новой задаче."""
    cfg.add_tag("быт", "blue", pinned=True, path=cfg.settings_path(vault))
    result = run(engine.cmd_create, json=json.dumps({
        "title": "Продлить страховку",
        "tags": ["быт"],
        "steps": [{"title": "Собрать документы", "control_date": "завтра"}],
    }))
    assert result["ok"]

    теги = cfg.list_tags(cfg.settings_path(vault))
    assert len(теги) == 1
    assert теги[0] == {"name": "быт", "color": "blue", "pinned": True}


def test_create_собирает_все_ошибки_разом(vault):
    """Форме надо подсветить все проблемные поля сразу, а не гонять по кругу."""
    result = run(engine.cmd_create, json=json.dumps({
        "title": "Отчёт: за/квартал",
        "steps": [{"title": "", "control_date": "позавчера"}],
    }))
    assert not result["ok"]
    поля = {e["field"] for e in result["errors"]}
    assert поля == {"title", "steps.0.title", "steps.0.control_date"}


def test_create_не_пускает_дубликат(vault):
    task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    result = run(engine.cmd_create, json=json.dumps({
        "title": "грант",  # тот же файл, другой регистр
        "steps": [{"title": "Что-то"}],
    }))
    assert not result["ok"]
    assert result["errors"][0]["field"] == "title"


def test_create_требует_хотя_бы_один_шаг(vault):
    result = run(engine.cmd_create, json=json.dumps({"title": "Пустая", "steps": []}))
    assert not result["ok"]
    assert result["errors"][0]["field"] == "steps"


@pytest.mark.parametrize("плохое", ['Отчёт/квартал', 'Файл: имя', 'Что?', 'a<b'])
def test_create_отвергает_запрещённые_в_windows_символы(vault, плохое):
    """Название задачи — это имя файла. Стор уезжает на Windows, и задача,
    заведённая на маке, должна там открыться."""
    result = run(engine.cmd_create, json=json.dumps({
        "title": плохое, "steps": [{"title": "Шаг"}]}))
    assert not result["ok"]
    assert result["errors"][0]["field"] == "title"


# --- шаблоны: заведение с нуля, без задачи-источника -------------------------
#
# Календарная арифметика и разбор полей шаблона проверены в test_templates.py.
# Здесь — что cmd_save_template это же считающее ядро зовёт из-под движка,
# как и cmd_create для задач: один путь записи что из формы, что из CLI.

def test_save_template_заводит_шаблон_с_нуля(vault):
    result = run(engine.cmd_save_template, json=json.dumps({
        "name": "Еженедельная встреча",
        "steps": [{"title": "Подготовить повестку", "offset_days": 0},
                  {"title": "Провести", "offset_days": 1}],
    }))
    assert result["ok"]
    assert result["template"] == "Еженедельная встреча"
    assert result["steps"] == 2

    склад = tpl.JsonStore(vault)
    шаблон = склад.get("Еженедельная встреча")
    assert шаблон is not None
    assert [s["offset_days"] for s in шаблон["steps"]] == [0, 1]


def test_save_template_без_шагов_возвращает_ошибку_поля(vault):
    result = run(engine.cmd_save_template, json=json.dumps({"name": "Пусто", "steps": []}))
    assert not result["ok"]
    assert result["errors"][0]["field"] == "steps"


def test_save_template_битый_json(vault):
    result = run(engine.cmd_save_template, json="не json")
    assert not result["ok"]
    assert result["errors"][0]["field"] is None


# --- шаблоны: удаление -------------------------------------------------------
#
# Issue #9: `templates.drop`/`Store.delete` уже умели удалять шаблон из списка,
# но до CLI, HTTP и страницы эта возможность не доходила — завести шаблон
# можно было, а убрать нет.

def test_template_delete_убирает_шаблон_из_склада(vault):
    run(engine.cmd_save_template, json=json.dumps({
        "name": "тест",
        "steps": [{"title": "шаг", "offset_days": 0}],
    }))
    result = run(engine.cmd_template_delete, name="тест")
    assert result["ok"]
    assert result["template"] == "тест"

    склад = tpl.JsonStore(vault)
    assert склад.get("тест") is None
    assert склад.all() == []


def test_template_delete_несуществующего_шаблона_не_падает(vault):
    result = run(engine.cmd_template_delete, name="Нет такого")
    assert not result["ok"]
    assert result["errors"][0]["field"] == "name"


def test_template_preview_считает_даты_до_сохранения(vault):
    """Форма показывает даты, пока человек ещё набирает сдвиги, — иначе «+10»
    выглядит правдоподобно ровно до попадания на праздники."""
    result = run(engine.cmd_template_preview, json=json.dumps({
        "name": "Черновик",
        "steps": [{"title": "Позвонить", "offset_days": 0},
                  {"title": "Сдать", "offset_days": 3}],
    }), start="2026-08-24")
    assert result["ok"]
    assert result["start"] == "2026-08-24"
    assert [s["control_date"] for s in result["steps"]] == ["2026-08-24", "2026-08-27"]
    # Подпись считает ядро: страница не пересказывает дату своими словами.
    assert result["steps"][0]["control_text"] == "пн 24.08"


def test_template_preview_не_требует_названия(vault):
    """Шаги набирают раньше, чем придумывают имя. Ругаться на пустое поле в
    предпросмотре незачем — на сохранении оно и так не пройдёт."""
    result = run(engine.cmd_template_preview, json=json.dumps({
        "name": "", "steps": [{"title": "Позвонить", "offset_days": 0}]}), start="2026-08-24")
    assert result["ok"]

    сохранение = run(engine.cmd_save_template, json=json.dumps({
        "name": "", "steps": [{"title": "Позвонить", "offset_days": 0}]}))
    assert not сохранение["ok"]
    assert сохранение["errors"][0]["field"] == "name"


def test_template_preview_жалуется_на_сдвиг_назад(vault):
    result = run(engine.cmd_template_preview, json=json.dumps({
        "name": "Черновик",
        "steps": [{"title": "Первый", "offset_days": 5},
                  {"title": "Второй", "offset_days": 2}],
    }), start="2026-08-24")
    assert not result["ok"]
    assert result["errors"][0]["field"] == "steps.1.offset_days"


# --- файлы шаблона ----------------------------------------------------------
#
# Вложение на шаблоне бессмысленно само по себе: смотрит человек в задачу.
# Поэтому проверяется не «строка легла в таблицу», а что файл доезжает до
# задачи, заведённой по шаблону, — и обычной, и очередным циклом повторения.

def шаблон_с_файлом(vault, имя="Отчёт", filename="схема.png"):
    tpl.JsonStore(vault).save({
        "name": имя, "steps": [{"title": "Собрать", "offset_days": 0}]})
    прикрепление = run(engine.cmd_attach, template=имя, filename=filename,
                       data=b"\x89PNG\r\n\x1a\n" + b"0" * 32, file=None,
                       step=None, task=None, caption=None)
    assert прикрепление["ok"], прикрепление
    return прикрепление


def test_вложение_цепляется_к_шаблону(vault):
    прикрепление = шаблон_с_файлом(vault)
    assert прикрепление["mime"] == "image/png"
    список = run(engine.cmd_attachments, template="Отчёт", task=None, step=None)
    assert [a["filename"] for a in список["attachments"]] == ["схема.png"]


def test_шаблон_адресуется_без_учёта_регистра(vault):
    """Имена шаблонов сравниваются без регистра (`tpl.same_name`) — вложения
    не должны заводить вторую полку рядом с той же карточкой."""
    шаблон_с_файлом(vault)
    список = run(engine.cmd_attachments, template="отчёт", task=None, step=None)
    assert len(список["attachments"]) == 1


def test_вложение_к_несуществующему_шаблону(vault):
    r = run(engine.cmd_attach, template="Нет такого", filename="a.png", data=b"x",
            file=None, step=None, task=None, caption=None)
    assert not r["ok"]
    assert r["errors"][0]["field"] == "template"


def test_файлы_шаблона_уезжают_в_задачу(vault):
    шаблон_с_файлом(vault)
    r = run(engine.cmd_from_template, name="Отчёт", start=None, title="Отчёт за август")
    assert r["ok"]
    assert r["attachments"] == 1
    список = run(engine.cmd_attachments, task="Отчёт за август", step=None, template=None)
    assert [a["filename"] for a in список["attachments"]] == ["схема.png"]
    # У шаблона файл остаётся: следующая задача получит его так же.
    assert len(run(engine.cmd_attachments, template="Отчёт",
                   task=None, step=None)["attachments"]) == 1


def test_файлы_шаблона_уезжают_в_цикл_повторения(vault):
    шаблон_с_файлом(vault)
    склад = tpl.JsonStore(vault)
    данные = dict(склад.get("Отчёт"))
    данные["recurrence"] = {"anchor": "2026-11-20", "freq": "monthly"}
    склад.save(данные)

    r = run(engine.cmd_recur, today=date(2026, 11, 20), name=None, limit=None)
    задача = r["templates"][0]["created"][0]["task"]
    список = run(engine.cmd_attachments, task=задача, step=None, template=None)
    assert [a["filename"] for a in список["attachments"]] == ["схема.png"]


def test_байты_вложения_не_дублируются_на_диске(vault):
    """Файл адресуется своим sha256, поэтому вторая ссылка на ту же картинку
    ничего не пишет на диск — копирование в задачу это строка в таблице."""
    шаблон_с_файлом(vault)
    run(engine.cmd_from_template, name="Отчёт", start=None, title="Отчёт за август")
    файлы = list((vault / "вложения").iterdir())
    assert len(файлы) == 1


# --- повторения: движок держит правило единственного цикла ------------------
#
# Сама календарная арифметика проверена в test_recurrence.py — сверена с
# dateutil.rrule на шести тысячах случайных правил. Здесь проверяется другое:
# что движок правильно ведёт журнал между вызовами, создаёт ровно один цикл за
# раз и не плодит дублей при повторном прогоне.

import templates as tpl  # noqa: E402


def месячный_шаблон(vault, day=5, **поля):
    склад = tpl.JsonStore(vault)
    шаблон = {
        "name": "Отчёт по кассе",
        "steps": [{"title": "Свести кассу", "offset_days": 0, "time_of_day": "10:00"}],
        "recurrence": {"anchor": f"2026-08-{day:02d}", "freq": "monthly",
                       "bymonthday": [day], **поля},
    }
    return склад.save(шаблон)


def test_recur_создаёт_только_первый_непройденный_цикл(vault):
    """Три цикла позади (авг/сен/окт), сегодня — 20 ноября. Создаётся только
    самый старый: следующие ждут, пока закроется он."""
    месячный_шаблон(vault)
    r = run(engine.cmd_recur, today=date(2026, 11, 20))
    задачи = r["templates"][0]
    assert [c["task"] for c in задачи["created"]] == ["Отчёт по кассе — 05.08.2026"]
    assert len(задачи["skipped"]) == 3


def test_recur_повторный_вызов_не_плодит_дублей(vault):
    """Идемпотентность из контракта: обрыв связи и повтор запроса не должны
    заводить вторую задачу на тот же цикл."""
    месячный_шаблон(vault)
    run(engine.cmd_recur, today=date(2026, 11, 20))
    r2 = run(engine.cmd_recur, today=date(2026, 11, 20))
    assert r2["created"] == 0
    assert len(engine.load_tasks()) == 1


def test_recur_закрытие_цикла_открывает_следующий(vault):
    """Ядро требования: незакрытый цикл блокирует следующий, а закрытие снимает
    блокировку и продвигает журнал ровно на один шаг вперёд, а не сразу до конца."""
    месячный_шаблон(vault)
    run(engine.cmd_recur, today=date(2026, 11, 20))
    run(engine.cmd_done, today=date(2026, 8, 5),
        task="Отчёт по кассе — 05.08", step="1")

    r = run(engine.cmd_recur, today=date(2026, 11, 20))
    задачи = r["templates"][0]
    assert [c["task"] for c in задачи["created"]] == ["Отчёт по кассе — 05.09.2026"]
    assert len(задачи["skipped"]) == 2


def test_recur_журнал_хранит_только_последний_цикл(vault):
    """Журнал — не полная история, а указатель на последний известный цикл:
    иначе он растёт вечно на каждое правило."""
    месячный_шаблон(vault)
    run(engine.cmd_recur, today=date(2026, 11, 20))
    состояние = engine.load_recurrence_state()
    assert состояние["Отчёт по кассе"]["previous"]["date"] == "2026-08-05"
    assert состояние["Отчёт по кассе"]["previous"]["closed"] is False


def test_recur_удалённая_задача_не_блокирует_навсегда(vault):
    """Заказчик вправе удалить задачу руками. Отсутствие задачи считается
    закрытием цикла, а не вечной блокировкой правила."""
    месячный_шаблон(vault)
    run(engine.cmd_recur, today=date(2026, 11, 20))
    run(engine.cmd_delete, task="Отчёт по кассе — 05.08.2026")

    r = run(engine.cmd_recur, today=date(2026, 11, 20))
    задачи = r["templates"][0]
    assert [c["task"] for c in задачи["created"]] == ["Отчёт по кассе — 05.09.2026"]


def test_recur_без_правила_шаблон_пропускается(vault):
    """Обычный шаблон без повторения не должен ничего создавать сам."""
    tpl.JsonStore(vault).save({"name": "Просто шаблон",
                               "steps": [{"title": "Шаг", "offset_days": 0}]})
    r = run(engine.cmd_recur, today=date(2026, 11, 20))
    assert r["templates"] == []
    assert r["created"] == 0


def test_recur_несколько_шаблонов_независимы(vault):
    """Ошибка или блокировка на одном правиле не должна тормозить остальные."""
    месячный_шаблон(vault, day=5)
    склад = tpl.JsonStore(vault)
    склад.save({
        "name": "Полить цветы",
        "steps": [{"title": "Полить", "offset_days": 0}],
        "recurrence": {"anchor": "2026-11-01", "freq": "weekly", "byweekday": [0]},
    })
    r = run(engine.cmd_recur, today=date(2026, 11, 20))
    имена = {t["template"] for t in r["templates"]}
    assert имена == {"Отчёт по кассе", "Полить цветы"}
    assert all(t["created"] for t in r["templates"])


def test_recur_name_ограничивает_одним_шаблоном(vault):
    """`--name` обещан справкой как «только один шаблон» (R8 issue): без фильтра
    в цикле по складу обрабатывались все шаблоны с активным повторением,
    независимо от переданного имени."""
    месячный_шаблон(vault, day=5)
    tpl.JsonStore(vault).save({
        "name": "Полить цветы",
        "steps": [{"title": "Полить", "offset_days": 0}],
        "recurrence": {"anchor": "2026-11-01", "freq": "weekly", "byweekday": [0]},
    })
    r = run(engine.cmd_recur, today=date(2026, 11, 20), name="Полить цветы", limit=None)
    assert [t["template"] for t in r["templates"]] == ["Полить цветы"]


def test_recur_name_несуществующего_шаблона_ничего_не_делает(vault):
    месячный_шаблон(vault, day=5)
    r = run(engine.cmd_recur, today=date(2026, 11, 20), name="Нет такого", limit=None)
    assert r["templates"] == []
    assert r["created"] == 0


# --- issue #2: ручное «Завести задачу» не должно плодить дубль циклом -------
#
# Если по шаблону с активным повторением сперва завели задачу вручную через
# from-template, а потом прогнали recur (то, что реально дёргает cron), для
# того же цикла заводился второй, дублирующий экземпляр: `_cycle_closed`
# искала задачу с именем ровно `recurring_title(имя, дата)` = «шаблон — дата»,
# а ручная задача называется просто именем шаблона — совпадения нет, журнал
# `.повторения.json` про ручную задачу тоже ничего не знает.

def test_from_template_и_recur_не_дают_дубль_ежедневно(vault):
    tpl.JsonStore(vault).save({
        "name": "Полить цветы",
        "steps": [{"title": "Полить", "offset_days": 0}],
        "recurrence": {"anchor": "2026-08-26", "freq": "daily"},
    })
    r1 = run(engine.cmd_from_template, today=date(2026, 8, 26),
             name="Полить цветы", start=None, title=None)
    assert r1["ok"]

    r2 = run(engine.cmd_recur, today=date(2026, 8, 26), name=None, limit=None)
    assert r2["created"] == 0
    задачи = [t["path"].stem for t in engine.load_tasks()]
    assert задачи == ["Полить цветы"]


def test_from_template_и_recur_не_дают_дубль_ежемесячно(vault):
    месячный_шаблон(vault, day=5)
    r1 = run(engine.cmd_from_template, today=date(2026, 8, 5),
             name="Отчёт по кассе", start=None, title=None)
    assert r1["ok"]

    r2 = run(engine.cmd_recur, today=date(2026, 8, 5), name=None, limit=None)
    assert r2["created"] == 0
    задачи = [t["path"].stem for t in engine.load_tasks()]
    assert задачи == ["Отчёт по кассе"]


def test_from_template_закрытая_вручную_задача_открывает_следующий_цикл(vault):
    """Ручная задача блокирует ровно так же, как автосозданная: следующий
    цикл появляется только после того, как эта закрыта."""
    месячный_шаблон(vault, day=5)
    run(engine.cmd_from_template, today=date(2026, 8, 5),
        name="Отчёт по кассе", start=None, title=None)
    run(engine.cmd_done, today=date(2026, 8, 5), task="Отчёт по кассе", step="1")

    r = run(engine.cmd_recur, today=date(2026, 9, 5), name=None, limit=None)
    задачи = r["templates"][0]
    assert [c["task"] for c in задачи["created"]] == ["Отчёт по кассе — 05.09.2026"]


def test_from_template_без_повторения_журнал_не_трогает(vault):
    """Шаблон без правила повторения не должен ничего писать в `.повторения.json`."""
    tpl.JsonStore(vault).save({"name": "Просто шаблон",
                               "steps": [{"title": "Шаг", "offset_days": 0}]})
    run(engine.cmd_from_template, today=date(2026, 8, 5),
        name="Просто шаблон", start=None, title=None)
    assert engine.load_recurrence_state() == {}


# --- база знаний: подключение kb.py (R17, разделы 5.7/5.8/7 ТЗ) ------------
#
# Сама морфология и защита от мусора уже проверены в test_kb.py на голом
# kb.py — здесь только то, что добавляет движок: чтение База/*.md,
# `--source-*` подмешивание уже подтверждённого, сплайс [[ссылок]] в тело
# задачи по убыванию смещения и идемпотентность через настоящий стор.

def kb_note(vault, title, *, aliases=None, body=""):
    """Кладёт запись базы знаний в стор — по образцу task()."""
    (vault / "База").mkdir(exist_ok=True)
    meta = {"type": "note", "title": title}
    if aliases:
        meta["aliases"] = list(aliases)
    fm = yaml.dump(meta, Dumper=NoAliasDumper, allow_unicode=True,
                   sort_keys=False, default_flow_style=False)
    path = vault / "База" / f"{title}.md"
    path.write_text(f"---\n{fm}---\n\n{body}", encoding="utf-8")
    return path


def test_kb_scan_находит_упоминание_offset_совпадает_со_срезом(vault):
    kb_note(vault, "Василий Говнов")
    текст = "Позвонить Василию Говнову завтра"
    result = run(engine.cmd_kb_scan, text=текст)

    assert len(result["hypotheses"]) == 1
    г = result["hypotheses"][0]
    assert г["entry_id"] == "Василий Говнов"
    assert текст[г["offset_start"]:г["offset_end"]] == г["matched"]
    assert result["confirmed"] == []
    assert result["kb_broken"] == []


@pytest.mark.parametrize("форма", ["Говнову", "Говновым", "Говнове"])
def test_kb_scan_не_путает_падежи(форма, vault):
    kb_note(vault, "Говнов")
    result = run(engine.cmd_kb_scan, text=f"отдать {форма} денег")
    assert [г["entry_id"] for г in result["hypotheses"]] == ["Говнов"]


@pytest.mark.parametrize("текст", ["Грантовый конкурс в июле", "говновая контора"])
def test_kb_scan_не_путает_словообразование(текст, vault):
    """Докстринг kb.py прямо предупреждает про этот промах наивного среза
    окончания: «Грантовый» даёт «грантов» из «Фонда президентских грантов»,
    «говновая» — «говнов» из фамилии другого человека. Через морфологию,
    подключённую здесь же, такого не происходит."""
    kb_note(vault, "Фонд президентских грантов", aliases=["ФПГ"])
    kb_note(vault, "Василий Говнов")
    assert run(engine.cmd_kb_scan, text=текст)["hypotheses"] == []


def test_kb_scan_без_записей_базы_ничего_не_падает(vault):
    result = run(engine.cmd_kb_scan, text="Отдать Василию Говнову деньги")
    assert result == {"hypotheses": [], "confirmed": [], "kb_broken": []}


def test_kb_confirm_вписывает_ссылку_в_тело(vault):
    """Написание совпадает с названием записи — простая ссылка, без piped-части
    (тот случай проверен отдельно, ниже)."""
    kb_note(vault, "Василий Говнов")
    body = "Отправить документы. Василий Говнов утвердит.\n\nВторой абзац заказчика.\n"
    path = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)], body=body)

    текст = read(path)[1]
    гипотезы = run(engine.cmd_kb_scan, text=текст)["hypotheses"]
    assert len(гипотезы) == 1

    r = run(engine.cmd_kb_confirm, source_type="task", source_id="Грант",
                mentions=гипотезы)
    assert r["ok"] and len(r["links"]) == 1 and r["errors"] == []

    _, body_after = read(path)
    assert "[[Василий Говнов]]" in body_after
    assert "Второй абзац заказчика." in body_after, "текст заказчика не должен потеряться"

    ссылки = kb.JsonLinkStore(vault).for_source("task", "Грант")
    assert len(ссылки) == 1 and ссылки[0]["kb_entry_id"] == "Василий Говнов"


def test_kb_confirm_piped_link_когда_написано_не_как_называется(vault):
    """`matched != title` → `[[Название|как написано]]`, чтобы карточка
    открывалась по названию, а текст остался таким, каким его набрал заказчик."""
    kb_note(vault, "Василий Говнов", aliases=["Вася Говнов"])
    path = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)],
                body="Спросить Васю Говнова про грант.\n")
    run(engine.cmd_refresh, force=True)

    _, body_now = read(path)
    текст, _ = engine._strip_steps_block(body_now)
    гипотеза = run(engine.cmd_kb_scan, text=текст)["hypotheses"][0]
    run(engine.cmd_kb_confirm, source_type="task", source_id="Грант", mentions=[гипотеза])

    _, body_after = read(path)
    assert "[[Василий Говнов|Васю Говнова]]" in body_after


def test_kb_confirm_несколько_гипотез_разные_смещения(vault):
    """Гипотезы обрабатываются по убыванию offset_start — иначе первая же
    вставка сдвинула бы смещения остальных, и они попали бы не туда."""
    kb_note(vault, "Пётр Семёнов")
    kb_note(vault, "Василий Говнов")
    body = "Пётр Семёнов и Василий Говнов встретились сегодня.\n"
    path = task(vault, "Встреча", [step(1, "Организовать", control_date=TODAY)], body=body)
    run(engine.cmd_refresh, force=True)

    _, body_now = read(path)
    текст, _ = engine._strip_steps_block(body_now)
    гипотезы = run(engine.cmd_kb_scan, text=текст)["hypotheses"]
    assert len(гипотезы) == 2

    run(engine.cmd_kb_confirm, source_type="task", source_id="Встреча", mentions=гипотезы)

    _, body_after = read(path)
    assert "[[Пётр Семёнов]] и [[Василий Говнов]] встретились сегодня." in body_after


def test_kb_confirm_текст_успел_измениться_не_падает(vault):
    """Ссылка в Ссылки.json пишется в любом случае — раздел 5.8: смещения
    посчитаны на старом тексте, и если заказчик успел его переписать до ответа,
    подчёркивать в теле уже нечего, но факт подтверждения не теряется."""
    kb_note(vault, "Василий Говнов")
    path = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)],
                body="Отдать Василию Говнову деньги.\n")
    текст = read(path)[1]
    гипотеза = run(engine.cmd_kb_scan, text=текст)["hypotheses"][0]

    # заказчик успел переписать абзац руками до того, как ответил на вопрос
    задача = engine.find_task("Грант")
    задача["body"] = "Текст совсем другой.\n"
    engine.save(задача, TODAY)

    r = run(engine.cmd_kb_confirm, source_type="task", source_id="Грант",
                mentions=[гипотеза])
    assert r["ok"] and len(r["links"]) == 1

    _, body_after = read(path)
    assert "[[Василий Говнов]]" not in body_after
    assert "Текст совсем другой." in body_after
    assert kb.JsonLinkStore(vault).for_source("task", "Грант")


def test_kb_confirm_повторный_вызов_идемпотентен(vault):
    kb_note(vault, "Василий Говнов")
    path = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)],
                body="Отправить документы. Василий Говнов утвердит.\n")
    run(engine.cmd_refresh, force=True)
    _, body_now = read(path)
    текст, _ = engine._strip_steps_block(body_now)
    гипотеза = run(engine.cmd_kb_scan, text=текст)["hypotheses"][0]

    r1 = run(engine.cmd_kb_confirm, source_type="task", source_id="Грант",
                 mentions=[гипотеза])
    r2 = run(engine.cmd_kb_confirm, source_type="task", source_id="Грант",
                 mentions=[гипотеза])

    assert r1["ok"] and r2["ok"]
    ссылки = kb.JsonLinkStore(vault).for_source("task", "Грант")
    assert len(ссылки) == 1, "вторая ссылка на то же место не завелась"

    _, body_after = read(path)
    assert body_after.count("[[Василий Говнов]]") == 1, "вставлено ровно один раз"


def test_kb_scan_подмешивает_подтверждённые_и_не_переспрашивает(vault):
    kb_note(vault, "Василий Говнов")
    path = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)],
                body="Отдать Василию Говнову деньги.\n")
    run(engine.cmd_refresh, force=True)
    _, body_now = read(path)
    текст, _ = engine._strip_steps_block(body_now)

    гипотеза = run(engine.cmd_kb_scan, text=текст)["hypotheses"][0]
    run(engine.cmd_kb_confirm, source_type="task", source_id="Грант", mentions=[гипотеза])

    result = run(engine.cmd_kb_scan, text=текст, source_type="task", source_id="Грант")
    assert result["hypotheses"] == [], "уже отвеченное не переспрашиваем"
    assert len(result["confirmed"]) == 1
    подтв = result["confirmed"][0]
    assert подтв["kb_entry_id"] == "Василий Говнов"
    assert (подтв["offset_start"], подтв["offset_end"]) == \
        (гипотеза["offset_start"], гипотеза["offset_end"])


def test_kb_confirm_сразу_после_создания_задачи(vault):
    """Путь из раздела 4: форма сканирует текст ДО того, как задача вообще
    существует (блока шагов ещё нет), потом /api/create заводит задачу — и
    только это сохранение впервые вставляет блок, склеивая его с текстом
    заказчика через «\\n\\n» (put_steps_into_body). Подтверждение приходит
    следом, с теми же гипотезами, что вернул самый первый скан. Если сплайс
    не срежет эту склейку вместе с блоком, смещения уедут на два символа и
    вставка молча не произойдёт — регрессионный тест ровно на этот случай."""
    kb_note(vault, "Василий Говнов")
    сырой_текст = "Отдать Василию Говнову деньги"
    гипотезы = run(engine.cmd_kb_scan, text=сырой_текст)["hypotheses"]
    assert len(гипотезы) == 1

    создание = run(engine.cmd_create, json=json.dumps({
        "title": "Долг", "steps": [{"title": "Шаг"}], "body": сырой_текст,
    }))
    assert создание["ok"]

    r = run(engine.cmd_kb_confirm, source_type="task", source_id=создание["task"],
                mentions=гипотезы)
    assert r["ok"] and len(r["links"]) == 1 and r["errors"] == []

    _, body_after = read(vault / "Задачи" / "Долг.md")
    assert "[[Василий Говнов|Василию Говнову]]" in body_after


def test_kb_reject_убирает_гипотезу_из_следующего_скана(vault):
    kb_note(vault, "Василий Говнов")
    текст = "Отдать Василию Говнову деньги"
    гипотеза = run(engine.cmd_kb_scan, text=текст)["hypotheses"][0]

    run(engine.cmd_kb_reject, mention=гипотеза, mute=False)
    assert run(engine.cmd_kb_scan, text=текст)["hypotheses"] == []


def test_kb_reject_mute_гасит_слово_у_любой_записи(vault):
    """Отказ по слову хранится без entry_id (`word_key`): гасит слово для любой
    записи, а не только для той, от которой пришла отклонённая гипотеза.
    Проверяем на двух РАЗНЫХ записях с одинаковым написанием названия — второй
    заводим уже после отказа, чтобы не столкнуться с побочной неоднозначностью
    (два омонима одновременно в базе сами по себе перестали бы находиться)."""
    kb_note(vault, "Грант")
    гипотеза = run(engine.cmd_kb_scan, text="ждём Грант в четверг")["hypotheses"][0]
    assert гипотеза["entry_id"] == "Грант"

    run(engine.cmd_kb_reject, mention=гипотеза, mute=True)
    assert run(engine.cmd_kb_scan, text="ждём Грант в четверг")["hypotheses"] == []

    (vault / "База" / "Грант.md").unlink()
    (vault / "База" / "Грант (вторая запись).md").write_text(
        "---\ntype: note\ntitle: Грант\n---\n\n", encoding="utf-8")

    assert run(engine.cmd_kb_scan, text="ждём Грант в четверг")["hypotheses"] == []
# --- резервные копии и экспорт: тонкая обвязка вокруг backup.py -------------
#
# Сам backup.py и его инварианты (ротация, атомарность, целостность архива)
# проверены в test_backup.py и здесь не дублируются. Здесь — то, что относится
# к движку: путь по умолчанию, перевод BackupError в структурную ошибку
# контракта, и что restore реально возвращает файлы к прежнему состоянию.

def test_backup_копия_снимается_за_пределами_стора(vault):
    """Раздел 9 ТЗ: пропажа папки стора не должна утащить с собой копии."""
    task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    r = run(engine.cmd_backup, dest=None, keep=None)

    assert r["ok"], r
    файл = Path(r["file"])
    assert файл.is_file()
    assert vault not in файл.parents


def test_backup_list_пуст_пока_копий_не_было(vault, tmp_path):
    """Отсутствие копий — не ошибка, а обычное состояние до первого снятия."""
    чужая = tmp_path.parent / "ещё-не-существует"
    r = run(engine.cmd_backup_list, dest=str(чужая))
    assert r == {"copies": [], "dest": str(чужая)}


def test_restore_несуществующего_файла_понятная_ошибка(vault, tmp_path):
    """BackupError движок обязан завернуть в {field, error}, а не уронить
    процесс traceback'ом — это ошибка выбора файла, а не сбой сервера."""
    r = run(engine.cmd_backup_restore, file=str(tmp_path.parent / "нет.zip"))
    assert r["ok"] is False
    assert r["errors"][0]["field"] == "archive"
    assert "нет.zip" in r["errors"][0]["error"] or "не открывается" in r["errors"][0]["error"]


def test_restore_возвращает_стор_к_состоянию_копии(vault):
    """Ядро требования R25: снял копию → изменил → восстановил → получил то,
    что было на момент копии, а не то, что стало после."""
    путь = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    r1 = run(engine.cmd_backup, dest=None, keep=None)
    assert r1["ok"], r1

    run(engine.cmd_done, task="Грант", step="1", reason="сдал")
    assert read(путь)[0]["steps"][0]["status"] == "done"

    r2 = run(engine.cmd_backup_restore, file=r1["file"])
    assert r2["ok"], r2
    meta, _ = read(путь)
    assert meta["steps"][0]["status"] == "pending"
    assert meta["steps"][0].get("completed_date") is None


def test_export_json_создаёт_валидный_json(vault):
    """R11: файл должен парситься обратно и нести хотя бы заведённую задачу."""
    task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    out = vault / "выгрузка-тест.json"

    r = run(engine.cmd_export_json, to=str(out))

    assert r["ok"], r
    данные = json.loads(out.read_text(encoding="utf-8"))
    assert данные["format"] == "yungdrung-export"
    assert [t["name"] for t in данные["tasks"]] == ["Грант"]


def test_export_json_путь_по_умолчанию_тоже_за_пределами_стора(vault):
    """Без явного --to движок сам не должен класть выгрузку внутрь стора —
    та же логика, что и у копий."""
    task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    r = run(engine.cmd_export_json, to=None)

    assert r["ok"], r
    файл = Path(r["file"])
    assert файл.is_file()
    assert vault not in файл.parents


def test_backup_повторный_с_force_не_падает_и_даёт_новый_файл(vault):
    """Идемпотентность в смысле контракта — «не падает», а не «не создаёт
    вторую запись»: у копии по кнопке «сейчас» это разные действия."""
    task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    r1 = run(engine.cmd_backup, dest=None, keep=None, force=True)
    r2 = run(engine.cmd_backup, dest=None, keep=None, force=True)

    assert r1["ok"] and r2["ok"]
    assert r1["file"] != r2["file"]
    dest = Path(r1["file"]).parent
    assert len(backup.copies(dest)) == 2


# --- настройки: причины и рабочие часы из файла, не из кода -----------------
#
# Требование R28. До этой правки REASONS был захардкожен списком в engine.py,
# а _work() читал только аргументы вызова — Настройки.json существовал сам по
# себе, трекер его не открывал. Тесты ниже проверяют ровно связку, а не
# settings.py как таковой — у него своя validate() и test_settings.py.

def test_get_reasons_отражает_файл_настроек(vault):
    """Без файла — семь причин по умолчанию (перенесены из старого REASONS)."""
    assert len(engine.get_reasons()) == 7
    cfg.add_reason("не дозвонился", path=cfg.settings_path(vault))
    assert "не дозвонился" in engine.get_reasons()


def test_get_reasons_не_предлагает_архивные(vault):
    путь = cfg.settings_path(vault)
    cfg.add_reason("устарела", path=путь)
    cfg.archive_reason("устарела", path=путь)
    assert "устарела" not in engine.get_reasons()


def test_work_читает_рабочие_часы_из_файла(vault):
    """_work() — приватная, но это ровно то место, от которого зависит вся
    лента и завал: без чтения файла настройки продукта были бы витриной."""
    путь = cfg.settings_path(vault)
    данные = cfg.defaults()
    данные["notifications"]["start"] = "07:00"
    данные["notifications"]["end"] = "16:00"
    cfg.save(данные, path=путь)
    work = engine._work(None)
    assert work["start"].strftime("%H:%M") == "07:00"
    assert work["end"].strftime("%H:%M") == "16:00"


def test_work_аргумент_вызова_перебивает_файл(vault):
    """Аргумент вызова важнее сохранённого файла — иначе разовый прогон с
    другим временем нельзя было бы сделать, не переписывая настройки.
    `_work` ждёт уже разобранный time, как и `worktime.settings()` — разбор
    строки в файле делает cfg.save()/cfg._deserialize, а не эта функция."""
    путь = cfg.settings_path(vault)
    данные = cfg.defaults()
    данные["notifications"]["start"] = "07:00"
    cfg.save(данные, path=путь)
    args = SimpleNamespace(work_start=dtime(10, 0), work_end=None, weekends=None)
    work = engine._work(args)
    assert work["start"].strftime("%H:%M") == "10:00"


def test_work_битый_файл_настроек_не_роняет_ленту(vault):
    """Испорченный Настройки.json — повод чинить настройки, не повод положить
    ленту и завал: они не про настройки, они про то, что просрочено сегодня."""
    cfg.settings_path(vault).write_text("{не json", encoding="utf-8")
    work = engine._work(None)
    assert work["start"].strftime("%H:%M") == "09:00"


# --- перенос тега по всем задачам стора ------------------------------------
#
# settings.rename_tag/merge_tags меняют только справочник (цвет, закрепление):
# сами задачи им не видны, об этом прямо сказано в докстроке merge_tags.
# rename_tag_everywhere — та часть, которую обязан сделать тот, кто подключает
# settings.py к движку.

def test_rename_tag_everywhere_меняет_тег_на_задаче(vault):
    task(vault, "Аренда", [step(1, "A", control_date=TODAY)], tags=["финансы"])
    задето = engine.rename_tag_everywhere("финансы", "деньги", TODAY)
    assert задето == 1
    meta, _ = read(vault / "Задачи" / "Аренда.md")
    assert meta["tags"] == ["деньги"]


def test_rename_tag_everywhere_схлопывает_дубликат(vault):
    """Слияние «срочное» → «важное» на задаче, где «важное» уже стоит, не
    должно оставить тег дважды."""
    task(vault, "Грант", [step(1, "A", control_date=TODAY)], tags=["срочное", "важное"])
    engine.rename_tag_everywhere("срочное", "важное", TODAY)
    meta, _ = read(vault / "Задачи" / "Грант.md")
    assert meta["tags"] == ["важное"]


def test_rename_tag_everywhere_не_трогает_задачи_без_тега(vault):
    task(vault, "Без тега", [step(1, "A", control_date=TODAY)], tags=["другое"])
    задето = engine.rename_tag_everywhere("финансы", "деньги", TODAY)
    assert задето == 0
    meta, _ = read(vault / "Задачи" / "Без тега.md")
    assert meta["tags"] == ["другое"]


# --- группы подшагов (параллельные и вложенные) ----------------------------
#
# Шаг с mode — группа: закрытие вычисляется из детей, даты и отметки только у
# листьев. Хранение плоское: parent ссылается на id родителя. Старые задачи
# (parent везде NULL) обязаны вести себя побитово как до появления групп —
# это проверяют все тесты выше, здесь только новое поведение.

def подписи(vault, **kw):
    """Задача: собрать три подписи в любом порядке, потом подать пакет."""
    return task(vault, "Сделка", [
        step(1, "Подписи", mode="par"),
        step(2, "Подпись Говнова", parent=1, control_date=TODAY),
        step(3, "Подпись банка", parent=1, control_date=date(2026, 8, 18)),
        step(4, "Подать пакет", control_date=date(2026, 8, 25)),
    ], **kw)


def test_параллельная_группа_активна_вся(vault):
    подписи(vault)
    задача = engine.find_task("Сделка")
    assert [s["id"] for s in engine.current_steps(задача)] == [2, 3]


def test_лента_даёт_строку_на_каждый_активный_лист(vault):
    пятница = date(2026, 8, 14)
    task(vault, "Сделка", [
        step(1, "Подписи", mode="par"),
        step(2, "Подпись Говнова", parent=1, control_date=пятница),
        step(3, "Подпись банка", parent=1, control_date=пятница),
        step(4, "Подать пакет", control_date=date(2026, 8, 25)),
    ])
    лента = run(engine.cmd_feed, today=пятница, now="2026-08-14T12:00")["feed"]
    строки = [i for i in лента if i["task"] == "Сделка"]
    assert [i["step"] for i in строки] == [2, 3]
    assert all(i["group"] == "Подписи" for i in строки)


def test_группа_закрыта_когда_закрыты_подшаги(vault):
    path = подписи(vault)
    run(engine.cmd_done, task="Сделка", step="2")
    run(engine.cmd_done, task="Сделка", step="3")
    задача = engine.find_task("Сделка")
    assert [s["id"] for s in engine.current_steps(задача)] == [4]
    meta, _ = read(path)
    assert meta["current_step"] == "Подать пакет"
    assert meta["progress"] == "2/3"        # группа — не единица работы


def test_статус_задачи_худшее_из_активных(vault):
    task(vault, "Сделка", [
        step(1, "Группа", mode="par"),
        step(2, "Просроченный", parent=1, control_date=date(2026, 8, 10)),
        step(3, "Ждущий", parent=1, control_date=date(2026, 8, 30)),
    ])
    задача = engine.find_task("Сделка")
    assert engine.task_status(задача, TODAY) == "overdue"


def test_сводка_берёт_ближайший_контроль_активных(vault):
    path = подписи(vault)
    meta, _ = read(path)
    assert meta["control_date"] == TODAY     # min(15.08, 18.08)


def test_группу_нельзя_отметить(vault):
    подписи(vault)
    with pytest.raises(SystemExit) as e:
        run(engine.cmd_done, task="Сделка", step="1")
    assert "группа" in str(e.value)


def test_done_раздаёт_даты_всем_открывшимся_листьям(vault):
    path = task(vault, "Сделка", [
        step(1, "Подготовить пакет", control_date=TODAY),
        step(2, "Подписи", mode="par"),
        step(3, "Подпись Говнова", parent=2),
        step(4, "Подпись банка", parent=2),
    ])
    result = run(engine.cmd_done, task="Сделка", step="1")
    assert result["dates_assigned"] == [3, 4]
    assert result["date_assigned_to_step"] == 3
    meta, _ = read(path)
    assert meta["steps"][2]["control_date"] == TODAY
    assert meta["steps"][3]["control_date"] == TODAY


def test_создание_с_вложенными_шагами(vault):
    result = run(engine.cmd_create, json=json.dumps({
        "title": "Сделка",
        "steps": [
            {"title": "Подписи", "mode": "par", "steps": [
                {"title": "Говнов", "control_date": "2026-08-20"},
                {"title": "Банк", "control_date": "2026-08-22"},
            ]},
            {"title": "Подать пакет", "control_date": "2026-08-30"},
        ]}))
    assert result["ok"], result
    задача = engine.find_task("Сделка")
    шаги = engine.steps_of(задача)
    assert [(s["id"], s.get("parent"), s.get("mode")) for s in шаги] == [
        (1, None, "par"), (2, 1, None), (3, 1, None), (4, None, None)]
    # дефолт старта после группы — самый поздний контроль её поддерева
    assert engine.as_date(шаги[3]["start_date"]) == date(2026, 8, 22)
    # внутри параллельной группы цепочки нет: оба стартуют от точки входа
    assert engine.as_date(шаги[1]["start_date"]) == engine.as_date(шаги[2]["start_date"])


def test_валидация_группы(vault):
    result = run(engine.cmd_create, json=json.dumps({
        "title": "Кривая",
        "steps": [
            {"title": "Пустая группа", "mode": "par", "steps": []},
            {"title": "Группа с датой", "control_date": "2026-08-20", "steps": [
                {"title": "Лист"}]},
            {"title": "Кривой режим", "mode": "вместе", "steps": [
                {"title": "Лист"}]},
        ]}))
    assert not result["ok"]
    поля = {e["field"] for e in result["errors"]}
    assert "steps.0.steps" in поля
    assert "steps.1.control_date" in поля
    assert "steps.2.mode" in поля


def test_вложенные_ошибки_с_полным_путём(vault):
    result = run(engine.cmd_create, json=json.dumps({
        "title": "Сделка",
        "steps": [{"title": "Группа", "steps": [{"title": ""}]}]}))
    assert not result["ok"]
    assert any(e["field"] == "steps.0.steps.0.title" for e in result["errors"])


def test_правка_собирает_шаги_в_группу(vault):
    """Карточка перетащила два существующих шага под новую группу: id, статусы
    и журнал переживают перестройку, группа получает свой новый id."""
    path = task(vault, "Сделка", [
        step(1, "Говнов", status="done", control_date=date(2026, 8, 10),
             completed_date=date(2026, 8, 10),
             log=[{"date": date(2026, 8, 10), "event": "done"}]),
        step(2, "Банк", control_date=TODAY),
    ])
    result = run(engine.cmd_update, task="Сделка", json=json.dumps({
        "title": "Сделка",
        "steps": [{"title": "Подписи", "mode": "par", "steps": [
            {"id": 1, "title": "Говнов", "control_date": "2026-08-10"},
            {"id": 2, "title": "Банк", "control_date": "2026-08-15"},
        ]}]}))
    assert result["ok"], result
    meta, _ = read(path)
    группа = meta["steps"][0]
    assert группа["mode"] == "par" and группа["id"] == 3
    assert [(s["id"], s["parent"]) for s in meta["steps"][1:]] == [(1, 3), (2, 3)]
    assert meta["steps"][1]["status"] == "done"
    assert meta["steps"][1]["log"][-1]["event"] == "done"
    assert meta["progress"] == "1/2"


def test_миграция_доращивает_старую_базу(tmp_path, monkeypatch):
    """База, созданная кодом до групп (нет parent_id/mode), открывается и
    работает: колонки добавляются, данные не трогаются. Ровно это случится
    на ноутбуке заказчика при первом запуске после git pull."""
    db = tmp_path / "стор.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL UNIQUE,
            schema INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL,
            start_date TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0,
            cancelled_reason TEXT, body TEXT NOT NULL DEFAULT '', extra TEXT);
        CREATE TABLE steps (
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            step_id INTEGER NOT NULL, position INTEGER NOT NULL,
            title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            start_date TEXT, control_date TEXT, completed_date TEXT, note TEXT,
            PRIMARY KEY (task_id, step_id));
        INSERT INTO tasks (title, created, start_date, body)
            VALUES ('Старая', '2026-08-01', '2026-08-01', '');
        INSERT INTO steps (task_id, step_id, position, title, control_date)
            VALUES (1, 1, 0, 'Единственный шаг', '2026-08-15');
    """)
    conn.commit()
    conn.close()

    задачи = store.Store(db).load_tasks()
    assert len(задачи) == 1
    шаг = задачи[0]["meta"]["steps"][0]
    assert шаг["title"] == "Единственный шаг"
    assert шаг["parent"] is None and шаг["mode"] is None

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA
    conn.close()


# --- настройки доходят до потребителя ---------------------------------------
#
# Сверка покрытия 2026-08-24 нашла восемь ключей, которые сохранялись и
# валидировались, но не читались никем: человек менял настройку и делал вывод,
# что она работает. Тесты ниже стерегут те, у которых потребитель появился.

def test_счётчик_переносов_карточка_берёт_у_движка(vault):
    """Раньше карточка считала переносы сама и считала иначе, чем лента: брала
    обычный `defer`, которого движок намеренно не считает. Один шаг показывал
    два разных числа на двух экранах, и ни одно нельзя было назвать верным."""
    т = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY, log=[
        {"date": TODAY, "event": "not_done", "reason": "не успел"},
        {"date": TODAY, "event": "defer", "to": TODAY},
        {"date": TODAY, "event": "mass_defer", "reason": "завал"},
    ])])
    показ = run(engine.cmd_show, task=т.stem)
    шаг = показ["steps"][0]
    # not_done + mass_defer = 2; одиночный defer не в счёт
    assert шаг["stalled"] == 2
    # То же число, что видит лента: у обоих экранов один источник.
    meta, _ = read(т)
    assert шаг["stalled"] == engine.stall_count(meta["steps"][0])


def test_бэкап_берёт_папку_и_число_копий_из_настроек(vault, tmp_path):
    """`backup.folder` и `backup.keep_count` лежали в файле мёртвым грузом:
    `cmd_backup` брал папку рядом с стором и константу `KEEP_DEFAULT`."""
    своя = tmp_path / "своя-папка-копий"
    данные = cfg.defaults()
    данные["backup"]["folder"] = str(своя)
    данные["backup"]["keep_count"] = 2
    cfg.save(данные, cfg.settings_path(vault))

    настройки = engine._backup_settings()
    assert настройки["folder"] == str(своя)
    assert настройки["keep_count"] == 2

    итог = run(engine.cmd_backup, dest=None, keep=None, force=True)
    assert итог["ok"], итог
    assert своя.is_dir(), "копия легла не в папку из настроек"


def test_битые_настройки_бэкапа_не_роняют_копию(vault):
    """Тот же принцип, что у рабочих часов: почему файл битый — разбирается в
    настройках-интерфейсе, а не в момент снятия копии."""
    cfg.settings_path(vault).write_text("{ это не json", encoding="utf-8")
    assert engine._backup_settings() == cfg.defaults()["backup"]


# --- происхождение задачи: чем архив свернёт циклы (схема v3) -----------------
#
# Решение по Q24 — сворачивать двенадцать циклов «Налогов» в одну строку с
# возможностью развернуть. Группировать разбором названия нельзя: задачу
# переименовывают, имя шаблона само может содержать тире, а заведённая руками
# «Отчёт — 05.09.2026» попала бы в группу ни за что. Отсюда две колонки.

def test_цикл_повторения_помнит_шаблон_и_ключ(vault):
    склад = tpl.JsonStore(vault)
    склад.save({"name": "Налоги",
                "steps": [{"title": "Подать декларацию", "offset_days": 0}],
                "recurrence": {"anchor": "2026-06-05", "freq": "monthly",
                               "bymonthday": [5]}})
    run(engine.cmd_recur, today=date(2026, 6, 5), name=None, limit=None)

    задача = engine.load_tasks()[0]
    assert задача["meta"]["template_name"] == "Налоги"
    # Тот же ключ, которым журнал повторений отличает цикл от цикла.
    assert задача["meta"]["cycle_key"] == "2026-06-05"


def test_у_заведённой_руками_задачи_происхождения_нет(vault):
    """Поля не появляются вовсе, а не лежат пустыми: код вокруг видит ту же
    форму meta, что до v3, и одиночная задача не попадёт ни в какую группу."""
    run(engine.cmd_create, json=json.dumps({
        "title": "Своими руками", "steps": [{"title": "Шаг"}]}))
    meta = engine.load_tasks()[0]["meta"]
    assert "template_name" not in meta and "cycle_key" not in meta


def test_правка_карточки_не_меняет_происхождения(vault):
    """Задача не может «стать» циклом чужого шаблона оттого, что ей поменяли
    заголовок: UPDATE эти колонки не трогает."""
    склад = tpl.JsonStore(vault)
    склад.save({"name": "Налоги", "steps": [{"title": "Подать", "offset_days": 0}],
                "recurrence": {"anchor": "2026-06-05", "freq": "monthly",
                               "bymonthday": [5]}})
    run(engine.cmd_recur, today=date(2026, 6, 5), name=None, limit=None)
    было = engine.load_tasks()[0]

    r = run(engine.cmd_update, task=было["path"].stem, json=json.dumps({
        "title": "Налоги за июнь", "start_date": "2026-06-05",
        "steps": [{"id": s["id"], "title": s["title"],
                   "control_date": str(s["control_date"])}
                  for s in было["meta"]["steps"]]}))
    assert r["ok"], r
    стало = engine.load_tasks()[0]["meta"]
    assert стало["title"] == "Налоги за июнь"
    assert стало["template_name"] == "Налоги" and стало["cycle_key"] == "2026-06-05"


def test_происхождение_не_дублируется_в_extra(vault):
    """У него свои колонки; лёжа ещё и в JSON-поле, оно однажды разошлось бы."""
    склад = tpl.JsonStore(vault)
    склад.save({"name": "Налоги", "steps": [{"title": "Подать", "offset_days": 0}],
                "recurrence": {"anchor": "2026-06-05", "freq": "monthly",
                               "bymonthday": [5]}})
    run(engine.cmd_recur, today=date(2026, 6, 5), name=None, limit=None)
    conn = sqlite3.connect(str(vault / "стор.db"))
    extra = conn.execute("SELECT extra FROM tasks").fetchone()[0]
    conn.close()
    assert extra is None or "template_name" not in extra


# --- поиск по истории (R24) --------------------------------------------------
#
# Главный ответ на вопрос заказчика «как я это делал в прошлый раз». Проверяется
# не «нашлось хоть что-то», а падежи: unicode61 в FTS5 морфологии не знает, и
# без лемматизации «грант» не нашёл бы «гранту» — ровно то, ради чего слой
# лемм и заводился.

@pytest.fixture
def стор_для_поиска(vault):
    run(engine.cmd_create, json=json.dumps({
        "title": "Подать заявку на грант ФПГ",
        "body": "Созвониться с Василием про декларацию",
        "steps": [{"title": "Собрать документы"}, {"title": "Отправить в фонд"}]}))
    run(engine.cmd_create, json=json.dumps({
        "title": "Налоги за третий квартал",
        "steps": [{"title": "Свериться с бухгалтером"}]}))
    return vault


@pytest.mark.parametrize("запрос, ожидание", [
    ("гранту", "Подать заявку на грант ФПГ"),        # падеж в заголовке
    ("заявка", "Подать заявку на грант ФПГ"),        # именительный против винительного
    ("декларациях", "Подать заявку на грант ФПГ"),   # падеж в заметке
    ("документ", "Подать заявку на грант ФПГ"),      # слово из названия шага
    ("бухгалтеру", "Налоги за третий квартал"),      # падеж в названии шага
    ("Василия", "Подать заявку на грант ФПГ"),       # имя в другом падеже
    ("налог", "Налоги за третий квартал"),
])
def test_поиск_находит_в_любом_падеже(стор_для_поиска, запрос, ожидание):
    r = run(engine.cmd_search, text=запрос, kind=None, limit=None)
    assert r["ok"], r
    assert [н["title"] for н in r["results"]] == [ожидание]


def test_два_слова_ищутся_вместе_а_не_по_отдельности(стор_для_поиска):
    """Человек, набравший два слова, ищет то, где есть оба."""
    assert run(engine.cmd_search, text="заявка грант",
               kind=None, limit=None)["count"] == 1
    assert run(engine.cmd_search, text="заявка налоги",
               kind=None, limit=None)["count"] == 0


def test_новая_задача_ищется_сразу(стор_для_поиска):
    """Индекс поддерживается при записи, а не только командой `reindex`:
    иначе заказчик заведёт задачу и не найдёт её."""
    run(engine.cmd_create, json=json.dumps({
        "title": "Совсем свежая про субсидию", "steps": [{"title": "Шаг"}]}))
    assert run(engine.cmd_search, text="субсидии", kind=None, limit=None)["count"] == 1


def test_удалённая_задача_уходит_из_поиска(стор_для_поиска):
    """Иначе она находится и ведёт в никуда."""
    run(engine.cmd_delete, task="Налоги за третий квартал")
    assert run(engine.cmd_search, text="налог", kind=None, limit=None)["count"] == 0


def test_переименованная_не_находится_по_старому_названию(vault):
    """Строку индекса адресует название: без снятия старой задача находилась бы
    и по прежнему слову, и по новому."""
    run(engine.cmd_create, json=json.dumps({
        "title": "Старое про грант", "start_date": "2026-08-17",
        "steps": [{"title": "Шаг", "control_date": "2026-08-17"}]}))
    r = run(engine.cmd_update, task="Старое про грант", json=json.dumps({
        "title": "Новое про декларацию", "start_date": "2026-08-17",
        "steps": [{"id": 1, "title": "Шаг", "control_date": "2026-08-17"}]}))
    assert r["ok"], r
    assert run(engine.cmd_search, text="грант", kind=None, limit=None)["count"] == 0
    assert run(engine.cmd_search, text="декларация", kind=None, limit=None)["count"] == 1


def test_мусорный_запрос_не_роняет_поиск(стор_для_поиска):
    """В строку поиска набирают что угодно, включая скобки и кавычки, на
    которых FTS5 ругается синтаксической ошибкой. Пустая выдача честнее
    исключения, вылетевшего в морду."""
    for мусор in ["(((", '"', "* AND", "^^^"]:
        r = run(engine.cmd_search, text=мусор, kind=None, limit=None)
        assert r["count"] == 0 if r.get("ok") else r["errors"]


def test_reindex_собирает_индекс_заново(стор_для_поиска):
    """Способ починить разошедшийся индекс: чинится он только так."""
    engine.get_store().search_clear()
    assert run(engine.cmd_search, text="грант", kind=None, limit=None)["count"] == 0
    итог = run(engine.cmd_reindex)
    assert итог["tasks"] == 2
    assert run(engine.cmd_search, text="грант", kind=None, limit=None)["count"] == 1


def test_записи_базы_знаний_тоже_ищутся(vault):
    engine.get_store().add_kb_note("Василий Говнов", ["Вася"], "Юрист и согласующий")
    run(engine.cmd_reindex)
    r = run(engine.cmd_search, text="юристу", kind=None, limit=None)
    assert [н["source_type"] for н in r["results"]] == ["kb_note"]
    assert r["results"][0]["title"] == "Василий Говнов"


# --- архив: история и свёртка циклов (R24, Q24) ------------------------------

@pytest.fixture
def стор_с_архивом(vault):
    склад = tpl.JsonStore(vault)
    склад.save({"name": "Налоги", "steps": [{"title": "Подать", "offset_days": 0}],
                "recurrence": {"anchor": "2026-06-05", "freq": "monthly",
                               "bymonthday": [5]}})
    for d in (date(2026, 6, 5), date(2026, 7, 5), date(2026, 8, 5)):
        run(engine.cmd_recur, today=d, name=None, limit=None)
        имя = f"Налоги — {d:%d.%m.%Y}"
        run(engine.cmd_done, today=d, task=имя, step=1)

    run(engine.cmd_create, json=json.dumps({
        "title": "Разовая закрытая", "tags": ["личное"], "start_date": "2026-08-10",
        "steps": [{"title": "Шаг", "control_date": "2026-08-10"}]}))
    run(engine.cmd_done, today=date(2026, 8, 11), task="Разовая закрытая", step=1)

    run(engine.cmd_create, json=json.dumps({
        "title": "Отменённая", "start_date": "2026-08-01",
        "steps": [{"title": "Шаг"}]}))
    run(engine.cmd_cancel, task="Отменённая", reason="не актуально")

    run(engine.cmd_create, json=json.dumps({
        "title": "Ещё живая", "steps": [{"title": "Шаг", "control_date": "2026-12-01"}]}))
    return vault


def test_архив_не_показывает_открытые_задачи(стор_с_архивом):
    r = run(engine.cmd_archive, tag=None, since=None, until=None)
    имена = {i["task"] for i in r["items"] if i["kind"] == "task"}
    assert "Ещё живая" not in имена
    assert r["count"] == 5  # 3 цикла + разовая + отменённая


def test_циклы_повторения_сворачиваются_в_группу(стор_с_архивом):
    r = run(engine.cmd_archive, tag=None, since=None, until=None)
    группы = [i for i in r["items"] if i["kind"] == "cycle_group"]
    assert len(группы) == 1
    группа = группы[0]
    assert группа["template_name"] == "Налоги"
    assert группа["count"] == 3
    assert [t["cycle_key"] for t in группа["tasks"]] == \
        ["2026-08-05", "2026-07-05", "2026-06-05"]  # свежее выше


def test_отменённая_и_разовая_не_группируются(стор_с_архивом):
    r = run(engine.cmd_archive, tag=None, since=None, until=None)
    одиночные = {i["task"] for i in r["items"] if i["kind"] == "task"}
    assert одиночные == {"Разовая закрытая", "Отменённая"}


def test_фильтр_по_тегу(стор_с_архивом):
    r = run(engine.cmd_archive, tag="личное", since=None, until=None)
    assert r["count"] == 1
    assert r["items"][0]["task"] == "Разовая закрытая"


def test_фильтр_по_периоду_разбивает_группу(стор_с_архивом):
    """Один цикл прошёл фильтр, два отсеялись — группе сворачивать больше
    нечего, она обязана превратиться в одиночную строку."""
    r = run(engine.cmd_archive, tag=None, since=None, until="2026-07-01")
    циклы = [i for i in r["items"] if i.get("template_name") == "Налоги"]
    assert len(циклы) == 1
    assert циклы[0]["kind"] == "task"
    assert циклы[0]["task"] == "Налоги — 05.06.2026"


def test_период_фильтрует_отменённую_по_дате_начала(стор_с_архивом):
    """У отменённой задачи может не быть completed_date вовсе — фильтровать её
    неоткуда, кроме даты начала (2026-08-01 в этом сторе)."""
    r = run(engine.cmd_archive, tag=None, since="2026-08-01", until="2026-08-01")
    имена = {i["task"] for i in r["items"] if i["kind"] == "task"}
    assert имена == {"Отменённая"}


def test_архив_отклоняет_непонятную_дату_границы(vault):
    r = run(engine.cmd_archive, tag=None, since="тридцать первое февраля", until=None)
    assert not r["ok"]
    assert r["errors"][0]["field"] == "since"


def test_граница_периода_смотрит_в_прошлое_а_не_вперёд(vault):
    """`parse_period_date` — не то же самое, что `parse_date_input`: тот
    планирует вперёд («18.08» без года ушло бы в следующий август), а для
    границы архива это означало бы «ничего не найдено» вместо прошлого."""
    assert engine.parse_period_date("18.08", date(2026, 8, 25)) == date(2026, 8, 18)
    assert engine.parse_period_date("01.01", date(2026, 8, 25)) == date(2026, 1, 1)
    # Ещё не наступившая в этом году дата — годом раньше, не годом позже.
    assert engine.parse_period_date("31.12", date(2026, 1, 5)) == date(2025, 12, 31)


# --- настройки базы знаний доходят до сканирования (сверка покрытия) --------

def test_auto_recognition_выключенный_гасит_новые_гипотезы(vault):
    """Флаг выключает поиск новых совпадений, а не сканирование как таковое:
    уже подтверждённая ссылка — факт, который заказчик когда-то подтвердил
    сам, и выключенный флаг не должен стирать историю."""
    (vault / "База").mkdir()
    (vault / "База" / "Василий Говнов.md").write_text(
        "---\ntype: note\ntitle: Василий Говнов\n---\n\nЮрист.\n", encoding="utf-8")
    engine.migrate_kb_to_db()

    данные = cfg.defaults()
    cfg.save(данные, cfg.settings_path(vault))
    r = run(engine.cmd_kb_scan, text="Спросить у Василия Говнова",
            source_type=None, source_id=None)
    assert r["hypotheses"], "с настройками по умолчанию гипотеза должна найтись"

    данные["kb"]["auto_recognition"] = False
    cfg.save(данные, cfg.settings_path(vault))
    r = run(engine.cmd_kb_scan, text="Спросить у Василия Говнова",
            source_type=None, source_id=None)
    assert r["hypotheses"] == []


def test_min_match_length_читается_из_настроек(vault):
    """Раньше поле лежало в файле и искало ровно четыре буквы, что бы там
    ни было записано — `kb.MIN_MATCH` был зашит намертво."""
    (vault / "База").mkdir()
    (vault / "База" / "Уно.md").write_text(
        "---\ntype: note\ntitle: Уно\n---\n\nТри буквы.\n", encoding="utf-8")
    engine.migrate_kb_to_db()

    данные = cfg.defaults()
    данные["kb"]["min_match_length"] = 2
    cfg.save(данные, cfg.settings_path(vault))
    r = run(engine.cmd_kb_scan, text="Свериться с Уно", source_type=None, source_id=None)
    assert any(h["title"] == "Уно" for h in r["hypotheses"])


def test_битые_настройки_kb_не_роняют_сканирование(vault):
    (vault / "База").mkdir()
    (vault / "База" / "Уно.md").write_text(
        "---\ntype: note\ntitle: Уно\n---\n\n.\n", encoding="utf-8")
    cfg.settings_path(vault).write_text("{ не json", encoding="utf-8")
    r = run(engine.cmd_kb_scan, text="проверка", source_type=None, source_id=None)
    assert r == {"hypotheses": [], "confirmed": [], "kb_broken": []}


# --- перенос с точным временем, не только датой (блокирует пресет «через час») -

def test_parse_stored_control_понимает_дату_и_время():
    assert engine.parse_stored_control("2026-08-24") == date(2026, 8, 24)
    assert engine.parse_stored_control("2026-08-24 15:00") == datetime(2026, 8, 24, 15, 0)
    assert engine.parse_stored_control("2026-08-24T15:00:00") == datetime(2026, 8, 24, 15, 0)


def test_defer_сохраняет_время_а_не_только_дату(vault):
    """Раньше `date.fromisoformat(args.to)` падал на строке с временем —
    «перенести на конкретный час», а тем более пресет «через час», был
    в принципе недостижим."""
    т = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    r = run(engine.cmd_defer, task=т.stem, step="1", to="2026-08-25 15:00", reason="не было времени")
    assert r["ok"], r
    assert r["next_check"] == "2026-08-25 15:00"
    meta, _ = read(т)
    assert meta["steps"][0]["control_date"] == datetime(2026, 8, 25, 15, 0)


def test_notdone_с_явной_датой_сохраняет_время(vault):
    т = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    r = run(engine.cmd_notdone, task=т.stem, step="1", to="2026-08-25 09:30", reason="не было времени")
    assert r["ok"], r
    assert r["next_check"] == "2026-08-25 09:30"


def test_backlog_bulk_defer_сохраняет_время(vault):
    т = task(vault, "Грант", [step(1, "Собрать", control_date=date(2026, 8, 1))])
    r = run(engine.cmd_backlog_bulk, op="defer", items=[{"task": т.stem, "step": 1}],
            to="2026-08-25 15:00", reason="не было времени")
    assert r["ok_count"] == 1, r
    assert r["items"][0]["next_check"] == "2026-08-25 15:00"


def test_через_час_считается_от_текущего_момента_а_не_от_полуночи():
    """Пресет «через час» в окне контроля — единственное место, где нужен не
    календарный день, а настоящее «сейчас». `today` его не несёт."""
    сейчас = datetime(2026, 8, 24, 14, 10)
    assert engine.parse_date_input("через час", date(2026, 8, 24), now=сейчас) == \
        datetime(2026, 8, 24, 15, 10)
    assert engine.parse_date_input("через 2 часа", date(2026, 8, 24), now=сейчас) == \
        datetime(2026, 8, 24, 16, 10)
    assert engine.parse_date_input("через 5 часов", date(2026, 8, 24), now=сейчас) == \
        datetime(2026, 8, 24, 19, 10)


def test_через_час_без_now_остаётся_непонятой_фразой():
    """Вызовы без настоящего «сейчас» (CLI, предпросмотр шаблона) эту фразу не
    понимают — лучше явная ошибка, чем «час» посчитанный от полуночи `today`."""
    with pytest.raises(ValueError):
        engine.parse_date_input("через час", date(2026, 8, 24))


def test_через_дни_не_путается_с_через_часами():
    сейчас = datetime(2026, 8, 24, 14, 10)
    assert engine.parse_date_input("через 3 дня", date(2026, 8, 24), now=сейчас) == \
        date(2026, 8, 27)


# --- причина обязана быть словом из справочника (R16) ------------------------

def test_причина_не_из_справочника_отвергается(vault):
    """Раздел 5.4 ТЗ: причина «из справочника», а не любой текст."""
    т = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    r = run(engine.cmd_notdone, task=т.stem, step="1", reason="просто так")
    assert not r["ok"]
    assert r["errors"][0]["field"] == "reason"


def test_причина_сверяется_без_учёта_регистра(vault):
    """«Не было денег» и «не было денег» — одна причина, тем же приёмом, что
    у тегов и шаблонов."""
    т = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    r = run(engine.cmd_notdone, task=т.stem, step="1", reason="Не Было Денег")
    assert r["ok"], r


def test_архивная_причина_не_годится_для_новой_записи(vault):
    """«Больше не предлагается при выборе новой» (решение по R16) — значит и
    для CLI тоже: заархивированная причина остаётся в истории тех записей,
    где уже стояла, но новый выбор её не принимает, дропдауна с ней нет."""
    cfg.add_reason("своя причина", path=cfg.settings_path(vault))
    cfg.archive_reason("своя причина", path=cfg.settings_path(vault))
    т = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    r = run(engine.cmd_notdone, task=т.stem, step="1", reason="своя причина")
    assert not r["ok"]
    assert r["errors"][0]["field"] == "reason"


def test_defer_и_fail_тоже_проверяют_причину(vault):
    т = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    assert not run(engine.cmd_defer, task=т.stem, step="1", to="2026-09-01",
                   reason="выдумка")["ok"]
    assert not run(engine.cmd_fail, task=т.stem, step="1", reason="выдумка")["ok"]


def test_пустая_причина_не_проверяется_по_справочнику(vault):
    """Обязательна причина или нет — решает вызывающий (оболочка или argparse);
    этот справочник проверяет только то, что уже указано."""
    т = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    r = run(engine.cmd_notdone, task=т.stem, step="1", reason=None)
    assert r["ok"], r


# --- разбор завала: сортировка и CLI (R20) -----------------------------------

def test_завал_сортирован_по_давности_а_не_по_буксованию(vault):
    """Раздел 6.9 ТЗ: «сортировка по умолчанию — сначала самое давнее», без
    исключения для буксующих. Раньше буксующий элемент всплывал наверх поверх
    более старой просрочки — удобно на глаз, но противоречит ТЗ."""
    task(vault, "Свежее", [step(1, "Шаг", control_date=date(2026, 8, 14))])
    task(vault, "Буксует", [
        step(1, "Шаг", control_date=date(2026, 8, 12), log=[
            {"date": date(2026, 7, 20), "event": "not_done", "reason": "не было времени"},
            {"date": date(2026, 7, 27), "event": "not_done", "reason": "не было времени"},
            {"date": date(2026, 8, 3), "event": "not_done", "reason": "не было времени"},
        ]),
    ])
    task(vault, "Самое старое", [step(1, "Шаг", control_date=date(2026, 8, 1))])

    r = run(engine.cmd_backlog)
    имена = [i["task"] for i in r["backlog"]]
    assert имена == ["Самое старое", "Буксует", "Свежее"]


def test_backlog_bulk_принимает_json_строку_как_из_cli(vault):
    т = task(vault, "Грант", [step(1, "Собрать", control_date=date(2026, 8, 1))])
    r = run(engine.cmd_backlog_bulk, op="done",
            items=json.dumps([{"task": т.stem, "step": 1}]))
    assert r["ok_count"] == 1, r


def test_backlog_bulk_битый_json_даёт_структурную_ошибку(vault):
    r = run(engine.cmd_backlog_bulk, op="done", items="не json")
    assert r["ok"] is False
    assert r["errors"][0]["field"] == "items"


# --- заголовок до 200 символов (R27) -----------------------------------------

def test_заголовок_ровно_200_символов_проходит(vault):
    """Раздел 6.3 ТЗ: до 200 символов. Раньше здесь стояло 120 — соображение о
    вёрстке ленты, подменившее число из требования; вёрстка теперь решена
    обрезкой с многоточием в CSS, а не запретом длинного названия."""
    assert engine.MAX_TITLE == 200
    r = run(engine.cmd_create, json=json.dumps({
        "title": "О" * 200, "steps": [{"title": "Шаг"}]}))
    assert r["ok"], r


def test_заголовок_длиннее_200_отвергается(vault):
    r = run(engine.cmd_create, json=json.dumps({
        "title": "О" * 201, "steps": [{"title": "Шаг"}]}))
    assert not r["ok"]
    assert r["errors"][0]["field"] == "title"


# --- подсветка активного шага (R27) -------------------------------------------

def test_cmd_show_отмечает_активный_шаг_в_последовательной_цепочке(vault):
    """Раздел 6.3 ТЗ: «активный подсвечен цветом». Не любой незакрытый лист —
    в цепочке `status="pending"` одинаков у первого и у всех следующих, а
    спрашивать про них ещё рано. Раньше поля не было вовсе, CSS `.is-active`
    висел мёртвым правилом."""
    т = task(vault, "Ремонт", [
        step(1, "Снять колесо", control_date=TODAY),
        step(2, "Заменить подшипник", control_date=None),
        step(3, "Собрать", control_date=None),
    ])
    показ = run(engine.cmd_show, task=т.stem)
    активные = {s["id"]: s["active"] for s in показ["steps"]}
    assert активные == {1: True, 2: False, 3: False}


def test_активный_переходит_на_следующий_шаг_после_закрытия(vault):
    т = task(vault, "Ремонт", [
        step(1, "Снять колесо", control_date=TODAY),
        step(2, "Заменить подшипник", control_date=None),
    ])
    run(engine.cmd_done, task=т.stem, step="1")
    показ = run(engine.cmd_show, task=т.stem)
    активные = {s["id"]: s["active"] for s in показ["steps"]}
    assert активные == {1: False, 2: True}


def test_закрытый_шаг_никогда_не_активен(vault):
    """Даже если бы движок ошибся и вернул его в current_steps — двойное
    условие в отрисовке (`s.active && !закрыт`) на странице подстраховано, а
    в самом ответе ядра closed и active не должны противоречить друг другу."""
    т = task(vault, "Ремонт", [step(1, "Снять колесо", status="done",
                                     completed_date=TODAY)])
    показ = run(engine.cmd_show, task=т.stem)
    assert показ["steps"][0]["active"] is False
    assert показ["steps"][0]["closed"] is True


# --- «Закрыть» задачу вручную (R27) ------------------------------------------
#
# Толкование неоднозначного пункта ТЗ (кнопка в шапке карточки перечислена без
# описания действия) — интерпретация, не факт от заказчика: закрытие говорит
# «работа сделана вся разом», не проходя оставшиеся шаги по одному, парой к
# «Отменить» («работа не нужна»).

def test_close_закрывает_все_открытые_шаги_сразу(vault):
    т = task(vault, "Ремонт", [
        step(1, "Снять колесо", control_date=TODAY),
        step(2, "Заменить подшипник", control_date=None),
        step(3, "Собрать", control_date=None),
    ])
    r = run(engine.cmd_close, task=т.stem)
    assert r["ok"] and r["closed_steps"] == 3
    assert r["task_status"] == "done"
    meta, _ = read(т)
    assert all(s["status"] == "done" for s in meta["steps"])
    assert all(s["completed_date"] == TODAY for s in meta["steps"])


def test_close_пишет_в_журнал_каждого_шага(vault):
    т = task(vault, "Ремонт", [step(1, "Снять колесо", control_date=TODAY)])
    run(engine.cmd_close, task=т.stem)
    meta, _ = read(т)
    assert meta["steps"][0]["log"][-1]["event"] == "done"


def test_close_отменённую_задачу_отклоняет(vault):
    т = task(vault, "Ремонт", [step(1, "Снять колесо", control_date=TODAY)],
             cancelled=True, cancelled_reason="не актуально")
    with pytest.raises(SystemExit):
        run(engine.cmd_close, task=т.stem)


def test_close_уже_закрытой_задачи_идемпотентен(vault):
    т = task(vault, "Ремонт", [step(1, "Снять колесо", status="done",
                                     completed_date=TODAY)])
    r = run(engine.cmd_close, task=т.stem)
    assert r["ok"] and r["closed_steps"] == 0


def test_close_с_параллельной_группой_закрывает_всех_детей_разом(vault):
    т = task(vault, "Сделка", [
        step(1, "Согласовать", parent=None, mode="par"),
        step(2, "Юрист", parent=1, control_date=TODAY),
        step(3, "Бухгалтер", parent=1, control_date=TODAY),
    ])
    r = run(engine.cmd_close, task=т.stem)
    assert r["ok"] and r["closed_steps"] == 2
    assert r["task_status"] == "done"
