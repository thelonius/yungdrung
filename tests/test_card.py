#!/usr/bin/env python3
"""Тесты карточки задачи — R27: дата начала, правка, отмена, переоткрытие.

Тот же приём изоляции, что в test_engine.py: подменяем VAULT/TASKS_DIR через
monkeypatch, зовём функции движка напрямую в обход argparse.

Отдельным блоком — тесты на `resolve_steps`: этот путь ловил настоящий баг
при разработке. Перестановка шагов пересчитывает дефолт даты начала по новому
порядку, и старая версия (дефолт считался отдельно от проверки) молча писала
на диск шаг, где дата начала обгоняла дату контроля. Тесты ниже воспроизводят
ровно этот сценарий, а не абстрактную перестановку.
"""
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402

TODAY = date(2026, 8, 18)


class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


@pytest.fixture
def vault(tmp_path, monkeypatch):
    tasks = tmp_path / "Задачи"
    tasks.mkdir()
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    monkeypatch.setattr(engine, "TASKS_DIR", tasks)
    return tmp_path


def task(vault, name, steps, *, body="Тело заметки.\n", **fields):
    meta = {"schema": 1, "type": "task", "title": name, "created": date(2026, 8, 1)}
    meta.update(fields)
    meta["steps"] = steps
    fm = yaml.dump(meta, Dumper=NoAliasDumper, allow_unicode=True,
                   sort_keys=False, default_flow_style=False)
    path = vault / "Задачи" / f"{name}.md"
    path.write_text(f"---\n{fm}---\n\n{body}", encoding="utf-8")
    return path


def run(command, today=TODAY, **fields):
    fields.setdefault("force", False)
    fields.setdefault("reason", None)
    fields.setdefault("to", None)
    return command(SimpleNamespace(**fields), today)


def read(path):
    return engine.parse_file(path)


# --- resolve_steps: дефолт даты начала --------------------------------------

def test_первый_шаг_берёт_старт_от_задачи():
    r = engine.resolve_steps([{"title": "A", "control_date": "20.08"}],
                             date(2026, 8, 18), TODAY)
    assert r[0]["start"] == date(2026, 8, 18)
    assert r[0]["явный_старт"] is False


def test_второй_шаг_берёт_контроль_первого():
    r = engine.resolve_steps(
        [{"title": "A", "control_date": "20.08"}, {"title": "B", "control_date": "25.08"}],
        date(2026, 8, 18), TODAY)
    assert r[1]["start"] == date(2026, 8, 20)


def test_явный_старт_перебивает_дефолт():
    r = engine.resolve_steps(
        [{"title": "A", "start_date": "19.08", "control_date": "20.08"}],
        date(2026, 8, 18), TODAY)
    assert r[0]["start"] == date(2026, 8, 19)
    assert r[0]["явный_старт"] is True


def test_контроль_раньше_явного_старта_ошибка():
    r = engine.resolve_steps(
        [{"title": "A", "start_date": "25.08", "control_date": "20.08"}],
        date(2026, 8, 18), TODAY)
    assert r[0]["errors"] == [{"field": "steps.0.control_date",
                               "error": "Контроль раньше даты начала шага"}]


def test_чистая_перестановка_без_правки_дат_не_ошибается(vault):
    """Живой баг на раннем варианте фикса: перестановка шагов пересчитывала
    дефолт даты начала по новому порядку и роняла валидацию, хотя человек
    ничего в датах не менял — только перетащил строки. `resolve_steps_for_edit`
    сохраняет дату начала уже существующего шага, а не пересчитывает её."""
    задача = engine.build_task({"title": "x", "start_date": "18.08", "steps": [
        {"title": "Свериться", "control_date": "19.08"},
        {"title": "Отправить платёж", "control_date": "+3"},
    ]}, TODAY)
    task(vault, "Аренда", задача["steps"], start_date=задача["start_date"])

    payload = {
        "title": "Аренда",
        "start_date": "18.08",
        "steps": [
            {"id": 2, "title": "Отправить платёж", "control_date": "21.08"},
            {"id": 1, "title": "Свериться", "control_date": "19.08"},
        ],
    }
    файл = vault / "Задачи" / "Аренда.md"
    meta, _ = read(файл)
    assert engine.validate_task_edit(
        {"path": файл, "meta": meta}, payload, ["Аренда"], TODAY) == []


