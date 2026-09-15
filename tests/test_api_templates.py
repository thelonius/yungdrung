#!/usr/bin/env python3
"""Тесты `/api/v1/templates` и `/api/v1/recurrence` (`api/v1/templates.py`).

Расчёт и проверка полей покрыты `test_templates.py`, `test_recurrence.py` и
`test_core_templates.py`; здесь — то, за что отвечает именно HTTP: коды
ответов, скобочный путь поля (Р1), живые проверки, отвечающие 200 при плохом
вводе, и что круглая поездка «сохранили → прочитали → отправили как есть»
не теряет скрытые поля правила.

Роутер подключается локально, в фикстуре: `api/app.py` его ещё не видит
(интегратор подключает после мержа веток, см. задание B2), а трогать
`api/app.py` из этой ветки нельзя.
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
from api.v1 import tasks as tasks_router  # noqa: E402
from api.v1 import templates as templates_router  # noqa: E402

NOW = "2026-08-05 09:00"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    app = FastAPI()
    api_errors.install(app)
    app.include_router(templates_router.router)
    # `instantiate` зовёт `core.tasks.create_task`/`core.attachments.
    # copy_template_to_task` напрямую (после слияния веток B1/B3 обе функции
    # настоящие) — роутер задач подключён тут же, чтобы карточку заведённой
    # задачи можно было прочитать тем же клиентом (см. test_instantiate_затем_GET_задачи).
    app.include_router(tasks_router.router)
    return TestClient(app)


def шаблон(name="Отчёт", **extra):
    return {"name": name, "steps": [{"title": "Собрать", "offset_days": 0}], **extra}


# --- список и карточка -------------------------------------------------------

def test_пустой_список_шаблонов(client):
    r = client.get("/api/v1/templates")
    assert r.status_code == 200
    assert r.json() == {"templates": [], "count": 0}


def test_get_несуществующего_шаблона_404(client):
    r = client.get("/api/v1/templates/Нет такого")
    assert r.status_code == 404
    assert r.json()["ok"] is False


# --- создание, чтение, круглая поездка правки --------------------------------

def test_create_несёт_даты_строками_с_пробелом(client):
    r = client.post("/api/v1/templates", json=шаблон())
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "Отчёт"
    assert d["steps"] == [{"position": 1, "title": "Собрать",
                           "offset_days": 0, "time_of_day": None}]
    assert d["recurrence"] is None


def test_create_ошибка_по_шагу_в_скобочном_пути(client):
    r = client.post("/api/v1/templates", json=шаблон(steps=[{"title": "", "offset_days": 0}]))
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "steps[0].title"


def test_create_дубль_имени_422_по_полю_name(client):
    client.post("/api/v1/templates", json=шаблон())
    r = client.post("/api/v1/templates", json=шаблон())
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "name"


def test_put_с_другим_именем_чем_путь_422(client):
    client.post("/api/v1/templates", json=шаблон("Исходное"))
    r = client.put("/api/v1/templates/Исходное", json=шаблон("Другое"))
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "name"


def test_put_несуществующего_404(client):
    r = client.put("/api/v1/templates/Нет такого", json=шаблон("Нет такого"))
    assert r.status_code == 404


def test_круглая_поездка_put_сохраняет_скрытые_поля_правила(client):
    """Форма получает `recurrence` из `GET` и отправляет его в `PUT` как есть,
    не трогая поля без своего виджета (`holiday_shift`, `lead_days`, `until`,
    `paused`) — те обязаны доехать до второго сохранения нетронутыми."""
    создание = client.post("/api/v1/templates", json=шаблон(recurrence={
        "anchor": "2026-08-04", "freq": "weekly", "byweekday": [1],
        "holiday_shift": "before", "lead_days": 2, "paused": True,
    }))
    assert создание.status_code == 200, создание.json()

    прочитанное = client.get("/api/v1/templates/Отчёт").json()
    assert прочитанное["recurrence"]["holiday_shift"] == "before"
    assert прочитанное["recurrence"]["lead_days"] == 2
    assert прочитанное["recurrence"]["paused"] is True
    assert прочитанное["recurrence"]["byweekday"] == [1]

    # `RuleIn` собран с `extra="forbid"` (§2.2): клиент шлёт запрос по форме
    # запроса, а не карточку ответа как есть — `description` в `RuleIn` нет,
    # его добавляет `recurrence_view` только для чтения.
    правило_для_put = {k: v for k, v in прочитанное["recurrence"].items()
                       if k != "description"}
    тело = {"name": прочитанное["name"], "tags": прочитанное["tags"],
            "body": прочитанное["body"], "steps": прочитанное["steps"],
            "recurrence": правило_для_put}
    перезапись = client.put("/api/v1/templates/Отчёт", json=тело)
    assert перезапись.status_code == 200, перезапись.json()
    d = перезапись.json()
    assert d["recurrence"]["holiday_shift"] == "before"
    assert d["recurrence"]["lead_days"] == 2
    assert d["recurrence"]["paused"] is True
    assert d["recurrence"]["byweekday"] == [1]


# --- удаление ------------------------------------------------------------

def test_delete_дважды_второй_раз_404(client):
    client.post("/api/v1/templates", json=шаблон())
    первый = client.delete("/api/v1/templates/Отчёт")
    assert первый.status_code == 200
    assert первый.json() == {"ok": True, "template": "Отчёт", "deleted": True}
    второй = client.delete("/api/v1/templates/Отчёт")
    assert второй.status_code == 404


# --- предпросмотр: сохранённый и черновик ------------------------------------

def test_preview_сохранённого_404_без_шаблона(client):
    r = client.get("/api/v1/templates/Нет такого/preview")
    assert r.status_code == 404


def test_preview_сохранённого_плохая_дата_200_ok_false(client):
    client.post("/api/v1/templates", json=шаблон())
    r = client.get("/api/v1/templates/Отчёт/preview", params={"start": "чепуха"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False
    assert d["errors"][0]["field"] == "start"


def test_preview_черновика_плохой_сдвиг_200_ok_false_скобочный_путь(client):
    """Р9: живая проверка опрашивается на каждое нажатие клавиши — плохой
    сдвиг не должен рвать форму ошибкой запроса, только подсветить поле."""
    r = client.post("/api/v1/templates/preview", json={"template": шаблон(
        steps=[{"title": "A", "offset_days": 0}, {"title": "B", "offset_days": "abc"}])})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False
    assert d["errors"][0]["field"] == "steps[1].offset_days"


def test_preview_черновика_не_требует_имени(client):
    r = client.post("/api/v1/templates/preview", json={
        "template": {"name": "", "steps": [{"title": "Позвонить", "offset_days": 0}]},
        "start": "2026-08-24"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


# --- повторение: разбор текста, предпросмотр правила, PUT/DELETE ------------

def test_recurrence_parse_понятный_текст(client):
    r = client.post("/api/v1/recurrence/parse", json={"text": "каждую неделю по вторникам"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["rule"]["freq"] == "weekly"
    assert d["description"]


def test_recurrence_parse_мусор_ok_false_без_ошибки_запроса(client):
    r = client.post("/api/v1/recurrence/parse", json={"text": "тарабарщина непонятная"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_put_recurrence_без_якоря_422(client):
    client.post("/api/v1/templates", json=шаблон())
    r = client.put("/api/v1/templates/Отчёт/recurrence", json={"freq": "daily"})
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "recurrence.anchor"


def test_put_recurrence_несуществующего_шаблона_404(client):
    r = client.put("/api/v1/templates/Нет такого/recurrence",
                   json={"anchor": "2026-08-04", "freq": "daily"})
    assert r.status_code == 404


def test_put_и_delete_recurrence_на_карточке(client):
    client.post("/api/v1/templates", json=шаблон())
    установка = client.put("/api/v1/templates/Отчёт/recurrence",
                           json={"anchor": "2026-08-04", "freq": "daily"})
    assert установка.status_code == 200
    assert установка.json()["recurrence"]["freq"] == "daily"

    снятие = client.delete("/api/v1/templates/Отчёт/recurrence")
    assert снятие.status_code == 200
    assert снятие.json()["recurrence"] is None


def test_recurrence_preview_без_якоря_200_ok_false(client):
    """`POST /recurrence/preview` — живая проверка, отвечает 200 и на плохой
    ввод (как `/recurrence/parse` и предпросмотр шаблона), а не 422."""
    r = client.post("/api/v1/recurrence/preview", json={"rule": {"freq": "daily"}})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False
    assert d["errors"][0]["field"] == "recurrence.anchor"


def test_recurrence_preview_считает_ближайшие_даты(client):
    r = client.post("/api/v1/recurrence/preview",
                    json={"anchor": "2026-08-04", "rule": {"freq": "daily"}})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert len(d["preview"]) == 5
    assert d["preview"][0]["date"] == "2026-08-04"


# --- «сохранить как шаблон» из задачи ----------------------------------------

def test_from_task_несуществующей_задачи_404(client):
    r = client.post("/api/v1/templates/from-task", json={"task_id": 999})
    assert r.status_code == 404


# --- заведение задачи из шаблона ---------------------------------------------

def test_instantiate_несуществующего_шаблона_404(client):
    r = client.post("/api/v1/templates/Нет такого/instantiate", json={})
    assert r.status_code == 404


def test_instantiate_плохой_даты_422(client):
    client.post("/api/v1/templates", json=шаблон())
    r = client.post("/api/v1/templates/Отчёт/instantiate", json={"start": "чепуха"})
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "start"


def test_instantiate_затем_GET_задачи(client):
    """`task_id` из ответа `instantiate` обязан открываться карточкой той же
    задачи через уже смерженный `api/v1/tasks.py` (B1)."""
    client.post("/api/v1/templates", json=шаблон())
    r = client.post("/api/v1/templates/Отчёт/instantiate",
                    json={"title": "Отчёт за август"})
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    r2 = client.get(f"/api/v1/tasks/{task_id}")
    assert r2.status_code == 200
