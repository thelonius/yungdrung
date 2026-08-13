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

КОРЕНЬ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(КОРЕНЬ))

import engine  # noqa: E402

СЕГОДНЯ = date(2026, 8, 15)
ЗАВТРА = date(2026, 8, 16)


# --- обвязка ---------------------------------------------------------------

class ДамперБезЯкорей(yaml.SafeDumper):
    """Свой, а не engine.PlainDumper: исходные файлы для тестов должны готовиться
    независимо от проверяемого кода, иначе тест на якоря проверял бы сам себя."""

    def ignore_aliases(self, data):
        return True


@pytest.fixture
def вольт(tmp_path, monkeypatch):
    """Временный вольт вместо репозитория."""
    задачи = tmp_path / "Задачи"
    задачи.mkdir()
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    monkeypatch.setattr(engine, "TASKS_DIR", задачи)
    return tmp_path


def шаг(номер, название, *, status="pending", control_date=None,
        completed_date=None, log=None):
    return {"id": номер, "title": название, "status": status,
            "control_date": control_date, "completed_date": completed_date,
            "log": log if log is not None else []}


def задача(вольт, имя, steps, *, тело="Тело заметки, его пишет заказчик.\n", **поля):
    """Кладёт файл задачи в вольт. Сводку верхнего уровня намеренно не пишем:
    её проставляет движок, а тесты проверяют, что он это делает."""
    meta = {"schema": 1, "type": "task", "title": имя, "created": date(2026, 8, 1)}
    meta.update(поля)
    meta["steps"] = steps
    fm = yaml.dump(meta, Dumper=ДамперБезЯкорей, allow_unicode=True,
                   sort_keys=False, default_flow_style=False)
    path = вольт / "Задачи" / f"{имя}.md"
    path.write_text(f"---\n{fm}---\n\n{тело}", encoding="utf-8")
    return path


def запустить(команда, today=СЕГОДНЯ, **поля):
    """Вызов команды движка без argparse."""
    поля.setdefault("force", False)
    поля.setdefault("reason", None)
    поля.setdefault("to", None)
    return команда(SimpleNamespace(**поля), today)


def прочитать(path):
    return engine.parse_file(path)


def фронтматтер(path):
    """Сырой текст frontmatter — для проверок формата, а не значений."""
    return path.read_text(encoding="utf-8").split("---", 2)[1]


def снимок(вольт):
    """Инод, время правки и содержимое каждого файла. Запись идёт через
    os.replace, то есть переписанный файл всегда меняет инод."""
    return {p.name: (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes())
            for p in sorted((вольт / "Задачи").glob("*.md"))}


# --- 1. YAML round-trip ----------------------------------------------------

def test_кругорейс_yaml_не_теряет_данные(вольт):
    """Записали → прочитали → то же самое, включая вложенный log внутри steps."""
    meta = {
        "schema": 1, "type": "task", "title": "Заявка на грант ФПГ",
        "created": date(2026, 8, 1), "status": "ждёт",
        "tags": ["гранты", "документы"],
        "steps": [
            шаг(1, "Собрать пакет документов", status="done",
                control_date=date(2026, 8, 8), completed_date=date(2026, 8, 7),
                log=[{"date": date(2026, 8, 7), "event": "done"}]),
            шаг(2, "Отправить [[Василий Говнов]] на согласование",
                control_date=date(2026, 8, 15),
                log=[{"date": date(2026, 8, 13), "event": "defer",
                      "was": date(2026, 8, 13), "to": date(2026, 8, 15),
                      "reason": "Говнов в отъезде, попросил перенести"},
                     {"date": date(2026, 8, 14), "event": "not_done",
                      "was": date(2026, 8, 14), "to": date(2026, 8, 15),
                      "reason": "не дозвонился"}]),
            шаг(3, "Дождаться решения фонда"),
        ],
    }
    тело = "Абзац заказчика со ссылкой [[Василий Говнов]].\n\nВторой абзац.\n"
    path = вольт / "Задачи" / "Заявка.md"

    engine.write_file(path, meta, тело)
    прочитано, тело_обратно = прочитать(path)

    assert прочитано == meta
    assert тело_обратно == тело
    # вложенность жива не «в целом», а поимённо: log — самое хрупкое место
    assert прочитано["steps"][1]["log"][1]["reason"] == "не дозвонился"
    assert прочитано["steps"][1]["log"][0]["to"] == date(2026, 8, 15)


def test_кругорейс_переживает_второй_проход(вольт):
    """Читаем и пишем то же самое — файл должен стать побайтово прежним."""
    path = задача(вольт, "Замена подшипника", [
        шаг(1, "Заказать подшипник [[6805]]", status="done",
            control_date=date(2026, 7, 20), completed_date=date(2026, 7, 20),
            log=[{"date": date(2026, 7, 20), "event": "done"}]),
        шаг(2, "Снять колесо", control_date=date(2026, 8, 12)),
    ])
    запустить(engine.cmd_refresh, force=True)
    первый = path.read_bytes()
    запустить(engine.cmd_refresh, force=True)
    assert path.read_bytes() == первый