def test_перестановка_с_реальной_правкой_дат_ловит_нарушение(vault):
    """А вот если при той же перестановке ещё и подвинуть контроль второго шага
    в прошлое — это уже настоящее нарушение, и проверка обязана его поймать."""
    задача = engine.build_task({"title": "x", "start_date": "18.08", "steps": [
        {"title": "Свериться", "control_date": "19.08"},
        {"title": "Отправить платёж", "control_date": "25.08"},
    ]}, TODAY)
    task(vault, "Аренда", задача["steps"], start_date=задача["start_date"])

    payload = {
        "title": "Аренда",
        "start_date": "18.08",
        "steps": [
            {"id": 2, "title": "Отправить платёж", "control_date": "25.08",
             "start_date": "26.08"},  # явно подвинули начало ПОЗЖЕ контроля
            {"id": 1, "title": "Свериться", "control_date": "19.08"},
        ],
    }
    файл = vault / "Задачи" / "Аренда.md"
    meta, _ = read(файл)
    ошибки = engine.validate_task_edit({"path": файл, "meta": meta}, payload, ["Аренда"], TODAY)
    assert any(e["field"] == "steps.0.control_date" for e in ошибки)


# --- build_task: цепочка дефолтов -------------------------------------------

def test_build_task_старт_задачи_по_умолчанию_сегодня():
    meta = engine.build_task({"title": "T", "steps": [{"title": "A"}]}, TODAY)
    assert meta["start_date"] == TODAY


def test_build_task_note_сохраняется():
    meta = engine.build_task(
        {"title": "T", "steps": [{"title": "A", "note": "счёт у бухгалтера"}]}, TODAY)
    assert meta["steps"][0]["note"] == "счёт у бухгалтера"


def test_build_task_пустая_заметка_становится_none():
    meta = engine.build_task(
        {"title": "T", "steps": [{"title": "A", "note": "   "}]}, TODAY)
    assert meta["steps"][0]["note"] is None


# --- soft_warnings -----------------------------------------------------------

def test_явный_старт_раньше_старта_задачи_предупреждение():
    w = engine.soft_warnings(
        {"title": "T", "start_date": "20.08",
         "steps": [{"title": "A", "start_date": "18.08", "control_date": "25.08"}]}, TODAY)
    assert any(x["field"] == "steps.0.start_date" for x in w)


def test_дефолтный_старт_не_даёт_предупреждения():
    """Дефолт равен тому, с чем его сравнивают, — предупреждать не о чем."""
    w = engine.soft_warnings(
        {"title": "T", "start_date": "20.08",
         "steps": [{"title": "A", "control_date": "25.08"}]}, TODAY)
    assert w == []


def test_старт_раньше_предыдущего_контроля_предупреждение():
    w = engine.soft_warnings(
        {"title": "T", "steps": [
            {"title": "A", "control_date": "20.08"},
            {"title": "B", "start_date": "19.08", "control_date": "25.08"},
        ]}, TODAY)
    assert any("предыдущий" in x["warning"] for x in w)


# --- cmd_update: правка ------------------------------------------------------

def test_update_меняет_заголовок_шага(vault):
    task(vault, "Грант", [
        {"id": 1, "title": "Собрать", "status": "pending",
         "control_date": date(2026, 8, 20), "completed_date": None, "log": []},
    ], start_date=date(2026, 8, 18))
    r = run(engine.cmd_update, task="Грант", json='{"title":"Грант",'
           '"steps":[{"id":1,"title":"Собрать документы","control_date":"20.08"}]}')
    assert r["ok"]
    meta, _ = read(vault / "Задачи" / "Грант.md")
    assert meta["steps"][0]["title"] == "Собрать документы"


