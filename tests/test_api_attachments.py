#!/usr/bin/env python3
"""Тесты HTTP-слоя вложений `/api/v1` и пользовательского пути `/вложение/{id}`.

Поведение ядра покрыто в test_core_attachments.py; здесь то, за что отвечает
HTTP: multipart вместо base64, `mime` по имени файла, а не по заголовку
клиента, коды 404/422 по типу исключения, заголовки отдачи байтов и то, что
русский путь не попадает в схему.

Роутеры подключаются к своему приложению прямо в фикстуре: в `api/app.py` их
включает интегратор, а до этого легаси-маршрут `/вложение/{id_text}` перекрывал
бы новый. Приложение своё, но обработчики ошибок те же (`api.errors.install`).
"""
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import attachments  # noqa: E402
import engine  # noqa: E402
import store  # noqa: E402
import templates as tpl  # noqa: E402
from api import errors  # noqa: E402
from api.v1 import attachments as api_att  # noqa: E402

NOW = "2026-09-08 11:00"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    conn = sqlite3.connect(str(tmp_path / "стор.db"))
    store.migrate_schema(conn)
    cur = conn.execute(
        "INSERT INTO tasks (title, schema, created, start_date, cancelled, body) "
        "VALUES ('Заявка на грант', 1, '2026-09-01', '2026-09-01', 0, '')")
    tid = cur.lastrowid
    conn.executemany(
        "INSERT INTO steps (task_id, step_id, position, title, status, control_date) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        [(tid, 1, 0, "Собрать документы", "2026-09-08"), (tid, 2, 1, "Отправить", None)])
    conn.commit()
    conn.close()
    tpl.JsonStore(tmp_path).save(
        {"name": "Отчёт", "steps": [{"title": "Собрать", "offset_days": 0}]})

    app = FastAPI()
    errors.install(app)
    app.include_router(api_att.router)
    app.include_router(api_att.public)
    c = TestClient(app)
    c.tid = tid  # type: ignore[attr-defined]
    return c


def upload(client, path, name, data, content_type="text/plain", **form):
    return client.post(path, params={"now": NOW}, data=form,
                       files={"file": (name, data, content_type)})


def test_загрузка_к_задаче_mime_по_имени_а_не_по_заголовку(client):
    r = upload(client, f"/api/v1/tasks/{client.tid}/attachments", "схема.png", PNG)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    a = d["attachment"]
    assert a["mime"] == "image/png" and a["bytes"] == len(PNG)
    assert a["filename"] == "схема.png" and a["step_id"] is None
    assert a["added"] == "2026-09-08" and a["caption"] is None
    assert a["url"] == f"/вложение/{a['id']}"


def test_step_id_формой_попадает_в_ответ_и_в_список_задачи(client):
    r = upload(client, f"/api/v1/tasks/{client.tid}/attachments", "скан.pdf", b"pdf",
               step_id=2, caption=" к отправке ")
    assert r.status_code == 200, r.text
    assert r.json()["attachment"]["step_id"] == 2
    assert r.json()["attachment"]["caption"] == "к отправке"
    upload(client, f"/api/v1/tasks/{client.tid}/attachments", "схема.png", PNG)

    d = client.get(f"/api/v1/tasks/{client.tid}/attachments").json()
    assert [(a["filename"], a["step_id"]) for a in d["attachments"]] == [
        ("схема.png", None), ("скан.pdf", 2)]


def test_нет_шага_и_нет_задачи_404(client):
    r = upload(client, f"/api/v1/tasks/{client.tid}/attachments", "x.png", b"x", step_id=99)
    assert r.status_code == 404
    assert r.json() == {"ok": False, "errors": [
        {"field": None, "error": "нет шага 99 в «Заявка на грант»"}]}
    assert client.get(f"/api/v1/tasks/{client.tid + 7}/attachments").status_code == 404
    r = upload(client, f"/api/v1/tasks/{client.tid + 7}/attachments", "x.png", b"x")
    assert r.status_code == 404


def test_пустое_имя_файла_422_с_полем(client):
    r = upload(client, f"/api/v1/tasks/{client.tid}/attachments", " ", b"x")
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "filename", "error": "Нужно имя файла"}]


def test_без_файла_422_в_форме_контракта(client):
    r = client.post(f"/api/v1/tasks/{client.tid}/attachments", data={"caption": "x"})
    assert r.status_code == 422
    d = r.json()
    assert d["ok"] is False and {e["field"] for e in d["errors"]} == {"file"}