def test_текст_заказчика_в_теле_не_трогаем(вольт):
    """Движок владеет только блоком между маркерами, остальное тело — заказчика.

    Блок шагов движок рендерит сам: в панели свойств Obsidian массив объектов не
    читается, и без этого блока «провалиться в задачу» упирается в JSON-строку.
    Но всё, что заказчик написал вокруг, обязано пережить любую запись.
    """
    тело = "Позвонить [[Василий Говнов]].\n\n- пункт\n- ещё пункт\n"
    path = задача(вольт, "Грант", [шаг(1, "Позвонить", control_date=СЕГОДНЯ)], тело=тело)
    запустить(engine.cmd_done, task="Грант", step="1")
    новое = прочитать(path)[1]
    assert тело.strip() in новое, "текст заказчика потерян"
    assert engine.ШАГИ_НАЧАЛО in новое and engine.ШАГИ_КОНЕЦ in новое


def test_блок_шагов_не_размножается(вольт):
    """Перерисовка заменяет блок на месте, а не дописывает ещё один."""
    path = задача(вольт, "Грант", [
        шаг(1, "Первый", control_date=СЕГОДНЯ),
        шаг(2, "Второй"),
    ], тело="Мои заметки по задаче.\n")
    for _ in range(3):
        запустить(engine.cmd_refresh, force=True)
    тело = прочитать(path)[1]
    assert тело.count(engine.ШАГИ_НАЧАЛО) == 1
    assert тело.count(engine.ШАГИ_КОНЕЦ) == 1
    assert "Мои заметки по задаче." in тело


def test_в_теле_видно_состояние_шагов(вольт):
    """Ровно то, ради чего блок и заводился: провалившись в задачу из таблицы,
    заказчик должен увидеть шаги словами, а не обрезанный JSON."""
    path = задача(вольт, "Колесо", [
        шаг(1, "Заказать [[6805]]", status="done", completed_date=date(2026, 8, 1),
            log=[{"date": date(2026, 8, 1), "event": "done"}]),
        шаг(2, "Заменить", control_date=date(2026, 8, 10),
            log=[{"date": d, "event": "not_done", "reason": "мастер в отпуске"}
                 for d in (date(2026, 8, 1), date(2026, 8, 5), date(2026, 8, 8))]),
    ])
    запустить(engine.cmd_refresh, force=True)
    тело = прочитать(path)[1]
    assert "Заказать [[6805]]" in тело, "ссылка на заметку должна попасть в тело"
    assert "сделан" in тело and "контроль" in тело
    assert "буксует" in тело, "три отметки «не сделан» должны быть видны глазами"


# --- 2. Якоря YAML ---------------------------------------------------------

def test_якорей_в_файле_нет(вольт):
    """Obsidian не разбирает `&id001`/`*id001`, файл для него ломается.

    Условие для якорей создаётся само: `done` кладёт один и тот же объект даты
    в completed_date, в log и в сводку верхнего уровня.
    """
    path = задача(вольт, "Грант", [
        шаг(1, "Собрать", control_date=СЕГОДНЯ),
        шаг(2, "Отправить"),
    ])
    запустить(engine.cmd_done, task="Грант", step="1", reason="сдали в срок")

    текст = path.read_text(encoding="utf-8")
    assert "&id" not in текст
    assert "*id" not in текст
    assert re.search(r"[&*]id\d+", текст) is None


def test_свой_дампер_а_не_везение(вольт):
    """Проверка, что предыдущий тест не пустой: обычный SafeDumper на тех же
    данных якоря как раз ставит, их убирает именно PlainDumper движка."""
    одна_дата = date(2026, 8, 15)
    meta = {"control_date": одна_дата,
            "steps": [{"id": 1, "control_date": одна_дата,
                       "log": [{"date": одна_дата, "event": "done"}]}]}

    обычный = yaml.dump(meta, Dumper=yaml.SafeDumper, sort_keys=False)
    наш = yaml.dump(meta, Dumper=engine.PlainDumper, sort_keys=False)

    assert re.search(r"[&*]id\d+", обычный), "SafeDumper перестал ставить якоря"
    assert re.search(r"[&*]id\d+", наш) is None


# --- 3. Даты — датами, не строками -----------------------------------------

