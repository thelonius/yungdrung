#!/usr/bin/env python3
"""Тесты движка шагов.

Проверяем то, на чём держится вольт: запись YAML без порчи, вычисление статуса
из шагов, отметки шагов и идемпотентность утреннего `refresh`. Код скоро уедет
на чужой ноутбук, где чинить придётся вслепую, поэтому тесты идут по инвариантам
из CLAUDE.md и ПРОТОКОЛ.md, а не по строчкам движка.

Изоляция. Путь к вольту движок читает из `YUNGDRUNG_VAULT` один раз, на уровне
модуля (`VAULT` и `TASKS_DIR`), — менять переменную окружения после импорта
бесполезно. Подменяем сами модульные переменные через monkeypatch. Так тесты
зовут ровно те функции, что и CLI, проверяют возвращённый словарь напрямую,
а при падении показывают assert, а не разбор чужого stdout; pytest сам вернёт
переменные на место. Полный запуск через subprocess честнее к CLI, но платить
процессом за каждую проверку дорого, поэтому им закрыт только тот кусок, до
которого подмена не достаёт: чтение `YUNGDRUNG_VAULT` и связка argparse —
два теста в самом конце файла.

Реальные `Задачи/` и `База/` репозитория не участвуют: вольт создаётся заново
в tmp_path на каждый тест.
"""
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backup  # noqa: E402
import engine  # noqa: E402

TODAY = date(2026, 8, 15)
TOMORROW = date(2026, 8, 16)


# --- обвязка ---------------------------------------------------------------

