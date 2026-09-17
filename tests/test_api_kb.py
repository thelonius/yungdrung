#!/usr/bin/env python3
"""Тесты `/api/v1/kb/*` и `/api/v1/tasks/{id}/kb-confirm` (§1.2, Р5).

Обёртки над `engine.cmd_kb_*` — дымовые: сама морфология и защита от мусора
уже проверены в `test_kb.py`, автораспознавание из движка — в `test_engine.py`
(`kb_note`, образец фикстуры отсюда, `tests/test_engine.py:1972`). Здесь —
то, что добавляет именно HTTP-обёртка: форма ответа, `task_id` → владелец
по названию, перевод «подтверждённой ссылки» в форму гипотезы.

Роутеры монтируются в отдельное, локальное приложение FastAPI, не в общий
`api.app.app` — тот же довод, что в `test_api_tasks.py`: синглтон общий на
процесс `pytest`, и правка его маршрутов протекла бы в снимок openapi.
"""
import sys
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402
from api import errors as api_errors  # noqa: E402
from api.v1.kb import router as kb_router  # noqa: E402
from api.v1.tasks import router as tasks_router  # noqa: E402

NOW = "2026-09-08 11:00"


class _NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def _make_app() -> FastAPI:
    test_app = FastAPI()
    api_errors.install(test_app)
    test_app.include_router(tasks_router)
    test_app.include_router(kb_router)
    return test_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    monkeypatch.setattr(engine, "KB_DIR", tmp_path / "База")
    return TestClient(_make_app())


def _kb_note(vault, title, *, aliases=None):
    (vault / "База").mkdir(exist_ok=True)
    meta = {"type": "note", "title": title}
    if aliases:
        meta["aliases"] = list(aliases)
    fm = yaml.dump(meta, Dumper=_NoAliasDumper, allow_unicode=True,
                   sort_keys=False, default_flow_style=False)
    (vault / "База" / f"{title}.md").write_text(f"---\n{fm}---\n\n", encoding="utf-8")


def test_scan_без_записей_пустые_гипотезы(client):
    r = client.post("/api/v1/kb/scan", json={"text": "Позвонить Василию"})
    assert r.status_code == 200
    assert r.json() == {"hypotheses": [], "confirmed": [], "kb_broken": []}


def test_scan_находит_упоминание(client, tmp_path):
    _kb_note(tmp_path, "Василий Говнов")
    текст = "Позвонить Василию Говнову завтра"
    r = client.post("/api/v1/kb/scan", json={"text": текст})
    d = r.json()
    assert r.status_code == 200 and len(d["hypotheses"]) == 1
    г = d["hypotheses"][0]
    assert г["entry_id"] == "Василий Говнов" and г["confirmed"] is False
    assert текст[г["offset_start"]:г["offset_end"]] == г["matched"]


def test_reject_и_scan_больше_не_предлагает(client, tmp_path):
    _kb_note(tmp_path, "Василий Говнов")
    текст = "Позвонить Василию Говнову завтра"
    г = client.post("/api/v1/kb/scan", json={"text": текст}).json()["hypotheses"][0]

    r = client.post("/api/v1/kb/reject", json={"mention": г, "mute": True})
    assert r.status_code == 200 and r.json() == {"ok": True}

    снова = client.post("/api/v1/kb/scan", json={"text": текст}).json()
    assert снова["hypotheses"] == []


def test_kb_confirm_на_задаче_подчёркивает_и_возвращает_ссылку(client, tmp_path):
    _kb_note(tmp_path, "Василий Говнов")
    создана = client.post("/api/v1/tasks", params={"now": NOW}, json={
        "title": "Отдать деньги", "body": "Отдать Василию Говнову деньги",
        "steps": [{"title": "Раз", "control_date": "10.09"}]})
    tid = создана.json()["task_id"]

    текст = "Отдать Василию Говнову деньги"
    гипотеза = client.post("/api/v1/kb/scan",
                           json={"text": текст, "task_id": tid}).json()["hypotheses"][0]

    r = client.post(f"/api/v1/tasks/{tid}/kb-confirm", json={"mentions": [гипотеза]})
    d = r.json()
    assert r.status_code == 200 and d["ok"] and len(d["links"]) == 1
    assert d["links"][0]["source_id"] == "Отдать деньги"

    снова = client.post("/api/v1/kb/scan", json={"text": текст, "task_id": tid}).json()
    assert снова["hypotheses"] == [], "подтверждённое не переспрашивается"
    assert len(снова["confirmed"]) == 1
    assert снова["confirmed"][0]["title"] == "Василий Говнов"
    assert снова["confirmed"][0]["entry_id"] == "Василий Говнов"


def test_kb_confirm_чужой_задачи_404(client):
    r = client.post("/api/v1/tasks/999999/kb-confirm", json={"mentions": []})
    assert r.status_code == 404


def test_kb_confirm_ошибка_поля_в_скобочном_пути(client, tmp_path):
    """Находка ревью среза 2 (api/v1/kb.py:114): `engine.cmd_kb_confirm`
    строит путь поля точками (`mentions.0.offset_end`, Р1) — HTTP-слой
    обязан перевести его в скобки, как для `tasks`/`templates`, иначе форма
    не подсветит гипотезу с ошибкой."""
    _kb_note(tmp_path, "Василий Говнов")
    создана = client.post("/api/v1/tasks", params={"now": NOW}, json={
        "title": "Отдать деньги", "body": "Отдать Василию Говнову деньги",
        "steps": [{"title": "Раз", "control_date": "10.09"}]})
    tid = создана.json()["task_id"]

    # `offset_end < offset_start` проходит модель (оба просто `int`), но
    # `kb.validate_link` бракует запись уже после разбора — этим и ловится
    # ветка `errors[]`, не задетая штатным сканом.
    сломанная_гипотеза = {
        "entry_id": "Василий Говнов", "title": "Василий Говнов", "via": None,
        "source": None, "matched": "Василию Говнову", "offset_start": 20,
        "offset_end": 5, "confirmed": False,
    }
    r = client.post(f"/api/v1/tasks/{tid}/kb-confirm",
                    json={"mentions": [сломанная_гипотеза]})
    d = r.json()
    assert r.status_code == 200 and d["ok"] is False
    assert d["errors"][0]["field"] == "mentions[0].offset_end"