def test_даты_пишутся_датами_а_не_строками(вольт):
    """Закавыченную дату Obsidian считает строкой и теряет календарь и сортировку."""
    path = задача(вольт, "Грант", [
        шаг(1, "Собрать", control_date=date(2026, 8, 10)),
        шаг(2, "Отправить"),
    ])
    запустить(engine.cmd_done, task="Грант", step="1")

    fm = фронтматтер(path)
    assert re.search(r"""['"]\d{4}-\d{2}-\d{2}['"]""", fm) is None, fm
    assert "control_date: 2026-08-15" in fm
    assert "completed_date: 2026-08-15" in fm

    meta, _ = прочитать(path)
    assert isinstance(meta["control_date"], date)
    assert isinstance(meta["steps"][0]["completed_date"], date)
    assert isinstance(meta["steps"][0]["log"][0]["date"], date)


def test_даты_датами_и_после_переноса(вольт):
    """Тот же инвариант на ветке defer: дату туда приносит аргумент-строка."""
    path = задача(вольт, "Грант", [шаг(1, "Собрать", control_date=СЕГОДНЯ)])
    запустить(engine.cmd_defer, task="Грант", step="1", to="2026-08-20")

    fm = фронтматтер(path)
    assert re.search(r"""['"]\d{4}-\d{2}-\d{2}['"]""", fm) is None, fm
    assert "control_date: 2026-08-20" in fm
    assert isinstance(прочитать(path)[0]["steps"][0]["log"][0]["to"], date)


# --- 4. done ---------------------------------------------------------------

def test_done_закрывает_шаг_и_ставит_дату(вольт):
    path = задача(вольт, "Грант", [
        шаг(1, "Собрать", control_date=СЕГОДНЯ),
        шаг(2, "Отправить", control_date=date(2026, 8, 20)),
    ])
    рез = запустить(engine.cmd_done, task="Грант", step="1", reason="всё собрал")

    assert рез["ok"] and рез["status"] == "done"
    assert рез["next_step"] == "Отправить"

    meta, _ = прочитать(path)
    первый = meta["steps"][0]
    assert первый["status"] == "done"
    assert первый["completed_date"] == СЕГОДНЯ
    assert первый["log"][-1] == {"date": СЕГОДНЯ, "event": "done", "reason": "всё собрал"}


def test_done_отдаёт_следующему_шагу_сегодняшнюю_дату(вольт):
    """Шаг без даты не всплывёт ни в одной сборке — задача молча пропадёт."""
    path = задача(вольт, "Грант", [
        шаг(1, "Собрать", control_date=СЕГОДНЯ),
        шаг(2, "Отправить"),          # даты нет
        шаг(3, "Ждать решения"),
    ])
    рез = запустить(engine.cmd_done, task="Грант", step="1")

    assert рез["date_assigned_to_step"] == 2
    assert рез["task_status"] == "due"
    meta, _ = прочитать(path)
    assert meta["steps"][1]["control_date"] == СЕГОДНЯ
    assert meta["steps"][2]["control_date"] is None   # через один не заглядываем
    assert meta["control_date"] == СЕГОДНЯ
    assert meta["current_step"] == "Отправить"

    # задача осталась видимой в сборке
    assert [v["step"] for v in запустить(engine.cmd_next)["due"]] == [2]


def test_done_не_перебивает_чужую_дату(вольт):
    """Дату ставим только если её нет: назначенную заказчиком не двигаем."""
    path = задача(вольт, "Грант", [
        шаг(1, "Собрать", control_date=СЕГОДНЯ),
        шаг(2, "Отправить", control_date=date(2026, 9, 1)),
    ])
    рез = запустить(engine.cmd_done, task="Грант", step="1")

    assert рез["date_assigned_to_step"] is None
    assert прочитать(path)[0]["steps"][1]["control_date"] == date(2026, 9, 1)


def test_done_последнего_шага_закрывает_задачу(вольт):
    path = задача(вольт, "Грант", [
        шаг(1, "Собрать", status="done", control_date=date(2026, 8, 1),
            completed_date=date(2026, 8, 1)),
        шаг(2, "Отправить", control_date=СЕГОДНЯ),
    ])
    рез = запустить(engine.cmd_done, task="Грант", step="2")

    assert рез["task_status"] == "done"
    assert рез["next_step"] is None
    meta, _ = прочитать(path)
    assert meta["status"] == "закрыта"
    assert meta["current_step"] is None
    assert meta["control_date"] is None
    assert meta["progress"] == "2/2"


# --- 5. notdone ------------------------------------------------------------

def test_notdone_оставляет_шаг_открытым(вольт):
    """Ключевое отличие от «сделан»: причина внешняя, решать всё равно надо."""
    path = задача(вольт, "Подшипник", [шаг(1, "Снять колесо", control_date=СЕГОДНЯ)])
    рез = запустить(engine.cmd_notdone, task="Подшипник", step="1",
                    reason="мастер в отпуске")

    assert рез["status"] == "pending"
    assert рез["next_check"] == "2026-08-16"
    assert рез["stalled"] == 1
    assert рез["hint"] is None

    meta, _ = прочитать(path)
    шаг1 = meta["steps"][0]
    assert шаг1["status"] == "pending"
    assert шаг1.get("completed_date") is None
    assert шаг1["control_date"] == ЗАВТРА          # по умолчанию едет на завтра
    assert шаг1["log"][-1] == {"date": СЕГОДНЯ, "event": "not_done",
                               "reason": "мастер в отпуске",
                               "was": СЕГОДНЯ, "to": ЗАВТРА}
    assert meta["status"] == "ждёт"