class NoAliasDumper(yaml.SafeDumper):
    """Свой, а не engine.PlainDumper: исходные файлы для тестов должны готовиться
    независимо от проверяемого кода, иначе тест на якоря проверял бы сам себя."""

    def ignore_aliases(self, data):
        return True


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Временный вольт вместо репозитория."""
    tasks = tmp_path / "Задачи"
    tasks.mkdir()
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    monkeypatch.setattr(engine, "TASKS_DIR", tasks)
    return tmp_path


def step(number, title, *, status="pending", control_date=None,
        completed_date=None, log=None):
    return {"id": number, "title": title, "status": status,
            "control_date": control_date, "completed_date": completed_date,
            "log": log if log is not None else []}


def task(vault, name, steps, *, body="Тело заметки, его пишет заказчик.\n", **fields):
    """Кладёт файл задачи в вольт. Сводку верхнего уровня намеренно не пишем:
    её проставляет движок, а тесты проверяют, что он это делает."""
    meta = {"schema": 1, "type": "task", "title": name, "created": date(2026, 8, 1)}
    meta.update(fields)
    meta["steps"] = steps
    fm = yaml.dump(meta, Dumper=NoAliasDumper, allow_unicode=True,
                   sort_keys=False, default_flow_style=False)
    path = vault / "Задачи" / f"{name}.md"
    path.write_text(f"---\n{fm}---\n\n{body}", encoding="utf-8")
    return path


def run(command, today=TODAY, **fields):
    """Вызов команды движка без argparse."""
    fields.setdefault("force", False)
    fields.setdefault("reason", None)
    fields.setdefault("to", None)
    return command(SimpleNamespace(**fields), today)


def read(path):
    return engine.parse_file(path)


def frontmatter(path):
    """Сырой текст frontmatter — для проверок формата, а не значений."""
    return path.read_text(encoding="utf-8").split("---", 2)[1]


def snapshot(vault):
    """Инод, время правки и содержимое каждого файла. Запись идёт через
    os.replace, то есть переписанный файл всегда меняет инод."""
    return {p.name: (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes())
            for p in sorted((vault / "Задачи").glob("*.md"))}


# --- 1. YAML round-trip ----------------------------------------------------

def test_round_trip_yaml_keeps_data(vault):
    """Записали → прочитали → то же самое, включая вложенный log внутри steps."""
    meta = {
        "schema": 1, "type": "task", "title": "Заявка на грант ФПГ",
        "created": date(2026, 8, 1), "status": "ждёт",
        "tags": ["гранты", "документы"],
        "steps": [
            step(1, "Собрать пакет документов", status="done",
                control_date=date(2026, 8, 8), completed_date=date(2026, 8, 7),
                log=[{"date": date(2026, 8, 7), "event": "done"}]),
            step(2, "Отправить [[Василий Говнов]] на согласование",
                control_date=date(2026, 8, 15),
                log=[{"date": date(2026, 8, 13), "event": "defer",
                      "was": date(2026, 8, 13), "to": date(2026, 8, 15),
                      "reason": "Говнов в отъезде, попросил перенести"},
                     {"date": date(2026, 8, 14), "event": "not_done",
                      "was": date(2026, 8, 14), "to": date(2026, 8, 15),
                      "reason": "не дозвонился"}]),
            step(3, "Дождаться решения фонда"),
        ],
    }
    body = "Абзац заказчика со ссылкой [[Василий Говнов]].\n\nВторой абзац.\n"
    path = vault / "Задачи" / "Заявка.md"

    engine.write_file(path, meta, body)
    parsed, body_back = read(path)

    assert parsed == meta
    assert body_back == body
    # вложенность жива не «в целом», а поимённо: log — самое хрупкое место
    assert parsed["steps"][1]["log"][1]["reason"] == "не дозвонился"
    assert parsed["steps"][1]["log"][0]["to"] == date(2026, 8, 15)


def test_round_trip_survives_second_pass(vault):
    """Читаем и пишем то же самое — файл должен стать побайтово прежним."""
    path = task(vault, "Замена подшипника", [
        step(1, "Заказать подшипник [[6805]]", status="done",
            control_date=date(2026, 7, 20), completed_date=date(2026, 7, 20),
            log=[{"date": date(2026, 7, 20), "event": "done"}]),
        step(2, "Снять колесо", control_date=date(2026, 8, 12)),
    ])
    run(engine.cmd_refresh, force=True)
    first = path.read_bytes()
    run(engine.cmd_refresh, force=True)
    assert path.read_bytes() == first


def test_customer_text_in_body_untouched(vault):
    """Движок владеет только блоком между маркерами, остальное тело — заказчика.

    Блок шагов движок рендерит сам: в панели свойств Obsidian массив объектов не
    читается, и без этого блока «провалиться в задачу» упирается в JSON-строку.
    Но всё, что заказчик написал вокруг, обязано пережить любую запись.
    """
    body = "Позвонить [[Василий Говнов]].\n\n- пункт\n- ещё пункт\n"
    path = task(vault, "Грант", [step(1, "Позвонить", control_date=TODAY)], body=body)
    run(engine.cmd_done, task="Грант", step="1")
    new_body = read(path)[1]
    assert body.strip() in new_body, "текст заказчика потерян"
    assert engine.STEPS_START in new_body and engine.STEPS_END in new_body


def test_steps_block_not_duplicated(vault):
    """Перерисовка заменяет блок на месте, а не дописывает ещё один."""
    path = task(vault, "Грант", [
        step(1, "Первый", control_date=TODAY),
        step(2, "Второй"),
    ], body="Мои заметки по задаче.\n")
    for _ in range(3):
        run(engine.cmd_refresh, force=True)
    body = read(path)[1]
    assert body.count(engine.STEPS_START) == 1
    assert body.count(engine.STEPS_END) == 1
    assert "Мои заметки по задаче." in body


def test_body_shows_step_state(vault):
    """Ровно то, ради чего блок и заводился: провалившись в задачу из таблицы,
    заказчик должен увидеть шаги словами, а не обрезанный JSON."""
    path = task(vault, "Колесо", [
        step(1, "Заказать [[6805]]", status="done", completed_date=date(2026, 8, 1),
            log=[{"date": date(2026, 8, 1), "event": "done"}]),
        step(2, "Заменить", control_date=date(2026, 8, 10),
            log=[{"date": d, "event": "not_done", "reason": "мастер в отпуске"}
                 for d in (date(2026, 8, 1), date(2026, 8, 5), date(2026, 8, 8))]),
    ])
    run(engine.cmd_refresh, force=True)
    body = read(path)[1]
    assert "Заказать [[6805]]" in body, "ссылка на заметку должна попасть в тело"
    assert "сделан" in body and "контроль" in body
    assert "буксует" in body, "три отметки «не сделан» должны быть видны глазами"


# --- 2. Якоря YAML ---------------------------------------------------------

def test_no_anchors_in_file(vault):
    """Obsidian не разбирает `&id001`/`*id001`, файл для него ломается.

    Условие для якорей создаётся само: `done` кладёт один и тот же объект даты
    в completed_date, в log и в сводку верхнего уровня.
    """
    path = task(vault, "Грант", [
        step(1, "Собрать", control_date=TODAY),
        step(2, "Отправить"),
    ])
    run(engine.cmd_done, task="Грант", step="1", reason="сдали в срок")

    text = path.read_text(encoding="utf-8")
    assert "&id" not in text
    assert "*id" not in text
    assert re.search(r"[&*]id\d+", text) is None


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

def test_dates_written_as_dates_not_strings(vault):
    """Закавыченную дату Obsidian считает строкой и теряет календарь и сортировку."""
    path = task(vault, "Грант", [
        step(1, "Собрать", control_date=date(2026, 8, 10)),
        step(2, "Отправить"),
    ])
    run(engine.cmd_done, task="Грант", step="1")

    fm = frontmatter(path)
    assert re.search(r"""['"]\d{4}-\d{2}-\d{2}['"]""", fm) is None, fm
    assert "control_date: 2026-08-15" in fm
    assert "completed_date: 2026-08-15" in fm

    meta, _ = read(path)
    assert isinstance(meta["control_date"], date)
    assert isinstance(meta["steps"][0]["completed_date"], date)
    assert isinstance(meta["steps"][0]["log"][0]["date"], date)