def test_update_сохраняет_статус_и_журнал_закрытого_шага(vault):
    """Правка карточки не должна трогать то, что принадлежит команде done."""
    task(vault, "Грант", [
        {"id": 1, "title": "Собрать", "status": "done",
         "control_date": date(2026, 8, 18), "completed_date": date(2026, 8, 18),
         "log": [{"date": date(2026, 8, 18), "event": "done"}]},
        {"id": 2, "title": "Отправить", "status": "pending",
         "control_date": date(2026, 8, 20), "completed_date": None, "log": []},
    ], start_date=date(2026, 8, 18))
    run(engine.cmd_update, task="Грант", json='{"title":"Грант","steps":['
       '{"id":1,"title":"Собрать документы","control_date":"18.08"},'
       '{"id":2,"title":"Отправить","control_date":"20.08"}]}')
    meta, _ = read(vault / "Задачи" / "Грант.md")
    первый = meta["steps"][0]
    assert первый["status"] == "done"
    assert первый["completed_date"] == date(2026, 8, 18)
    assert первый["log"] == [{"date": date(2026, 8, 18), "event": "done"}]


def test_update_добавляет_шаг_с_новым_id(vault):
    task(vault, "Грант", [
        {"id": 1, "title": "Собрать", "status": "pending",
         "control_date": date(2026, 8, 18), "completed_date": None, "log": []},
    ], start_date=date(2026, 8, 18))
    run(engine.cmd_update, task="Грант", json='{"title":"Грант","steps":['
       '{"id":1,"title":"Собрать","control_date":"18.08"},'
       '{"title":"Проверить приём","control_date":"22.08"}]}')
    meta, _ = read(vault / "Задачи" / "Грант.md")
    assert [s["id"] for s in meta["steps"]] == [1, 2]
    assert meta["steps"][1]["status"] == "pending"


def test_update_переупорядочивает_сохраняя_id(vault):
    """Даты у обоих шагов сохранены (реальный движок всегда их пишет), поэтому
    перестановка без правки дат обязана пройти — дефолт по новому порядку
    здесь трогать нечего, `start_date` берётся уже сохранённым."""
    task(vault, "Грант", [
        {"id": 1, "title": "Первый", "status": "pending", "start_date": date(2026, 8, 18),
         "control_date": date(2026, 8, 19), "completed_date": None, "log": []},
        {"id": 2, "title": "Второй", "status": "pending", "start_date": date(2026, 8, 19),
         "control_date": date(2026, 8, 21), "completed_date": None, "log": []},
    ], start_date=date(2026, 8, 18))
    r = run(engine.cmd_update, task="Грант", json='{"title":"Грант","steps":['
           '{"id":2,"title":"Второй","control_date":"21.08"},'
           '{"id":1,"title":"Первый","control_date":"19.08"}]}')
    assert r["ok"], r.get("errors")
    meta, _ = read(vault / "Задачи" / "Грант.md")
    assert [s["id"] for s in meta["steps"]] == [2, 1]


def test_update_без_force_отказывает_на_пропавшем_шаге(vault):
    task(vault, "Грант", [
        {"id": 1, "title": "A", "status": "pending",
         "control_date": date(2026, 8, 19), "completed_date": None, "log": []},
        {"id": 2, "title": "B", "status": "pending",
         "control_date": date(2026, 8, 21), "completed_date": None, "log": []},
    ], start_date=date(2026, 8, 18))
    r = run(engine.cmd_update, task="Грант",
           json='{"title":"Грант","steps":[{"id":1,"title":"A","control_date":"19.08"}]}')
    assert not r["ok"]
    assert any("2" in e["error"] for e in r["errors"])
    # файл не тронут
    meta, _ = read(vault / "Задачи" / "Грант.md")
    assert len(meta["steps"]) == 2