def test_notdone_копит_счётчик(вольт):
    """Каждая отметка ложится в log, счётчик считается по нему, а не хранится."""
    path = задача(вольт, "Подшипник", [шаг(1, "Снять колесо", control_date=СЕГОДНЯ)])

    запустить(engine.cmd_notdone, today=date(2026, 8, 15), task="Подшипник",
              step="1", reason="не было времени")
    запустить(engine.cmd_notdone, today=date(2026, 8, 16), task="Подшипник",
              step="1", reason="сервис не отвечал")

    meta, _ = прочитать(path)
    assert len(meta["steps"][0]["log"]) == 2
    assert [e["reason"] for e in meta["steps"][0]["log"]] == [
        "не было времени", "сервис не отвечал"]
    assert meta["stalled"] == 2
    assert engine.stall_count(meta["steps"][0]) == 2


def test_notdone_уважает_явную_дату(вольт):
    задача(вольт, "Подшипник", [шаг(1, "Снять колесо", control_date=СЕГОДНЯ)])
    рез = запустить(engine.cmd_notdone, task="Подшипник", step="1", to="2026-09-01")
    assert рез["next_check"] == "2026-09-01"


# --- 6. Буксование ---------------------------------------------------------

def test_три_отметки_подряд_это_буксование(вольт):
    """Четвёртый перенос подряд означает, что нужен другой ход, а не новая дата."""
    path = задача(вольт, "Подшипник", [
        шаг(1, "Снять колесо", control_date=СЕГОДНЯ, log=[
            {"date": date(2026, 8, 1), "event": "not_done", "reason": "не было времени"},
            {"date": date(2026, 8, 8), "event": "not_done", "reason": "сервис не отвечал"},
        ]),
    ])
    рез = запустить(engine.cmd_notdone, task="Подшипник", step="1",
                    reason="мастер в отпуске до сентября")

    assert рез["stalled"] == 3
    assert рез["hint"] == "шаг буксует, нужен другой ход"
    assert прочитать(path)[0]["stalled"] == 3

    # в сборке следующего дня шаг попадает в отдельный список
    сборка = запустить(engine.cmd_next, today=ЗАВТРА)
    assert [v["task"] for v in сборка["stalled"]] == ["Подшипник"]
    assert сборка["stalled"][0]["last_reason"] == "мастер в отпуске до сентября"


def test_две_отметки_ещё_не_буксование(вольт):
    задача(вольт, "Подшипник", [
        шаг(1, "Снять колесо", control_date=СЕГОДНЯ, log=[
            {"date": date(2026, 8, 1), "event": "not_done", "reason": "некогда"},
        ]),
    ])
    рез = запустить(engine.cmd_notdone, task="Подшипник", step="1")
    assert рез["stalled"] == 2 and рез["hint"] is None
    assert запустить(engine.cmd_next, today=ЗАВТРА)["stalled"] == []


def test_перенос_не_считается_буксованием(вольт):
    """`defer` выбирает заказчик — это не то же, что «опять не сделал»."""
    задача(вольт, "Грант", [
        шаг(1, "Собрать", control_date=СЕГОДНЯ, log=[
            {"date": date(2026, 8, 1), "event": "defer", "to": date(2026, 8, 8)},
            {"date": date(2026, 8, 8), "event": "defer", "to": date(2026, 8, 12)},
            {"date": date(2026, 8, 12), "event": "defer", "to": СЕГОДНЯ},
        ]),
    ])
    assert запустить(engine.cmd_next)["stalled"] == []
    assert запустить(engine.cmd_next)["due"][0]["stalled"] == 0


# --- 7. defer --------------------------------------------------------------

def test_defer_ставит_указанную_дату(вольт):
    path = задача(вольт, "Грант", [шаг(1, "Собрать", control_date=СЕГОДНЯ)])
    рез = запустить(engine.cmd_defer, task="Грант", step="1",
                    to="2026-08-20", reason="Говнов в отъезде")

    assert рез["next_check"] == "2026-08-20"
    meta, _ = прочитать(path)
    assert meta["steps"][0]["control_date"] == date(2026, 8, 20)   # не завтра
    assert meta["steps"][0]["control_date"] != ЗАВТРА
    assert meta["steps"][0]["status"] == "pending"
    assert meta["steps"][0]["log"][-1] == {
        "date": СЕГОДНЯ, "event": "defer", "reason": "Говнов в отъезде",
        "was": СЕГОДНЯ, "to": date(2026, 8, 20)}
    assert meta["status"] == "ждёт"
    assert meta["control_date"] == date(2026, 8, 20)


