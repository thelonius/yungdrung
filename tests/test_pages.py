#!/usr/bin/env python3
"""Страницы: какой адрес отдаёт новый клиент, какой — прежнюю статику.

Писалось после того, как срез 2 сделал форму, карточку и шаблоны на React, а
`api/app.py` остался прежним: `/задача/12` отвечала 404, то есть перезагрузка
страницы роняла человека в ошибку, а `/новая` и `/шаблоны` по прямому адресу
открывали старые страницы. Приёмка этого не ловила — она ходит по приложению
клавишами, ни разу не перезагружая адрес.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import api.app as app_module  # noqa: E402
from api.app import app  # noqa: E402

РАЗДЕЛЫ_КЛИЕНТА = ["/", "/лента", "/новая", "/шаблоны", "/задача", "/задача/12"]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def сборка(tmp_path, monkeypatch):
    """Подставная сборка клиента: настоящей в git нет, её кладёт
    `tools/update.py` на машине заказчика."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>клиент</title>", encoding="utf-8")
    monkeypatch.setattr(app_module, "DIST", dist)
    return dist


@pytest.mark.parametrize("path", РАЗДЕЛЫ_КЛИЕНТА)
def test_разделы_клиента_отдают_приложение(client, сборка, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "клиент" in r.text


def test_карточка_по_любому_id_открывается(client, сборка):
    # Мусорный id — тоже страница приложения: «нет такой задачи» человек
    # прочитает в интерфейсе, а не в голом JSON от валидатора.
    assert client.get("/задача/абв").status_code == 200


@pytest.mark.parametrize("path,кусок", [
    ("/новая", "form"), ("/шаблоны", "шаблон"), ("/задача", "<!doctype"),
])
def test_без_сборки_отдаются_прежние_страницы(client, tmp_path, monkeypatch, path, кусок):
    # Трекер обязан работать до первого `tools/update.py`: сборки ещё нет,
    # а разделы должны открываться.
    monkeypatch.setattr(app_module, "DIST", tmp_path / "нет-такой")
    r = client.get(path)
    assert r.status_code == 200
    assert кусок.lower() in r.text.lower()


def test_старые_разделы_остались_статикой(client, сборка):
    # Настройки, архив, список и база знаний ещё не переехали: даже при
    # собранном клиенте они отдаются прежними страницами.
    for path in ["/старая", "/настройки", "/архив", "/задачи", "/база"]:
        r = client.get(path)
        assert r.status_code == 200
        assert "клиент" not in r.text


def test_чужой_адрес_по_прежнему_404(client, сборка):
    r = client.get("/нет-такого-раздела")
    assert r.status_code == 404
    assert r.json()["error"] == "нет такого адреса"