def test_dates_as_dates_after_defer_too(vault):
    """Тот же инвариант на ветке defer: дату туда приносит аргумент-строка."""
    path = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    run(engine.cmd_defer, task="Грант", step="1", to="2026-08-20")

    fm = frontmatter(path)
    assert re.search(r"""['"]\d{4}-\d{2}-\d{2}['"]""", fm) is None, fm
    assert "control_date: 2026-08-20" in fm
    assert isinstance(read(path)[0]["steps"][0]["log"][0]["to"], date)


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
                    reason="мастер в отпуске")

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
                               "reason": "мастер в отпуске",
                               "was": TODAY, "to": TOMORROW}
    assert meta["status"] == "ждёт"


def test_notdone_accumulates_counter(vault):
    """Каждая отметка ложится в log, счётчик считается по нему, а не хранится."""
    path = task(vault, "Подшипник", [step(1, "Снять колесо", control_date=TODAY)])

    run(engine.cmd_notdone, today=date(2026, 8, 15), task="Подшипник",
              step="1", reason="не было времени")
    run(engine.cmd_notdone, today=date(2026, 8, 16), task="Подшипник",
              step="1", reason="сервис не отвечал")

    meta, _ = read(path)
    assert len(meta["steps"][0]["log"]) == 2
    assert [e["reason"] for e in meta["steps"][0]["log"]] == [
        "не было времени", "сервис не отвечал"]
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
            {"date": date(2026, 8, 8), "event": "not_done", "reason": "сервис не отвечал"},
        ]),
    ])
    result = run(engine.cmd_notdone, task="Подшипник", step="1",
                    reason="мастер в отпуске до сентября")

    assert result["stalled"] == 3
    assert result["hint"] == "шаг буксует, нужен другой ход"
    assert read(path)[0]["stalled"] == 3

    # в сборке следующего дня шаг попадает в отдельный список
    build = run(engine.cmd_next, today=TOMORROW)
    assert [v["task"] for v in build["stalled"]] == ["Подшипник"]
    assert build["stalled"][0]["last_reason"] == "мастер в отпуске до сентября"


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
                    to="2026-08-20", reason="Говнов в отъезде")

    assert result["next_check"] == "2026-08-20"
    meta, _ = read(path)
    assert meta["steps"][0]["control_date"] == date(2026, 8, 20)   # не завтра
    assert meta["steps"][0]["control_date"] != TOMORROW
    assert meta["steps"][0]["status"] == "pending"
    assert meta["steps"][0]["log"][-1] == {
        "date": TODAY, "event": "defer", "reason": "Говнов в отъезде",
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
    """Файлы читает заказчик, а не только движок."""
    status_set(vault)
    run(engine.cmd_refresh, force=True)
    statuses = {p.stem: read(p)[0]["status"]
               for p in sorted((vault / "Задачи").glob("*.md"))}
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


def test_build_includes_only_what_needs_attention(vault):
    """`waiting` и `done` в утренней сборке не нужны, `no_date` — нужен."""
    status_set(vault)
    build = run(engine.cmd_next)
    assert [v["task"] for v in build["due"]] == ["Просрочена", "Без даты", "Сегодня"]
    assert build["due"][0]["overdue_days"] == 5


# --- 9. refresh ------------------------------------------------------------

def test_refresh_is_idempotent(vault):
    """Второй прогон подряд не должен трогать ни одного файла: иначе каждое утро
    холостой коммит и перезагрузка вольта в Obsidian."""
    status_set(vault)

    first = run(engine.cmd_refresh)
    assert first["count"] == 6
    assert all(t["changed"] for t in first["written"])

    before = snapshot(vault)
    second = run(engine.cmd_refresh)

    assert second["count"] == 0
    assert second["written"] == []
    assert snapshot(vault) == before


def test_refresh_force_rewrites_everything(vault):
    """Путь миграции вольта при смене схемы: файлы должны быть переписаны все,
    даже те, где сводка не поменялась."""
    status_set(vault)
    run(engine.cmd_refresh)
    before = snapshot(vault)

    result = run(engine.cmd_refresh, force=True)

    assert result["forced"] is True and result["count"] == 6
    assert all(t["changed"] is False for t in result["written"])
    after = snapshot(vault)
    # инод сменился у каждого (запись идёт через os.replace), содержимое то же
    assert all(after[name][0] != before[name][0] for name in before)
    assert all(after[name][2] == before[name][2] for name in before)


def test_refresh_idempotent_with_control_time_too(vault):
    """Свойство «дата и время» из Obsidian приезжает как datetime. Сводка хранит
    только дату, и на втором прогоне это не должно выглядеть изменением."""
    path = task(vault, "Грант", [
        step(1, "Созвон", control_date=datetime(2026, 8, 20, 10, 30)),
    ])
    assert run(engine.cmd_refresh)["count"] == 1
    assert read(path)[0]["status"] == "ждёт"
    assert isinstance(read(path)[0]["steps"][0]["control_date"], datetime)

    before = snapshot(vault)
    assert run(engine.cmd_refresh)["count"] == 0
    assert snapshot(vault) == before


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
    """Тот же вольт, другой день — другой статус. Без этого утренняя сборка
    показывала бы вчерашнюю картину."""
    path = task(vault, "Грант", [step(1, "Собрать", control_date=date(2026, 8, 20))])

    run(engine.cmd_refresh, today=date(2026, 8, 15))
    assert read(path)[0]["status"] == "ждёт"
    assert run(engine.cmd_next, today=date(2026, 8, 15))["due"] == []

    result = run(engine.cmd_refresh, today=date(2026, 8, 20))
    assert result["count"] == 1
    assert read(path)[0]["status"] == "сегодня"

    run(engine.cmd_refresh, today=date(2026, 8, 25))
    assert read(path)[0]["status"] == "просрочена"
    build = run(engine.cmd_next, today=date(2026, 8, 25))
    assert build["due"][0]["overdue_days"] == 5


# --- 11. Порядок полей -----------------------------------------------------

def test_field_order_summary_on_top_steps_below(vault):
    """В редакторе свойств Obsidian статуса не видно за простынёй шагов."""
    path = task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)],
                  tags=["гранты"], приоритет="высокий")
    run(engine.cmd_refresh, force=True)

    keys = list(read(path)[0].keys())
    assert keys[-1] == "steps"
    assert keys[:10] == ["schema", "type", "title", "created", "status",
                          "current_step", "control_date", "progress", "stalled",
                          "tags"]
    assert keys.index("приоритет") == 10        # чужие поля не теряются
    # порядок именно в файле, а не только в словаре
    fm = frontmatter(path)
    assert fm.index("status:") < fm.index("steps:")
    assert fm.index("schema:") < fm.index("status:")