def test_defer_умеет_назад(вольт):
    """Перенести можно и на более раннюю дату — движок дат не оценивает."""
    path = задача(вольт, "Грант", [шаг(1, "Собрать", control_date=date(2026, 9, 1))])
    запустить(engine.cmd_defer, task="Грант", step="1", to="2026-08-10")
    meta, _ = прочитать(path)
    assert meta["steps"][0]["control_date"] == date(2026, 8, 10)
    assert meta["status"] == "просрочена"


# --- 8. Статус задачи считается из шагов ------------------------------------

def набор_статусов(вольт):
    задача(вольт, "Просрочена", [шаг(1, "Шаг", control_date=date(2026, 8, 10))])
    задача(вольт, "Сегодня", [шаг(1, "Шаг", control_date=СЕГОДНЯ)])
    задача(вольт, "Ждёт", [шаг(1, "Шаг", control_date=date(2026, 8, 20))])
    задача(вольт, "Без даты", [шаг(1, "Шаг")])
    задача(вольт, "Закрыта", [
        шаг(1, "Раз", status="done", control_date=date(2026, 8, 1),
            completed_date=date(2026, 8, 1)),
        шаг(2, "Два", status="skipped", control_date=date(2026, 8, 2)),
    ])
    задача(вольт, "Пустая", [])


def test_статус_в_json_по_английски(вольт):
    """JSON наружу — интерфейс для бота, ключи английские."""
    набор_статусов(вольт)
    статусы = {t["task"]: t["status"] for t in запустить(engine.cmd_list)["tasks"]}
    assert статусы == {
        "Просрочена": "overdue", "Сегодня": "due", "Ждёт": "waiting",
        "Без даты": "no_date", "Закрыта": "done", "Пустая": "empty",
    }


def test_статус_в_файле_по_русски(вольт):
    """Файлы читает заказчик, а не только движок."""
    набор_статусов(вольт)
    запустить(engine.cmd_refresh, force=True)
    статусы = {p.stem: прочитать(p)[0]["status"]
               for p in sorted((вольт / "Задачи").glob("*.md"))}
    assert статусы == {
        "Просрочена": "просрочена", "Сегодня": "сегодня", "Ждёт": "ждёт",
        "Без даты": "без даты", "Закрыта": "закрыта", "Пустая": "нет шагов",
    }


def test_снятый_шаг_считается_закрытым(вольт):
    """Задача закрыта, если все шаги done или skipped — не только done."""
    path = задача(вольт, "Грант", [
        шаг(1, "Раз", status="done", completed_date=date(2026, 8, 1)),
        шаг(2, "Два", control_date=СЕГОДНЯ),
    ])
    рез = запустить(engine.cmd_skip, task="Грант", step="2", reason="пошли другим путём")
    assert рез["task_status"] == "done"
    meta, _ = прочитать(path)
    assert meta["status"] == "закрыта" and meta["progress"] == "2/2"


def test_текущий_шаг_первый_незакрытый(вольт):
    задача(вольт, "Грант", [
        шаг(1, "Раз", status="done", completed_date=date(2026, 8, 1)),
        шаг(2, "Два", status="skipped"),
        шаг(3, "Три", control_date=date(2026, 8, 20)),
        шаг(4, "Четыре", control_date=date(2026, 8, 10)),
    ])
    задачи = запустить(engine.cmd_list)["tasks"]
    assert задачи[0]["current"] == "Три"          # дата четвёртого на статус не влияет
    assert задачи[0]["status"] == "waiting"
    assert задачи[0]["steps_done"] == 2 and задачи[0]["steps_total"] == 4


def test_в_сборку_попадает_только_требующее_внимания(вольт):
    """`waiting` и `done` в утренней сборке не нужны, `no_date` — нужен."""
    набор_статусов(вольт)
    сборка = запустить(engine.cmd_next)
    assert [v["task"] for v in сборка["due"]] == ["Просрочена", "Без даты", "Сегодня"]
    assert сборка["due"][0]["overdue_days"] == 5


# --- 9. refresh ------------------------------------------------------------

def test_refresh_идемпотентен(вольт):
    """Второй прогон подряд не должен трогать ни одного файла: иначе каждое утро
    холостой коммит и перезагрузка вольта в Obsidian."""
    набор_статусов(вольт)

    первый = запустить(engine.cmd_refresh)
    assert первый["count"] == 6
    assert all(t["changed"] for t in первый["written"])

    было = снимок(вольт)
    второй = запустить(engine.cmd_refresh)

    assert второй["count"] == 0
    assert второй["written"] == []
    assert снимок(вольт) == было