def test_update_с_force_разрешает_пропажу_шага(vault):
    task(vault, "Грант", [
        {"id": 1, "title": "A", "status": "pending",
         "control_date": date(2026, 8, 19), "completed_date": None, "log": []},
        {"id": 2, "title": "B", "status": "pending",
         "control_date": date(2026, 8, 21), "completed_date": None, "log": []},
    ], start_date=date(2026, 8, 18))
    r = run(engine.cmd_update, task="Грант", force=True,
           json='{"title":"Грант","steps":[{"id":1,"title":"A","control_date":"19.08"}]}')
    assert r["ok"]
    meta, _ = read(vault / "Задачи" / "Грант.md")
    assert len(meta["steps"]) == 1


def test_update_переименование_переносит_файл(vault):
    task(vault, "Черновик", [
        {"id": 1, "title": "A", "status": "pending",
         "control_date": date(2026, 8, 19), "completed_date": None, "log": []},
    ], start_date=date(2026, 8, 18))
    r = run(engine.cmd_update, task="Черновик", json='{"title":"Итоговое название",'
           '"steps":[{"id":1,"title":"A","control_date":"19.08"}]}')
    assert r["ok"] and r["task"] == "Итоговое название"
    assert not (vault / "Задачи" / "Черновик.md").exists()
    assert (vault / "Задачи" / "Итоговое название.md").exists()


def test_update_переименование_в_занятое_имя_отказывает(vault):
    task(vault, "Первая", [
        {"id": 1, "title": "A", "status": "pending", "control_date": date(2026, 8, 19),
         "completed_date": None, "log": []}], start_date=date(2026, 8, 18))
    task(vault, "Вторая", [
        {"id": 1, "title": "A", "status": "pending", "control_date": date(2026, 8, 19),
         "completed_date": None, "log": []}], start_date=date(2026, 8, 18))
    r = run(engine.cmd_update, task="Первая",
           json='{"title":"Вторая","steps":[{"id":1,"title":"A","control_date":"19.08"}]}')
    assert not r["ok"]
    assert (vault / "Задачи" / "Первая.md").exists()


def test_update_невалидные_даты_не_переименовывают_и_не_пишут(vault):
    """Отказ по датам должен случиться до переименования файла — иначе останется
    файл под новым именем с недописанными или противоречивыми шагами."""
    task(vault, "Аренда", [
        {"id": 1, "title": "A", "status": "pending", "control_date": date(2026, 8, 19),
         "completed_date": None, "log": []}], start_date=date(2026, 8, 18))
    r = run(engine.cmd_update, task="Аренда", json='{"title":"Аренда 2","steps":['
           '{"id":1,"title":"A","start_date":"25.08","control_date":"19.08"}]}')
    assert not r["ok"]
    assert (vault / "Задачи" / "Аренда.md").exists()
    assert not (vault / "Задачи" / "Аренда 2.md").exists()


# --- отмена, удаление, переоткрытие ------------------------------------------

def test_cancel_меняет_статус_задачи(vault):
    task(vault, "Грант", [
        {"id": 1, "title": "A", "status": "pending", "control_date": date(2026, 8, 10),
         "completed_date": None, "log": []}], start_date=date(2026, 8, 1))
    r = run(engine.cmd_cancel, task="Грант", reason="передумали")
    assert r["ok"] and r["status"] == "отменена"
    meta, _ = read(vault / "Задачи" / "Грант.md")
    assert meta["cancelled"] is True
    assert meta["cancelled_reason"] == "передумали"


def test_отменённая_задача_не_просроченная(vault):
    """Просроченный шаг есть, но задача отменена — статус не «просрочена»."""
    task(vault, "Грант", [
        {"id": 1, "title": "A", "status": "pending", "control_date": date(2026, 8, 1),
         "completed_date": None, "log": []}], start_date=date(2026, 8, 1))
    run(engine.cmd_cancel, task="Грант")
    r = run(engine.cmd_list)
    строка = next(t for t in r["tasks"] if t["task"] == "Грант")
    assert строка["status"] == "cancelled"