def test_order_holds_for_task_without_summary_too(vault):
    """Файл, где сводки не было вовсе, тоже приводится к порядку."""
    path = vault / "Задачи" / "Ручная.md"
    path.write_text(
        "---\ntype: task\nsteps:\n  - id: 1\n    title: Шаг\n"
        "    control_date: 2026-08-15\ntitle: Ручная\n---\n\nТело\n",
        encoding="utf-8")
    run(engine.cmd_refresh)
    assert list(read(path)[0].keys())[-1] == "steps"
    assert list(read(path)[0].keys())[0] == "schema"


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
    before = path.read_bytes()

    with pytest.raises(SystemExit) as e:
        run(getattr(engine, command), task="Грант", step="1", to="2026-08-20")
    assert "уже done" in str(e.value)
    assert path.read_bytes() == before      # файл не тронут


def test_file_without_frontmatter_is_skipped(vault, capsys):
    """Один битый файл не должен ронять всю сборку."""
    (vault / "Задачи" / "Битая.md").write_text("Просто текст без шапки\n",
                                               encoding="utf-8")
    task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])

    tasks = run(engine.cmd_list)["tasks"]
    assert [t["task"] for t in tasks] == ["Грант"]
    stderr = capsys.readouterr().err
    assert "не разобран Битая.md" in stderr and "нет frontmatter" in stderr


