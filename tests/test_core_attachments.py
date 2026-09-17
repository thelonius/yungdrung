#!/usr/bin/env python3
"""Тесты `core.attachments` напрямую, минуя адаптер `engine.py`.

Файловый слой и команды движка покрыты в test_attachments.py; здесь то, что
появилось вместе с ядром (срез 2, §3.5): владелец по `task_id`, а не по куску
названия, `NotFound` вместо `sys.exit`, список задачи вместе с шагами,
готовая ссылка на байты в каждой строке.
"""
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import attachments  # noqa: E402
import store  # noqa: E402
import templates as tpl  # noqa: E402
from core import attachments as core_att  # noqa: E402
from core import errors  # noqa: E402
from core.attachments import TaskOwner, TemplateOwner  # noqa: E402
from core.context import Context  # noqa: E402

TODAY = date(2026, 8, 24)


@pytest.fixture
def ctx(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "стор.db"))
    store.migrate_schema(conn)
    conn.close()
    return Context(tmp_path)


def task(ctx, name, steps=()):
    """Задача сырым SQL: `steps` — кортежи `(step_id, title)` или
    `(step_id, title, parent_step_id, mode)` для групп и их детей."""
    conn = sqlite3.connect(str(ctx.vault / "стор.db"))
    cur = conn.execute(
        "INSERT INTO tasks (title, schema, created, start_date, cancelled, body) "
        "VALUES (?, 1, '2026-08-01', '2026-08-01', 0, '')", (name,))
    task_id = cur.lastrowid
    for i, s in enumerate(steps):
        sid, title = s[0], s[1]
        parent = s[2] if len(s) > 2 else None
        mode = s[3] if len(s) > 3 else None
        conn.execute(
            "INSERT INTO steps (task_id, step_id, position, title, status, parent_id, mode) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)", (task_id, sid, i, title, parent, mode))
    conn.commit()
    conn.close()
    return task_id


def template(ctx, name="Отчёт"):
    tpl.JsonStore(ctx.vault).save(
        {"name": name, "steps": [{"title": "Собрать", "offset_days": 0}]})


# --- owner_key ---------------------------------------------------------------

def test_owner_key_задачи_и_шага(ctx):
    tid = task(ctx, "Грант", [(1, "Собрать"), (2, "Отправить")])
    assert core_att.owner_key(ctx, TaskOwner(tid))[:2] == ("task", "Грант")
    assert core_att.owner_key(ctx, TaskOwner(tid, 2))[:2] == ("step", "Грант:2")


def test_owner_key_группа_допустима(ctx):
    """Группа тоже может нести вложение (PLAN.md 554): проверяется
    существование шага, а не то, что он лист."""
    tid = task(ctx, "Грант", [(1, "Подготовка", None, "par"), (2, "Собрать", 1), (3, "Снять", 1)])
    assert core_att.owner_key(ctx, TaskOwner(tid, 1))[:2] == ("step", "Грант:1")


def test_owner_key_нет_задачи_и_нет_шага(ctx):
    tid = task(ctx, "Грант", [(1, "Собрать")])
    with pytest.raises(errors.NotFound):
        core_att.owner_key(ctx, TaskOwner(tid + 7))
    with pytest.raises(errors.NotFound, match="нет шага 9 в «Грант»"):
        core_att.owner_key(ctx, TaskOwner(tid, 9))


def test_owner_key_шаблон_без_учёта_регистра(ctx):
    template(ctx)
    assert core_att.owner_key(ctx, TemplateOwner("отчёт")) == ("template", "Отчёт", None)
    with pytest.raises(errors.NotFound, match="нет шаблона"):
        core_att.owner_key(ctx, TemplateOwner("Нет такого"))


# --- add ---------------------------------------------------------------------

def test_add_отдаёт_строку_со_ссылкой_и_mime_по_имени(ctx):
    tid = task(ctx, "Грант")
    a = core_att.add(ctx, TaskOwner(tid), b"png-bytes", "план.png", " схема ", TODAY)
    assert a.mime == "image/png" and a.bytes == 9 and a.caption == "схема"
    assert a.added == TODAY and a.step_id is None
    assert a.url == f"/вложение/{a.id}"


def test_add_дедуп_по_sha256_у_двух_задач(ctx):
    """Одна картинка в двух задачах — две строки, один файл на диске
    (дедуплицирует `attachments.save`, не store)."""
    a, b = task(ctx, "Грант"), task(ctx, "Найм")
    core_att.add(ctx, TaskOwner(a), b"same image", "схема.png", None, TODAY)
    core_att.add(ctx, TaskOwner(b), b"same image", "другое-имя.jpg", None, TODAY)
    assert len(list(attachments.dir_path(ctx.vault).iterdir())) == 1
    assert len(core_att.list_for(ctx, TaskOwner(a)).attachments) == 1
    assert len(core_att.list_for(ctx, TaskOwner(b)).attachments) == 1


