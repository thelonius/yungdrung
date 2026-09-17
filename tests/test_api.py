#!/usr/bin/env python3
"""Тесты HTTP-слоя `/api/v1` через `TestClient` и дымовая проверка легаси-маршрутов.

Поведение операций покрыто в test_core_mark.py и test_engine.py; здесь — то, за
что отвечает именно HTTP: коды ответов по типу исключения, тело ошибки с
полем, разбор человеческой даты в `to`, `now` как параметр запроса, и что
легаси-маршруты живы (полная сверка со старым сервером — `tools/http_parity.py`,
итог в PROTOCOL.md от 2026-09-08).
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402
import store  # noqa: E402
from api.app import app  # noqa: E402

NOW = "2026-09-08 11:00"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    monkeypatch.setattr(engine, "KB_DIR", tmp_path / "База")
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
    c = TestClient(app)
    c.tid = tid  # type: ignore[attr-defined]
    return c


def test_лента_v1_несёт_task_id_и_считается_от_now(client):
    r = client.get("/api/v1/feed", params={"now": NOW})
    assert r.status_code == 200
    d = r.json()
    assert d["now"] == "2026-09-08T11:00:00"
    assert [x["task_id"] for x in d["feed"]] == [client.tid]
    assert d["counts"] == {"overdue": 0, "today": 1, "waiting": 0}


def test_отметка_возвращает_MarkResult_с_новым_состоянием(client):
    r = client.post(f"/api/v1/tasks/{client.tid}/steps/1/mark",
                    params={"now": NOW}, json={"op": "done"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] and d["status"] == "done" and d["dates_assigned"] == [2]
    assert d["row"] is None and d["counts"]["today"] == 1


def test_повторная_отметка_409_с_телом_контракта(client):
    client.post(f"/api/v1/tasks/{client.tid}/steps/1/mark", params={"now": NOW},
                json={"op": "done"})
    r = client.post(f"/api/v1/tasks/{client.tid}/steps/1/mark", params={"now": NOW},
                    json={"op": "done"})
    assert r.status_code == 409
    assert r.json() == {"ok": False, "errors": [{"field": None, "error": "шаг 1 уже done"}]}


def test_чужой_id_404(client):
    r = client.post(f"/api/v1/tasks/{client.tid + 7}/steps/1/mark", json={"op": "done"})
    assert r.status_code == 404
    assert r.json()["ok"] is False


@pytest.mark.parametrize("body, поле", [
    ({"op": "fail"}, "reason"),
    ({"op": "defer"}, "to"),
    ({"op": "defer", "to": "мусор", "reason": None}, "to"),
    ({"op": "взлететь"}, "op"),
])
def test_ошибки_ввода_422_с_полем(client, body, поле):
    r = client.post(f"/api/v1/tasks/{client.tid}/steps/1/mark", params={"now": NOW}, json=body)
    assert r.status_code == 422
    assert [e["field"] for e in r.json()["errors"]] == [поле]


def test_битое_тело_запроса_тоже_в_форме_контракта(client):
    r = client.post(f"/api/v1/tasks/{client.tid}/steps/1/mark", json={"reason": 5})
    assert r.status_code == 422
    d = r.json()
    assert d["ok"] is False and {e["field"] for e in d["errors"]} >= {"op"}


def test_перенос_разбирает_человеческую_дату(client):
    r = client.post(f"/api/v1/tasks/{client.tid}/steps/1/mark", params={"now": NOW},
                    json={"op": "defer", "to": "+3 15:00"})
    assert r.status_code == 200
    assert r.json()["next_check"] == "2026-09-11 15:00"
    assert r.json()["row"]["state"] == "waiting"


def test_undo_через_v1(client):
    client.post(f"/api/v1/tasks/{client.tid}/steps/1/mark", params={"now": NOW},
                json={"op": "done"})
    r = client.post(f"/api/v1/tasks/{client.tid}/steps/1/undo", params={"now": NOW})
    assert r.status_code == 200 and r.json()["undone"] == "done"
    r = client.post(f"/api/v1/tasks/{client.tid}/steps/2/undo", params={"now": NOW})
    assert r.status_code == 422


def test_parse_date_и_extract_when(client):
    r = client.post("/api/v1/parse-date", params={"now": NOW}, json={"text": "завтра в 9"})
    assert r.json() == {"ok": True, "date": "2026-09-09T09:00:00",
                        "label": "завтра, среда, в 09:00", "past": False, "error": None}
    r = client.post("/api/v1/parse-date", json={"text": "мусор"})
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["error"]
    r = client.post("/api/v1/extract-when", params={"now": NOW},
                    json={"text": "позвонить Василию завтра в полдесятого"})
    d = r.json()
    assert d["title"] == "позвонить Василию" and d["date"] == "2026-09-09T09:30:00"
    assert d["span"] == [18, 38] and d["label"] == "завтра, среда, в 09:30"


def test_search_пустой_запрос_422(client):
    r = client.get("/api/v1/search", params={"q": ""})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "text"
    assert client.get("/api/v1/search", params={"q": "грант"}).status_code == 200


def test_легаси_маршруты_живы_и_отдают_прежние_формы(client):
    d = client.get("/api/feed").json()
    assert "feed" in d and "broken" in d
    r = client.post("/api/action", json={"op": "done", "task": "грант", "step": 1})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["task_id"] == client.tid
    r = client.post("/api/create", content="{не json".encode(),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["errors"][0]["error"].startswith("битый запрос")
    assert client.get("/api/нет-такого").status_code == 404
    assert client.get("/api/нет-такого").json() == {"error": "нет такого адреса"}
    assert client.get("/лента").status_code == 200
    assert client.get("/style.css").headers["content-type"].startswith("text/css")
    assert client.get("/..%2f..%2fetc/passwd.css").status_code == 404


def test_легаси_маршрутов_нет_в_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert all(p.startswith("/api/v1/") for p in paths), sorted(paths)


def test_снимок_openapi_свежий():
    """`api/openapi.json` в git — из него `make types` собирает типы клиента.
    Поменял модель и забыл перегенерировать — этот тест скажет."""
    снимок = json.loads((ROOT / "api" / "openapi.json").read_text(encoding="utf-8"))
    assert снимок == app.openapi(), "устарел api/openapi.json: make openapi"