def test_refresh_force_переписывает_всё(вольт):
    """Путь миграции вольта при смене схемы: файлы должны быть переписаны все,
    даже те, где сводка не поменялась."""
    набор_статусов(вольт)
    запустить(engine.cmd_refresh)
    было = снимок(вольт)

    рез = запустить(engine.cmd_refresh, force=True)

    assert рез["forced"] is True and рез["count"] == 6
    assert all(t["changed"] is False for t in рез["written"])
    стало = снимок(вольт)
    # инод сменился у каждого (запись идёт через os.replace), содержимое то же
    assert all(стало[имя][0] != было[имя][0] for имя in было)
    assert all(стало[имя][2] == было[имя][2] for имя in было)


def test_refresh_идемпотентен_и_с_контрольным_временем(вольт):
    """Свойство «дата и время» из Obsidian приезжает как datetime. Сводка хранит
    только дату, и на втором прогоне это не должно выглядеть изменением."""
    path = задача(вольт, "Грант", [
        шаг(1, "Созвон", control_date=datetime(2026, 8, 20, 10, 30)),
    ])
    assert запустить(engine.cmd_refresh)["count"] == 1
    assert прочитать(path)[0]["status"] == "ждёт"
    assert isinstance(прочитать(path)[0]["steps"][0]["control_date"], datetime)

    было = снимок(вольт)
    assert запустить(engine.cmd_refresh)["count"] == 0
    assert снимок(вольт) == было


def test_refresh_проставляет_сводку_с_нуля(вольт):
    """Файл, заведённый руками без сводки, после refresh пригоден для Bases."""
    path = задача(вольт, "Грант", [
        шаг(1, "Раз", status="done", completed_date=date(2026, 8, 1)),
        шаг(2, "Два", control_date=date(2026, 8, 10), log=[
            {"date": date(2026, 8, 5), "event": "not_done", "reason": "некогда"}]),
    ])
    запустить(engine.cmd_refresh)
    meta, _ = прочитать(path)
    assert meta["schema"] == 1
    assert meta["status"] == "просрочена"
    assert meta["current_step"] == "Два"
    assert meta["control_date"] == date(2026, 8, 10)
    assert meta["progress"] == "1/2"
    assert meta["stalled"] == 1


# --- 10. Статус устаревает сам по себе -------------------------------------

def test_статус_устаревает_от_того_что_прошёл_день(вольт):
    """Тот же вольт, другой день — другой статус. Без этого утренняя сборка
    показывала бы вчерашнюю картину."""
    path = задача(вольт, "Грант", [шаг(1, "Собрать", control_date=date(2026, 8, 20))])

    запустить(engine.cmd_refresh, today=date(2026, 8, 15))
    assert прочитать(path)[0]["status"] == "ждёт"
    assert запустить(engine.cmd_next, today=date(2026, 8, 15))["due"] == []

    рез = запустить(engine.cmd_refresh, today=date(2026, 8, 20))
    assert рез["count"] == 1
    assert прочитать(path)[0]["status"] == "сегодня"

    запустить(engine.cmd_refresh, today=date(2026, 8, 25))
    assert прочитать(path)[0]["status"] == "просрочена"
    сборка = запустить(engine.cmd_next, today=date(2026, 8, 25))
    assert сборка["due"][0]["overdue_days"] == 5


# --- 11. Порядок полей -----------------------------------------------------

def test_порядок_полей_сводка_сверху_шаги_снизу(вольт):
    """В редакторе свойств Obsidian статуса не видно за простынёй шагов."""
    path = задача(вольт, "Грант", [шаг(1, "Собрать", control_date=СЕГОДНЯ)],
                  tags=["гранты"], приоритет="высокий")
    запустить(engine.cmd_refresh, force=True)

    ключи = list(прочитать(path)[0].keys())
    assert ключи[-1] == "steps"
    assert ключи[:10] == ["schema", "type", "title", "created", "status",
                          "current_step", "control_date", "progress", "stalled",
                          "tags"]
    assert ключи.index("приоритет") == 10        # чужие поля не теряются
    # порядок именно в файле, а не только в словаре
    fm = фронтматтер(path)
    assert fm.index("status:") < fm.index("steps:")
    assert fm.index("schema:") < fm.index("status:")


def test_порядок_держится_и_у_задачи_без_сводки(вольт):
    """Файл, где сводки не было вовсе, тоже приводится к порядку."""
    path = вольт / "Задачи" / "Ручная.md"
    path.write_text(
        "---\ntype: task\nsteps:\n  - id: 1\n    title: Шаг\n"
        "    control_date: 2026-08-15\ntitle: Ручная\n---\n\nТело\n",
        encoding="utf-8")
    запустить(engine.cmd_refresh)
    assert list(прочитать(path)[0].keys())[-1] == "steps"
    assert list(прочитать(path)[0].keys())[0] == "schema"


# --- Ошибки ----------------------------------------------------------------