def test_add_без_имени_файла_422(ctx):
    tid = task(ctx, "Грант")
    with pytest.raises(errors.ValidationError) as e:
        core_att.add(ctx, TaskOwner(tid), b"x", "  ", None, TODAY)
    assert e.value.errors == [{"field": "filename", "error": "Нужно имя файла"}]


def test_add_слишком_большой_файл_поле_file(ctx, monkeypatch):
    """Лимит живёт только в `attachments.save`: подмена его константы
    меняет поведение ядра, второго числа здесь нет."""
    tid = task(ctx, "Грант")
    monkeypatch.setattr(attachments, "MAX_BYTES", 4)
    with pytest.raises(errors.ValidationError) as e:
        core_att.add(ctx, TaskOwner(tid), b"12345", "big.bin", None, TODAY)
    assert e.value.errors[0]["field"] == "file"


def test_add_к_чужой_задаче_проверяет_владельца_раньше_имени(ctx):
    tid = task(ctx, "Грант")
    with pytest.raises(errors.NotFound):
        core_att.add(ctx, TaskOwner(tid + 1), b"x", "", None, TODAY)


# --- list_for ----------------------------------------------------------------

def test_list_for_задачи_отдаёт_файлы_шагов_со_step_id(ctx):
    tid = task(ctx, "Грант", [(1, "Собрать"), (2, "Отправить")])
    core_att.add(ctx, TaskOwner(tid), b"a", "задача.png", None, TODAY)
    core_att.add(ctx, TaskOwner(tid, 2), b"b", "шаг2.pdf", None, TODAY)
    core_att.add(ctx, TaskOwner(tid, 1), b"c", "шаг1.pdf", None, TODAY)

    всё = core_att.list_for(ctx, TaskOwner(tid)).attachments
    assert [(a.filename, a.step_id) for a in всё] == [
        ("задача.png", None), ("шаг1.pdf", 1), ("шаг2.pdf", 2)]

    шаг2 = core_att.list_for(ctx, TaskOwner(tid, 2)).attachments
    assert [(a.filename, a.step_id) for a in шаг2] == [("шаг2.pdf", 2)]


def test_list_for_шаблона(ctx):
    template(ctx)
    core_att.add(ctx, TemplateOwner("отчёт"), b"x", "схема.png", None, TODAY)
    [a] = core_att.list_for(ctx, TemplateOwner("ОТЧЁТ")).attachments
    assert a.filename == "схема.png" and a.step_id is None
    assert ctx.store.list_attachments("template", "Отчёт")


# --- remove, locate_bytes ----------------------------------------------------

def test_remove_и_чужой_id(ctx):
    tid = task(ctx, "Грант")
    a = core_att.add(ctx, TaskOwner(tid), b"x", "a.png", None, TODAY)
    r = core_att.remove(ctx, a.id)
    assert r.ok and r.id == a.id
    assert core_att.list_for(ctx, TaskOwner(tid)).attachments == []
    with pytest.raises(errors.NotFound, match="нет вложения 999"):
        core_att.remove(ctx, 999)


def test_locate_bytes_путь_и_заголовки(ctx):
    tid = task(ctx, "Грант")
    png = core_att.add(ctx, TaskOwner(tid), b"png", "план.png", None, TODAY)
    html = core_att.add(ctx, TaskOwner(tid), b"<b>", "правда.html", None, TODAY)
    путь, ctype, disp = core_att.locate_bytes(ctx, png.id)
    assert путь.read_bytes() == b"png" and ctype == "image/png" and disp.startswith("inline;")
    _, ctype, disp = core_att.locate_bytes(ctx, html.id)
    assert ctype == "application/octet-stream" and disp.startswith("attachment;")


def test_locate_bytes_нет_строки_и_потерян_файл(ctx):
    tid = task(ctx, "Грант")
    a = core_att.add(ctx, TaskOwner(tid), b"gone", "a.png", None, TODAY)
    with pytest.raises(errors.NotFound, match="нет вложения"):
        core_att.locate_bytes(ctx, a.id + 1)
    for f in attachments.dir_path(ctx.vault).iterdir():
        f.unlink()
    with pytest.raises(errors.NotFound, match="потерян на диске"):
        core_att.locate_bytes(ctx, a.id)


# --- copy_template_to_task ---------------------------------------------------

def test_copy_template_to_task_копирует_строки_а_не_байты(ctx):
    template(ctx)
    core_att.add(ctx, TemplateOwner("Отчёт"), b"img", "схема.png", "план", date(2026, 1, 1))
    tid = task(ctx, "Отчёт за август")

    assert core_att.copy_template_to_task(ctx, "Отчёт", "Отчёт за август", TODAY) == 1
    [a] = core_att.list_for(ctx, TaskOwner(tid)).attachments
    assert a.filename == "схема.png" and a.caption == "план" and a.added == TODAY
    # У шаблона файл остаётся, на диске он один.
    assert len(core_att.list_for(ctx, TemplateOwner("Отчёт")).attachments) == 1
    assert len(list(attachments.dir_path(ctx.vault).iterdir())) == 1
    assert core_att.copy_template_to_task(ctx, "Пустой", "Отчёт за август", TODAY) == 0