def test_broken_yaml_is_skipped(vault, capsys):
    (vault / "Задачи" / "Кривая.md").write_text(
        "---\ntype: task\nsteps: [1, 2\n---\n\nТело\n", encoding="utf-8")
    task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])

    assert [t["task"] for t in run(engine.cmd_list)["tasks"]] == ["Грант"]
    assert "не разобран Кривая.md" in capsys.readouterr().err


def test_broken_file_visible_in_json_not_only_stderr(vault):
    """Заказчик правит шаги руками, и опечатка в YAML — вопрос времени.

    Раньше такая задача молча исчезала: `tasks: []`, код возврата 0,
    предупреждение только в stderr, которого не видят ни бот, ни человек.
    Пропажа задачи из трекера и из утренней сборки без единого сигнала опаснее
    падения — падение хотя бы заметно.
    """
    (vault / "Задачи" / "Кривая.md").write_text(
        "---\ntype: task\ntitle: [Съездить\nstatus: pending\n---\n\nТело\n",
        encoding="utf-8")
    task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])

    for command in (engine.cmd_list, engine.cmd_next):
        result = run(command)
        broken = result["broken"]
        assert [b["file"] for b in broken] == ["Кривая.md"], command.__name__
        assert broken[0]["error"], "причина должна быть, иначе чинить вслепую"


