#!/usr/bin/env python3
"""Тесты `/api/v1` для задач через `TestClient` (§1.1 спецификации среза 2).

Роутер `api/v1/tasks.py` ещё не подключён в `api/app.py` (подключит
интегратор при мерже, REFACTOR.md) — здесь он монтируется в отдельное,
локальное приложение FastAPI, а не в общий `api.app.app`: тот — модульный
синглтон, общий на весь процесс `pytest`, и `app.include_router` в фикстуре
без отмены протёк бы в другие файлы теста (в частности, испортил бы снимок
`api/openapi.json`, который сверяет `test_api.py`).
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402
from api import errors as api_errors  # noqa: E402
from api.v1.tasks import router as tasks_router  # noqa: E402

NOW = "2026-09-08 11:00"


def _make_app() -> FastAPI:
    test_app = FastAPI()
    api_errors.install(test_app)
    test_app.include_router(tasks_router)
    return test_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    monkeypatch.setattr(engine, "KB_DIR", tmp_path / "База")
    return TestClient(_make_app())


def _create(client, **over):
    body = {"title": "Заявка на грант", "steps": [
        {"title": "Собрать документы", "control_date": "10.09"},
        {"title": "Отправить", "control_date": "15.09"}]}
    body.update(over)
    return client.post("/api/v1/tasks", params={"now": NOW}, json=body)


def test_create_200_и_control_date_строкой_с_пробелом(client):
    r = client.post("/api/v1/tasks", params={"now": NOW}, json={
        "title": "Позвонить", "steps": [{"title": "Раз", "control_date": "10.09 10:00"}]})
    assert r.status_code == 200
    d = r.json()
    assert d["created"] and d["task_id"]
    assert d["card"]["steps"][0]["control_date"] == "2026-09-10 10:00"


def test_create_422_путь_ошибки_в_скобках(client):
    r = client.post("/api/v1/tasks", params={"now": NOW}, json={
        "title": "Задача", "steps": [
            {"title": "Группа", "mode": "par", "steps": [
                {"title": "Внутри", "steps": [{"title": ""}], "mode": "par",
                 "note": None}]}]})
    поля = [e["field"] for e in r.json()["errors"]]
    assert r.status_code == 422
    assert "steps[0].steps[0].steps[0].title" in поля


def test_put_без_force_422_с_force_200(client):
    tid = _create(client).json()["task_id"]
    body = {"title": "Заявка на грант", "steps": [
        {"id": 1, "title": "Собрать документы", "control_date": "10.09"}]}
    r = client.put(f"/api/v1/tasks/{tid}", params={"now": NOW}, json=body)
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "steps"

    r2 = client.put(f"/api/v1/tasks/{tid}", params={"now": NOW},
                    json={**body, "force": True})
    assert r2.status_code == 200 and r2.json()["card"]["steps"] and len(
        r2.json()["card"]["steps"]) == 1


def test_put_дата_возвращается_нетронутой(client):
    tid = _create(client).json()["task_id"]
    card = client.get(f"/api/v1/tasks/{tid}", params={"now": NOW}).json()
    шаг = card["steps"][0]
    r = client.put(f"/api/v1/tasks/{tid}", params={"now": NOW}, json={
        "title": card["task"], "force": True,
        "steps": [{"id": s["id"], "title": s["title"], "control_date": s["control_date"]}
                  for s in card["steps"]]})
    assert r.status_code == 200
    assert r.json()["card"]["steps"][0]["control_date"] == шаг["control_date"]


def test_cancel_дважды_409(client):
    tid = _create(client).json()["task_id"]
    r1 = client.post(f"/api/v1/tasks/{tid}/cancel", json={})
    assert r1.status_code == 200 and r1.json()["cancelled"]
    r2 = client.post(f"/api/v1/tasks/{tid}/cancel", json={})
    assert r2.status_code == 409


def test_close_отменённой_409(client):
    tid = _create(client).json()["task_id"]
    client.post(f"/api/v1/tasks/{tid}/cancel", json={})
    r = client.post(f"/api/v1/tasks/{tid}/close", params={"now": NOW})
    assert r.status_code == 409


def test_reopen_группы_422(client):
    r = client.post("/api/v1/tasks", params={"now": NOW}, json={
        "title": "Сделка", "steps": [{"title": "Подписи", "mode": "par", "steps": [
            {"title": "Арендодатель", "control_date": "10.09"}]}]})
    tid = r.json()["task_id"]
    группа_id = r.json()["card"]["steps"][0]["id"]
    rr = client.post(f"/api/v1/tasks/{tid}/steps/{группа_id}/reopen", params={"now": NOW})
    assert rr.status_code == 422


def test_delete_404_на_повторном_get(client):
    tid = _create(client).json()["task_id"]
    r = client.delete(f"/api/v1/tasks/{tid}")
    assert r.status_code == 200 and r.json()["deleted"]
    assert client.get(f"/api/v1/tasks/{tid}").status_code == 404


def test_plan_200_при_ошибке_даты(client):
    r = client.post("/api/v1/tasks/plan", json={
        "steps": [{"title": "A", "control_date": "тарабарщина"}]})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["errors"][0]["field"] == "steps[0].control_date"


def test_resolve_404_на_чужом_названии(client):
    _create(client)
    assert client.get("/api/v1/tasks/resolve",
                      params={"title": "Заявка на грант"}).status_code == 200
    r = client.get("/api/v1/tasks/resolve", params={"title": "грант"})
    assert r.status_code == 404


def test_quick_422_на_пустом_тексте(client):
    r = client.post("/api/v1/tasks/quick", params={"now": NOW}, json={"text": "  "})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "text"
