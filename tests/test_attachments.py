#!/usr/bin/env python3
"""Тесты вложений: файловый слой (attachments.py), хранилище (store.py) и
команды движка (cmd_attach/cmd_attachments/cmd_attachment_delete).

Решение записано в PLAN.md («Вложения», обсуждение 2026-08-22): байты на
диске под sha256, метаданные в стор.db, владелец — задача или шаг.
"""
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import attachments  # noqa: E402
import engine  # noqa: E402
import store  # noqa: E402

TODAY = date(2026, 8, 24)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    return tmp_path


def run(command, **fields):
    fields.setdefault("force", False)
    fields.setdefault("reason", None)
    fields.setdefault("to", None)
    fields.setdefault("step", None)
    fields.setdefault("filename", None)
    fields.setdefault("caption", None)
    fields.setdefault("data", None)
    fields.setdefault("file", None)
    return command(SimpleNamespace(**fields), TODAY)


def task(vault, name, steps=(), **fields):
    created = fields.pop("created", date(2026, 8, 1))
    start_date = fields.pop("start_date", created)
    assert not fields
    import sqlite3
    conn = sqlite3.connect(str(vault / "стор.db"))
    store.migrate_schema(conn)
    cur = conn.execute(
        "INSERT INTO tasks (title, schema, created, start_date, cancelled, body) "
        "VALUES (?, 1, ?, ?, 0, '')",
        (name, store._iso(created), store._iso(start_date)))
    task_id = cur.lastrowid
    for i, s in enumerate(steps):
        conn.execute(
            "INSERT INTO steps (task_id, step_id, position, title, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (task_id, s["id"], i, s["title"]))
    conn.commit()
    conn.close()
    return store.TaskRef(task_id, name)


# --- attachments.py: файловый слой -----------------------------------------

def test_save_кладёт_файл_под_хешем(tmp_path):
    sha256, size = attachments.save(tmp_path, b"hello", "схема.png")
    assert size == 5
    путь = attachments.locate(tmp_path, sha256)
    assert путь is not None and путь.is_file()
    assert путь.read_bytes() == b"hello"
    assert путь.suffix == ".png"


def test_save_дедуплицирует_по_содержимому(tmp_path):
    """Разное имя, то же содержимое — один файл на диске, не два. Расширение
    берёт первая запись; вторая находит его через locate() и ничего не пишет."""
    a, _ = attachments.save(tmp_path, b"same bytes", "a.png")
    b, _ = attachments.save(tmp_path, b"same bytes", "совсем-другое-имя.jpg")
    assert a == b
    assert len(list(attachments.dir_path(tmp_path).iterdir())) == 1
    assert attachments.locate(tmp_path, a).suffix == ".png"


def test_locate_не_путает_похожие_хеши(tmp_path):
    assert attachments.locate(tmp_path, "abc") is None  # нет папки вложений вовсе
    attachments.save(tmp_path, b"x", "x.png")
    assert attachments.locate(tmp_path, "нет-такого-хеша") is None


def test_save_отвергает_слишком_большой_файл(tmp_path):
    старый = attachments.MAX_BYTES
    attachments.MAX_BYTES = 10
    try:
        with pytest.raises(attachments.AttachmentError) as e:
            attachments.save(tmp_path, b"0123456789ABCDEF", "big.bin")
        assert e.value.field == "file"
    finally:
        attachments.MAX_BYTES = старый


def test_ext_режет_странные_символы():
    assert attachments._ext("схема.PNG") == ".png"
    assert attachments._ext("вирус.exe; rm -rf") == ".exermrf"[:9]  # буквы/цифры, до 8
    assert attachments._ext("без_расширения") == ""


def test_content_disposition_картинка_инлайн():
    ctype, disp = attachments.content_type_and_disposition("image/png", "план.png")
    assert ctype == "image/png"
    assert disp.startswith("inline;")


def test_content_disposition_прочее_на_скачивание():
    """Заявленный mime не решает: html/svg со скриптом внутри не должен
    получить шанс исполниться в контексте страницы."""
    ctype, disp = attachments.content_type_and_disposition("text/html", "правда.html")
    assert ctype == "application/octet-stream"
    assert disp.startswith("attachment;")


def test_content_disposition_экранирует_переносы_строк():
    _, disp = attachments.content_type_and_disposition(
        "application/pdf", "файл\r\nX-Evil: 1.pdf")
    assert "\r" not in disp and "\n" not in disp


# --- store.py: метаданные ----------------------------------------------------

def test_store_add_list_delete(tmp_path):
    s = store.Store(tmp_path / "стор.db")
    aid = s.add_attachment("task", "Грант", "abc", "план.png", "image/png",
                           123, "план участка", TODAY)
    [row] = s.list_attachments("task", "Грант")
    assert row["id"] == aid and row["filename"] == "план.png"
    assert row["caption"] == "план участка"
    assert s.list_attachments("task", "Другая") == []
    s.delete_attachment(aid)
    assert s.list_attachments("task", "Грант") == []


# --- engine.py: команды -----------------------------------------------------

def test_attach_к_задаче(vault):
    task(vault, "Грант")
    r = run(engine.cmd_attach, task="Грант", filename="план.png", data=b"png-bytes")
    assert r["ok"], r
    assert r["mime"] == "image/png"
    assert r["bytes"] == len(b"png-bytes")

    список = run(engine.cmd_attachments, task="Грант")["attachments"]
    assert len(список) == 1 and список[0]["filename"] == "план.png"


def test_attach_к_шагу(vault):
    task(vault, "Грант", [{"id": 1, "title": "Собрать"}, {"id": 2, "title": "Отправить"}])
    r = run(engine.cmd_attach, task="Грант", step=1, filename="скан.pdf", data=b"pdf")
    assert r["ok"], r

    к_шагу_1 = run(engine.cmd_attachments, task="Грант", step=1)["attachments"]
    к_шагу_2 = run(engine.cmd_attachments, task="Грант", step=2)["attachments"]
    к_задаче = run(engine.cmd_attachments, task="Грант")["attachments"]
    assert len(к_шагу_1) == 1
    assert к_шагу_2 == []
    assert к_задаче == []  # вложение шага не путается с вложением задачи


def test_attach_к_несуществующему_шагу(vault):
    task(vault, "Грант", [{"id": 1, "title": "Собрать"}])
    r = run(engine.cmd_attach, task="Грант", step=99, filename="x.png", data=b"x")
    assert not r["ok"]
    assert r["errors"][0]["field"] == "task"


def test_attach_без_имени_файла(vault):
    task(vault, "Грант")
    r = run(engine.cmd_attach, task="Грант", filename="  ", data=b"x")
    assert not r["ok"]
    assert r["errors"][0]["field"] == "filename"


def test_attach_из_файла_на_диске(vault, tmp_path):
    task(vault, "Грант")
    источник = tmp_path / "снаружи" / "схема.png"
    источник.parent.mkdir()
    источник.write_bytes(b"from disk")
    r = run(engine.cmd_attach, task="Грант", file=str(источник))
    assert r["ok"], r
    assert r["filename"] == "схема.png"  # имя взято из пути, раз не задано явно


def test_attachment_delete(vault):
    task(vault, "Грант")
    aid = run(engine.cmd_attach, task="Грант", filename="a.png", data=b"a")["id"]
    r = run(engine.cmd_attachment_delete, id=aid)
    assert r["ok"]
    assert run(engine.cmd_attachments, task="Грант")["attachments"] == []


def test_attachment_delete_не_существует(vault):
    with pytest.raises(SystemExit):
        run(engine.cmd_attachment_delete, id=999)


def test_дедуп_на_уровне_диска_переживает_две_задачи(vault):
    """Одна и та же картинка в двух задачах — одна запись в attachments()
    на каждую, но один файл на диске (attachments.save дедуплицирует по
    содержимому, а не store — двух источников правды для этого решения нет)."""
    task(vault, "Грант")
    task(vault, "Найм")
    run(engine.cmd_attach, task="Грант", filename="схема.png", data=b"same image bytes")
    run(engine.cmd_attach, task="Найм", filename="схема.png", data=b"same image bytes")
    assert len(list(attachments.dir_path(vault).iterdir())) == 1