def test_несуществующая_задача(вольт):
    задача(вольт, "Грант", [шаг(1, "Собрать", control_date=СЕГОДНЯ)])
    with pytest.raises(SystemExit) as e:
        запустить(engine.cmd_show, task="подшипник")
    assert "нет задачи" in str(e.value)


def test_неоднозначный_фрагмент_имени(вольт):
    задача(вольт, "Заявка на грант ФПГ", [шаг(1, "Собрать", control_date=СЕГОДНЯ)])
    задача(вольт, "Заявка на субсидию", [шаг(1, "Собрать", control_date=СЕГОДНЯ)])

    with pytest.raises(SystemExit) as e:
        запустить(engine.cmd_show, task="заявка")
    assert "подходит несколько" in str(e.value)
    assert "Заявка на грант ФПГ" in str(e.value)

    # однозначный фрагмент находится, регистр не важен
    assert запустить(engine.cmd_show, task="ГРАНТ")["task"] == "Заявка на грант ФПГ"


def test_несуществующий_шаг(вольт):
    задача(вольт, "Грант", [шаг(1, "Собрать", control_date=СЕГОДНЯ)])
    with pytest.raises(SystemExit) as e:
        запустить(engine.cmd_done, task="Грант", step="7")
    assert "нет шага 7" in str(e.value)


@pytest.mark.parametrize("команда", ["cmd_done", "cmd_notdone", "cmd_defer"])
def test_закрытый_шаг_повторно_не_трогается(вольт, команда):
    """Иначе отметка задним числом перепишет дату выполнения и сломает историю."""
    path = задача(вольт, "Грант", [
        шаг(1, "Собрать", status="done", control_date=date(2026, 8, 1),
            completed_date=date(2026, 8, 1),
            log=[{"date": date(2026, 8, 1), "event": "done"}]),
        шаг(2, "Отправить", control_date=СЕГОДНЯ),
    ])
    было = path.read_bytes()

    with pytest.raises(SystemExit) as e:
        запустить(getattr(engine, команда), task="Грант", step="1", to="2026-08-20")
    assert "уже done" in str(e.value)
    assert path.read_bytes() == было      # файл не тронут


def test_файл_без_frontmatter_пропускается(вольт, capsys):
    """Один битый файл не должен ронять всю сборку."""
    (вольт / "Задачи" / "Битая.md").write_text("Просто текст без шапки\n",
                                               encoding="utf-8")
    задача(вольт, "Грант", [шаг(1, "Собрать", control_date=СЕГОДНЯ)])

    задачи = запустить(engine.cmd_list)["tasks"]
    assert [t["task"] for t in задачи] == ["Грант"]
    stderr = capsys.readouterr().err
    assert "не разобран Битая.md" in stderr and "нет frontmatter" in stderr


def test_битый_yaml_пропускается(вольт, capsys):
    (вольт / "Задачи" / "Кривая.md").write_text(
        "---\ntype: task\nsteps: [1, 2\n---\n\nТело\n", encoding="utf-8")
    задача(вольт, "Грант", [шаг(1, "Собрать", control_date=СЕГОДНЯ)])

    assert [t["task"] for t in запустить(engine.cmd_list)["tasks"]] == ["Грант"]
    assert "не разобран Кривая.md" in capsys.readouterr().err


def test_битый_файл_виден_в_json_а_не_только_в_stderr(вольт):
    """Заказчик правит шаги руками, и опечатка в YAML — вопрос времени.

    Раньше такая задача молча исчезала: `tasks: []`, код возврата 0,
    предупреждение только в stderr, которого не видят ни бот, ни человек.
    Пропажа задачи из трекера и из утренней сборки без единого сигнала опаснее
    падения — падение хотя бы заметно.
    """
    (вольт / "Задачи" / "Кривая.md").write_text(
        "---\ntype: task\ntitle: [Съездить\nstatus: pending\n---\n\nТело\n",
        encoding="utf-8")
    задача(вольт, "Грант", [шаг(1, "Собрать", control_date=СЕГОДНЯ)])

    for команда in (engine.cmd_list, engine.cmd_next):
        рез = запустить(команда)
        битые = рез["broken"]
        assert [b["file"] for b in битые] == ["Кривая.md"], команда.__name__
        assert битые[0]["error"], "причина должна быть, иначе чинить вслепую"


def test_починенный_файл_уходит_из_битых(вольт):
    """Список битых пересобирается при каждом чтении, а не копится."""
    путь = вольт / "Задачи" / "Кривая.md"
    путь.write_text("---\ntype: task\ntitle: [битое\n---\n\nТело\n", encoding="utf-8")
    assert запустить(engine.cmd_list)["broken"]

    путь.write_text("---\ntype: task\ntitle: Целое\nsteps: []\n---\n\nТело\n",
                    encoding="utf-8")
    assert запустить(engine.cmd_list)["broken"] == []


