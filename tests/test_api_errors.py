#!/usr/bin/env python3
"""Перевод путей полей на границе HTTP (`api/errors.py`, решение Р1 среза 2).

Ядро отдаёт точечные пути (`steps.1.steps.0.control_date`), клиент разбирает
только скобочные (`steps[1].steps[0].control_date`). Перевод один и живёт в
`bracket_path`; здесь проверяется сама функция и то, что обработчик
`ValidationError` применяет её к каждому полю, а `RequestValidationError`
Pydantic даёт тот же вид.
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api import errors as api_errors  # noqa: E402
from core.errors import Conflict, ValidationError  # noqa: E402


@pytest.mark.parametrize("точки, скобки", [
    ("steps.1.steps.0.control_date", "steps[1].steps[0].control_date"),
    ("tags.1", "tags[1]"),
    ("mentions.0.title", "mentions[0].title"),
    ("steps", "steps"),
    ("recurrence.anchor", "recurrence.anchor"),
    ("title", "title"),
    (None, None),
])
def test_точечный_путь_переводится_в_скобочный(точки, скобки):
    assert api_errors.bracket_path(точки) == скобки


def test_скобочный_путь_не_меняется_повторным_переводом():
    """Идемпотентность: если маршрут по ошибке переведёт путь дважды,
    клиент всё равно получит один и тот же вид."""
    assert api_errors.bracket_path("steps[1].steps[0].title") == "steps[1].steps[0].title"


@pytest.fixture
def client():
    app = FastAPI()
    api_errors.install(app)

    class Body(BaseModel):
        steps: list[dict]

    @app.post("/ядро")
    def ядро():
        raise ValidationError([
            {"field": "steps.1.steps.0.control_date", "error": "Контроль раньше начала"},
            {"field": "title", "error": "Название задачи обязательно"},
            {"field": None, "error": "запрос целиком"},
        ])

    @app.post("/конфликт")
    def конфликт():
        raise Conflict("шаг 1 уже done", actual_status="done")

    @app.post("/pydantic")
    def pydantic(body: Body):
        return {"ok": True}

    return TestClient(app)


def test_обработчик_ValidationError_переводит_каждое_поле(client):
    r = client.post("/ядро")
    assert r.status_code == 422
    assert r.json() == {"ok": False, "errors": [
        {"field": "steps[1].steps[0].control_date", "error": "Контроль раньше начала"},
        {"field": "title", "error": "Название задачи обязательно"},
        {"field": None, "error": "запрос целиком"},
    ]}


def test_CoreError_без_поля_и_с_кодом_по_типу(client):
    r = client.post("/конфликт")
    assert r.status_code == 409
    assert r.json() == {"ok": False, "errors": [{"field": None, "error": "шаг 1 уже done"}]}


def test_ошибка_Pydantic_даёт_тот_же_скобочный_вид(client):
    r = client.post("/pydantic", json={"steps": [{}, "не словарь"]})
    assert r.status_code == 422
    d = r.json()
    assert d["ok"] is False
    assert [e["field"] for e in d["errors"]] == ["steps[1]"]
