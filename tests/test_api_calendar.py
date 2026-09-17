#!/usr/bin/env python3
"""Тесты `GET /api/v1/calendar` через `TestClient`.

Расчёт покрыт в `test_core_calendar.py`; здесь — то, за что отвечает HTTP:
имя параметра `from` (ключевое слово Python, в маршруте живёт под алиасом),
настройки заказчика из стора, и что плохой диапазон уходит ошибкой контракта,
а не пятисоткой.
"""
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402
import settings as cfg  # noqa: E402
import store  # noqa: E402
from api.app import app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    monkeypatch.setattr(engine, "KB_DIR", tmp_path / "База")
    conn = sqlite3.connect(str(tmp_path / "стор.db"))
    store.migrate_schema(conn)
    cur = conn.execute(
        "INSERT INTO tasks (title, schema, created, start_date, cancelled, body) "
        "VALUES ('Заявка на грант', 1, '2026-08-01', '2026-08-01', 0, '')")
    tid = cur.lastrowid
    conn.executemany(
        "INSERT INTO steps (task_id, step_id, position, title, status, control_date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(tid, 1, 0, "Собрать документы", "done", "2026-08-03"),
         (tid, 2, 1, "Отправить", "pending", "2026-08-08")])
    conn.commit()
    conn.close()
    c = TestClient(app)
    c.vault = tmp_path  # type: ignore[attr-defined]
    return c


def сетка(client, **params):
    return client.get("/api/v1/calendar", params={"from": "2026-08-03",
                                                  "to": "2026-08-09", **params})


def test_диапазон_отдаётся_построчно_с_обеими_границами(client):
    r = сетка(client)
    assert r.status_code == 200
    дни = r.json()["days"]
    assert [d["date"] for d in дни] == [f"2026-08-0{i}" for i in range(3, 10)]
    assert дни[0] == {"date": "2026-08-03", "weekend": False, "controls": 0}
    assert дни[5] == {"date": "2026-08-08", "weekend": True, "controls": 1}


def test_работа_по_выходным_снимает_пометку_с_субботы(client):
    """Настройки заказчика из стора, а не дефолты движка: включённая работа по
    выходным обязана долететь до сетки через `ctx.work()`."""
    cfg.save({"notifications": {"weekends": True}},
             cfg.settings_path(client.vault))
    дни = {d["date"]: d["weekend"] for d in сетка(client).json()["days"]}
    assert дни["2026-08-08"] is False
    assert дни["2026-08-09"] is False


def test_слишком_длинный_диапазон_даёт_422_с_полем(client):
    r = сетка(client, **{"to": "2026-12-31"})
    assert r.status_code == 422
    assert r.json() == {"ok": False, "errors": [
        {"field": "to", "error": "Диапазон длиннее 62 дней"}]}


def test_кривая_дата_даёт_422_а_не_пятисотку(client):
    r = сетка(client, **{"from": "позавчера"})
    assert r.status_code == 422
    d = r.json()
    assert d["ok"] is False and d["errors"][0]["field"] == "from"


def test_без_параметров_422_с_указанием_поля(client):
    r = client.get("/api/v1/calendar")
    assert r.status_code == 422
    assert {e["field"] for e in r.json()["errors"]} == {"from", "to"}