def test_fixed_file_leaves_broken_list(vault):
    """Список битых пересобирается при каждом чтении, а не копится."""
    path = vault / "Задачи" / "Кривая.md"
    path.write_text("---\ntype: task\ntitle: [битое\n---\n\nТело\n", encoding="utf-8")
    assert run(engine.cmd_list)["broken"]

    path.write_text("---\ntype: task\ntitle: Целое\nsteps: []\n---\n\nТело\n",
                    encoding="utf-8")
    assert run(engine.cmd_list)["broken"] == []


def test_note_not_task_is_ignored(vault):
    """В папке задач может лежать что угодно: смотрим на type, а не на имя."""
    (vault / "Задачи" / "Василий Говнов.md").write_text(
        "---\ntype: note\n---\n\nЗаметка базы знаний\n", encoding="utf-8")
    task(vault, "Грант", [step(1, "Собрать", control_date=TODAY)])
    assert [t["task"] for t in run(engine.cmd_list)["tasks"]] == ["Грант"]


def test_no_tasks_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "TASKS_DIR", tmp_path / "Задачи")
    with pytest.raises(SystemExit) as e:
        run(engine.cmd_list)
    assert "нет папки задач" in str(e.value)


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
    """Вольт правится руками, и `status: сделан` вместо `done` — вопрос времени.

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
# вольту вообще берётся из YUNGDRUNG_VAULT и что argparse связан с командами.
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
    (tmp_path / "Задачи").mkdir()
    task(tmp_path, "Грант", [step(1, "Собрать", control_date=TODAY)])

    result = run_cli(tmp_path, "--today", "2026-08-15", "list")
    assert [t["task"] for t in result["tasks"]] == ["Грант"]
    assert result["tasks"][0]["status"] == "due"
    # реальный вольт репозитория при этом не читается
    assert "Заявка на грант ФПГ" not in [t["task"] for t in result["tasks"]]


def test_cli_writes_to_its_own_vault(tmp_path):
    (tmp_path / "Задачи").mkdir()
    path = task(tmp_path, "Грант", [
        step(1, "Собрать", control_date=TODAY),
        step(2, "Отправить"),
    ])
    repo = snapshot(ROOT)

    result = run_cli(tmp_path, "--today", "2026-08-15", "done", "грант", "1",
                 "--reason", "сдал")
    assert result["ok"] and result["date_assigned_to_step"] == 2

    meta, _ = read(path)
    assert meta["steps"][0]["status"] == "done"
    assert meta["current_step"] == "Отправить"
    assert snapshot(ROOT) == repo, "движок тронул реальные Задачи/"


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
    """Название задачи — это имя файла. Вольт уезжает на Windows, и задача,
    заведённая на маке, должна там открыться."""
    result = run(engine.cmd_create, json=json.dumps({
        "title": плохое, "steps": [{"title": "Шаг"}]}))
    assert not result["ok"]
    assert result["errors"][0]["field"] == "title"


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
    assert len(list((vault / "Задачи").glob("*.md"))) == 1


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
    """Заказчик вправе удалить задачу руками. Отсутствие файла считается
    закрытием цикла, а не вечной блокировкой правила."""
    месячный_шаблон(vault)
    run(engine.cmd_recur, today=date(2026, 11, 20))
    (vault / "Задачи" / "Отчёт по кассе — 05.08.2026.md").unlink()

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


# --- резервные копии и экспорт: тонкая обвязка вокруг backup.py -------------
#
# Сам backup.py и его инварианты (ротация, атомарность, целостность архива)
# проверены в test_backup.py и здесь не дублируются. Здесь — то, что относится
# к движку: путь по умолчанию, перевод BackupError в структурную ошибку
# контракта, и что restore реально возвращает файлы к прежнему состоянию.

def test_backup_копия_снимается_за_пределами_вольта(vault):
    """Раздел 9 ТЗ: пропажа папки вольта не должна утащить с собой копии."""
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


def test_restore_возвращает_вольт_к_состоянию_копии(vault):
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


def test_export_json_путь_по_умолчанию_тоже_за_пределами_вольта(vault):
    """Без явного --to движок сам не должен класть выгрузку внутрь вольта —
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