def test_слишком_большой_файл_422_поле_file(client, monkeypatch):
    """Лимит один — `attachments.MAX_BYTES`; подмена константы меняет ответ
    HTTP, второго числа в обработчике нет."""
    monkeypatch.setattr(attachments, "MAX_BYTES", 4)
    r = upload(client, f"/api/v1/tasks/{client.tid}/attachments", "big.bin", b"12345")
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "file"
    assert "МБ" in r.json()["errors"][0]["error"]


def test_шаблон_без_учёта_регистра_и_несуществующий(client):
    r = upload(client, "/api/v1/templates/отчёт/attachments", "схема.png", PNG)
    assert r.status_code == 200, r.text
    d = client.get("/api/v1/templates/ОТЧЁТ/attachments").json()
    assert [a["filename"] for a in d["attachments"]] == ["схема.png"]
    assert client.get("/api/v1/templates/Нет%20такого/attachments").status_code == 404
    r = upload(client, "/api/v1/templates/Нет такого/attachments", "x.png", b"x")
    assert r.status_code == 404 and r.json()["errors"][0]["error"] == "нет шаблона «Нет такого»"


def test_байты_inline_для_png_attachment_для_html_nosniff(client):
    png = upload(client, f"/api/v1/tasks/{client.tid}/attachments", "план.png", PNG).json()
    html = upload(client, f"/api/v1/tasks/{client.tid}/attachments", "правда.html",
                  b"<script>1</script>").json()

    r = client.get(png["attachment"]["url"])
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["content-disposition"].startswith("inline;")
    assert r.headers["x-content-type-options"] == "nosniff"

    r = client.get(html["attachment"]["url"])
    assert r.status_code == 200 and r.content == b"<script>1</script>"
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_v1_bytes_отдаёт_те_же_байты(client):
    a = upload(client, f"/api/v1/tasks/{client.tid}/attachments", "план.png", PNG).json()
    aid = a["attachment"]["id"]
    v1 = client.get(f"/api/v1/attachments/{aid}/bytes")
    ru = client.get(f"/вложение/{aid}")
    assert v1.status_code == ru.status_code == 200
    assert v1.content == ru.content == PNG
    assert v1.headers["content-disposition"] == ru.headers["content-disposition"]


def test_удаление_и_404_после(client):
    a = upload(client, f"/api/v1/tasks/{client.tid}/attachments", "план.png", PNG).json()
    aid = a["attachment"]["id"]
    r = client.delete(f"/api/v1/attachments/{aid}")
    assert r.status_code == 200 and r.json() == {"ok": True, "id": aid}
    assert client.get(f"/api/v1/tasks/{client.tid}/attachments").json()["attachments"] == []
    assert client.get(f"/вложение/{aid}").status_code == 404
    assert client.get(f"/api/v1/attachments/{aid}/bytes").status_code == 404
    r = client.delete(f"/api/v1/attachments/{aid}")
    assert r.status_code == 404
    assert r.json() == {"ok": False, "errors": [{"field": None, "error": f"нет вложения {aid}"}]}


def test_вложение_мусорный_id_404_как_в_легаси(client):
    """`/вложение/{id}` — пользовательский путь (виден в адресной строке),
    не типизированная схема: нечисловой id раньше (легаси) отвечал 404 «нет
    вложения», как любой другой несуществующий id, а не 422 валидации пути
    (находка ревью среза 2, api/v1/attachments.py:79)."""
    r = client.get("/вложение/мусор")
    assert r.status_code == 404
    assert r.json()["errors"][0]["error"] == "нет вложения"


def test_потерянный_на_диске_файл_404(client, tmp_path):
    a = upload(client, f"/api/v1/tasks/{client.tid}/attachments", "план.png", PNG).json()
    for f in attachments.dir_path(tmp_path).iterdir():
        f.unlink()
    r = client.get(a["attachment"]["url"])
    assert r.status_code == 404 and "потерян" in r.json()["errors"][0]["error"]


def test_русского_пути_нет_в_схеме_а_v1_есть(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert all(p.startswith("/api/v1/") for p in paths), sorted(paths)
    assert {"/api/v1/tasks/{task_id}/attachments", "/api/v1/templates/{name}/attachments",
            "/api/v1/attachments/{id}", "/api/v1/attachments/{id}/bytes"} <= set(paths)
    тело = paths["/api/v1/tasks/{task_id}/attachments"]["post"]["requestBody"]["content"]
    assert "multipart/form-data" in тело