def test_cancel_дважды_отказывает(vault):
    task(vault, "Грант", [
        {"id": 1, "title": "A", "status": "pending", "control_date": date(2026, 8, 10),
         "completed_date": None, "log": []}], start_date=date(2026, 8, 1))
    run(engine.cmd_cancel, task="Грант")
    with pytest.raises(SystemExit):
        run(engine.cmd_cancel, task="Грант")


def test_delete_убирает_файл(vault):
    task(vault, "Грант", [
        {"id": 1, "title": "A", "status": "pending", "control_date": date(2026, 8, 10),
         "completed_date": None, "log": []}], start_date=date(2026, 8, 1))
    r = run(engine.cmd_delete, task="Грант")
    assert r["ok"] and r["deleted"]
    assert not (vault / "Задачи" / "Грант.md").exists()


def test_reopen_возвращает_шаг_в_работу(vault):
    task(vault, "Грант", [
        {"id": 1, "title": "A", "status": "done", "control_date": date(2026, 8, 5),
         "completed_date": date(2026, 8, 5),
         "log": [{"date": date(2026, 8, 5), "event": "done"}]},
    ], start_date=date(2026, 8, 1))
    r = run(engine.cmd_reopen, task="Грант", step="1")
    assert r["ok"]
    meta, _ = read(vault / "Задачи" / "Грант.md")
    шаг = meta["steps"][0]
    assert шаг["status"] == "pending"
    assert шаг["completed_date"] is None
    assert шаг["log"][-1]["event"] == "reopened"


def test_reopen_незакрытого_шага_отказывает(vault):
    task(vault, "Грант", [
        {"id": 1, "title": "A", "status": "pending", "control_date": date(2026, 8, 10),
         "completed_date": None, "log": []}], start_date=date(2026, 8, 1))
    with pytest.raises(SystemExit):
        run(engine.cmd_reopen, task="Грант", step="1")


# --- время с секундами: то, что печатает str(datetime) --------------------

@pytest.mark.parametrize("текст, ожидаем", [
    ("2026-08-18 10:00:00", engine.datetime(2026, 8, 18, 10, 0)),
    ("18.08 10:00:30", engine.datetime(2026, 8, 18, 10, 0)),
    ("10:00:00", engine.datetime(2026, 8, 18, 10, 0)),
])
def test_время_с_секундами_не_теряется(текст, ожидаем):
    """Живой баг: карточка получает control_date строкой из `cmd_show`, где
    `str(datetime(...))` печатает секунды («10:00:00»). Отправленное обратно
    без изменений значение раньше проваливалось в ISO-fallback на голую дату —
    время у уже сохранённого шага пропадало при первом же сохранении карточки,
    даже если человек его не трогал."""
    assert engine.parse_date_input(текст, TODAY) == ожидаем


def test_показ_и_обратный_разбор_даты_с_временем_совпадают(vault):
    """От записи до повторного сохранения без правок время шага не меняется —
    ровно тот путь, которым карточка каждый раз отправляет непотронутые поля."""
    task(vault, "Грант", [
        {"id": 1, "title": "A", "status": "pending",
         "control_date": engine.datetime(2026, 8, 18, 10, 0),
         "completed_date": None, "log": []},
    ], start_date=date(2026, 8, 18))

    показанное = run(engine.cmd_show, task="Грант")
    control_как_строка = показанное["steps"][0]["control_date"]

    run(engine.cmd_update, task="Грант", json=engine.json.dumps({
        "title": "Грант",
        "steps": [{"id": 1, "title": "A", "control_date": control_как_строка}],
    }))
    meta, _ = read(vault / "Задачи" / "Грант.md")
    assert meta["steps"][0]["control_date"] == engine.datetime(2026, 8, 18, 10, 0)