def test_заметка_не_задача_игнорируется(вольт):
    """В папке задач может лежать что угодно: смотрим на type, а не на имя."""
    (вольт / "Задачи" / "Василий Говнов.md").write_text(
        "---\ntype: note\n---\n\nЗаметка базы знаний\n", encoding="utf-8")
    задача(вольт, "Грант", [шаг(1, "Собрать", control_date=СЕГОДНЯ)])
    assert [t["task"] for t in запустить(engine.cmd_list)["tasks"]] == ["Грант"]


def test_нет_папки_задач(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "TASKS_DIR", tmp_path / "Задачи")
    with pytest.raises(SystemExit) as e:
        запустить(engine.cmd_list)
    assert "нет папки задач" in str(e.value)


def test_пустой_вольт(вольт):
    assert запустить(engine.cmd_list) == {"today": "2026-08-15", "tasks": [],
                                          "broken": []}
    assert запустить(engine.cmd_next)["due"] == []
    assert запустить(engine.cmd_refresh)["count"] == 0


# --- Починенные баги --------------------------------------------------------
#
# Оба нашлись при написании тестов и были исправлены здесь же. Тесты остаются
# регрессионными: они описывают ровно то поведение, которого раньше не было.

def test_чужой_статус_шага_не_роняет_сборку(вольт):
    """Вольт правится руками, и `status: сделан` вместо `done` — вопрос времени.

    Раньше это валило list/next/refresh/export целиком: «закрыт» и «открыт»
    проверялись разными правилами и на незнакомом статусе расходились.
    """
    задача(вольт, "Кривая", [шаг(1, "Шаг", status="сделан",
                                 control_date=date(2026, 8, 10))])
    задача(вольт, "Грант", [шаг(1, "Собрать", control_date=СЕГОДНЯ)])
    задачи = запустить(engine.cmd_list)["tasks"]
    assert "Грант" in [t["task"] for t in задачи]


def test_снять_закрытый_шаг_нельзя(вольт):
    """Снятие закрытого шага оставляло бы completed_date и событие «сделан»
    рядом со статусом «снят» — запись, противоречащая сама себе."""
    задача(вольт, "Грант", [
        шаг(1, "Собрать", status="done", control_date=date(2026, 8, 1),
            completed_date=date(2026, 8, 1),
            log=[{"date": date(2026, 8, 1), "event": "done"}]),
    ])
    with pytest.raises(SystemExit):
        запустить(engine.cmd_skip, task="Грант", step="1")


# --- CLI как его увидит чужой ноутбук ---------------------------------------
#
# Отсюда и до конца — настоящий процесс: только так проверяется, что путь к
# вольту вообще берётся из YUNGDRUNG_VAULT и что argparse связан с командами.
# Подмена модульных переменных этот кусок обходит стороной.

def движок(вольт, *аргументы):
    """Движок всегда печатает UTF-8 — значит и читать его надо как UTF-8.

    Без явного encoding subprocess декодирует вывод в кодировке локали: на маке
    это UTF-8 и всё сходится случайно, а на Windows — cp1251, и «Грант»
    превращается в «Ð“Ñ€Ð°Ð½Ñ‚». Поймано настоящим прогоном на машине заказчика.
    То же правило действует для любого, кто вызывает движок и разбирает JSON.
    """
    рез = subprocess.run(
        [sys.executable, str(КОРЕНЬ / "engine.py"), *аргументы],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "YUNGDRUNG_VAULT": str(вольт)},
    )
    assert рез.returncode == 0, рез.stderr
    return json.loads(рез.stdout)


def test_cli_читает_переменную_окружения(tmp_path):
    (tmp_path / "Задачи").mkdir()
    задача(tmp_path, "Грант", [шаг(1, "Собрать", control_date=СЕГОДНЯ)])

    рез = движок(tmp_path, "--today", "2026-08-15", "list")
    assert [t["task"] for t in рез["tasks"]] == ["Грант"]
    assert рез["tasks"][0]["status"] == "due"
    # реальный вольт репозитория при этом не читается
    assert "Заявка на грант ФПГ" not in [t["task"] for t in рез["tasks"]]


def test_cli_пишет_в_свой_вольт(tmp_path):
    (tmp_path / "Задачи").mkdir()
    path = задача(tmp_path, "Грант", [
        шаг(1, "Собрать", control_date=СЕГОДНЯ),
        шаг(2, "Отправить"),
    ])
    репозиторий = снимок(КОРЕНЬ)

    рез = движок(tmp_path, "--today", "2026-08-15", "done", "грант", "1",
                 "--reason", "сдал")
    assert рез["ok"] and рез["date_assigned_to_step"] == 2

    meta, _ = прочитать(path)
    assert meta["steps"][0]["status"] == "done"
    assert meta["current_step"] == "Отправить"
    assert снимок(КОРЕНЬ) == репозиторий, "движок тронул реальные Задачи/"
