#!/usr/bin/env python3
"""Golden-тест пути «лента → отметка шага» — сетка безопасности среза 1a.

Снимок снят на коде **до** переноса ленты и отметок в `core/` (REFACTOR.md,
срез 1a). Смысл теста один: перенос не имеет права поменять ни одного байта в
ответах движка, кроме полей, добавленных осознанно и перечисленных в
`NEW_FIELDS`. Существующие тесты проверяют инварианты по отдельности; этот
проверяет форму ответа целиком, включая порядок строк, подписи и тексты
ошибок, которые ни один инвариант не стережёт.

Стор здесь свой, маленький и заведомо разнообразный: просроченный и
буксующий шаг, шаг на сегодня со временем, параллельная группа с двумя
активными листьями, отменённая задача с открытым шагом (не должна всплыть),
шаг без даты, ждущая и закрытая задачи. Настройки — по умолчанию из
`settings.defaults()`, поэтому рабочие часы и справочник причин
детерминированы.

Обновить снимок осознанно:

    GOLDEN_UPDATE=1 python3 -m pytest tests/test_golden_feed_mark.py

После обновления снимок читается глазами через `git diff`: это и есть ревью
изменения формы ответа.
"""
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402
import store  # noqa: E402

GOLDEN = Path(__file__).parent / "golden" / "feed_mark.json"

TODAY = date(2026, 9, 8)
NOW = "2026-09-08 11:00"

# Поля, добавленные после снятия снимка. В сравнении вычитаются из ответа:
# их появление — решение из REFACTOR.md, а не регрессия.
NEW_FIELDS = {"task_id"}


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    monkeypatch.setattr(engine, "KB_DIR", tmp_path / "База")
    return tmp_path


def _step(number, title, *, status="pending", control=None, completed=None,
          log=(), parent=None, mode=None):
    return {"id": number, "title": title, "status": status, "control": control,
            "completed": completed, "log": list(log), "parent": parent, "mode": mode}


def _seed(vault):
    """Пишет стор напрямую, в обход движка — код под тестом не готовит свои
    данные. Тот же приём, что в `test_engine.task()`."""
    conn = sqlite3.connect(str(vault / "стор.db"))
    store.migrate_schema(conn)

    def task(title, steps, *, cancelled=False, tags=()):
        cur = conn.execute(
            "INSERT INTO tasks (title, schema, created, start_date, cancelled, "
            "cancelled_reason, body) VALUES (?, 1, '2026-08-20', '2026-08-20', ?, ?, ?)",
            (title, int(cancelled), "передумали" if cancelled else None, "Тело.\n"))
        tid = cur.lastrowid
        for i, s in enumerate(steps):
            conn.execute(
                "INSERT INTO steps (task_id, step_id, position, title, status, "
                "start_date, control_date, completed_date, note, parent_id, mode) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?, ?, NULL, ?, ?)",
                (tid, s["id"], i, s["title"], s["status"], s["control"],
                 s["completed"], s["parent"], s["mode"]))
            for e in s["log"]:
                conn.execute(
                    "INSERT INTO step_log (task_id, step_id, date, event, reason, was, "
                    "to_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (tid, s["id"], e["date"], e["event"], e.get("reason"),
                     e.get("was"), e.get("to")))
        for name in tags:
            row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
            tag_id = row[0] if row else conn.execute(
                "INSERT INTO tags (name, color, pinned) VALUES (?, '#999999', 0)",
                (name,)).lastrowid
            conn.execute("INSERT INTO task_tags (task_id, tag_id) VALUES (?, ?)", (tid, tag_id))

    task("Оплатить налоги за третий квартал", [
        _step(1, "Свести суммы", status="done", completed="2026-09-01",
              log=[{"date": "2026-09-01", "event": "done"}]),
        _step(2, "Отправить платёжку", control="2026-09-04",
              log=[{"date": "2026-09-04", "event": "not_done", "reason": "не было денег",
                    "was": "2026-09-03", "to": "2026-09-04"},
                   {"date": "2026-09-05", "event": "not_done", "reason": "не было денег",
                    "was": "2026-09-04", "to": "2026-09-04"}]),
        _step(3, "Получить выписку"),
    ], tags=("финансы",))
    task("Позвонить Василию", [
        _step(1, "Договориться о встрече", control="2026-09-08T10:00:00"),
        _step(2, "Съездить на объект", control="2026-09-12"),
    ], tags=("стройка", "люди"))
    task("Сделка по складу", [
        _step(1, "Подписи", mode="par"),
        _step(2, "Подпись арендодателя", control="2026-09-08", parent=1),
        _step(3, "Подпись банка", control="2026-09-09", parent=1),
        _step(4, "Передать ключи", control="2026-09-20"),
    ])
    task("Отменённая история", [_step(1, "Не всплывать", control="2026-09-01")],
         cancelled=True)
    task("Продлить страховку", [_step(1, "Запросить счёт", control="2026-09-15")])
    task("Закрытая задача", [
        _step(1, "Сделано давно", status="done", completed="2026-08-25",
              log=[{"date": "2026-08-25", "event": "done"}]),
    ])
    task("Шаг без даты", [_step(1, "Когда-нибудь")])
    conn.commit()
    conn.close()


def _run(cmd, **fields):
    fields.setdefault("force", False)
    fields.setdefault("reason", None)
    fields.setdefault("to", None)
    return cmd(SimpleNamespace(**fields), TODAY)


def _scenario(vault):
    _seed(vault)
    причина = engine.get_reasons()[0]
    шаги = []

    def snap(name, fn):
        try:
            result = fn()
        except SystemExit as e:
            result = {"exit": str(e)}
        шаги.append({"op": name, "result": json.loads(json.dumps(result, default=str,
                                                                    ensure_ascii=False))})

    snap("feed", lambda: _run(engine.cmd_feed, now=NOW))
    snap("backlog", lambda: _run(engine.cmd_backlog, now=NOW))
    snap("done", lambda: _run(engine.cmd_done, task="Позвонить", step="1"))
    snap("notdone", lambda: _run(engine.cmd_notdone, task="налоги", step="2", reason=причина))
    snap("notdone_bad_reason", lambda: _run(engine.cmd_notdone, task="налоги", step="2",
                                            reason="такой причины нет"))
    snap("defer", lambda: _run(engine.cmd_defer, task="Сделка", step="2",
                               to="2026-09-10 15:00", reason=причина))
    snap("fail", lambda: _run(engine.cmd_fail, task="Сделка", step="3", reason=причина))
    snap("skip", lambda: _run(engine.cmd_skip, task="страховку", step="1"))
    snap("done_on_group", lambda: _run(engine.cmd_done, task="Сделка", step="1"))
    snap("done_twice", lambda: _run(engine.cmd_done, task="Позвонить", step="1"))
    snap("done_unknown_task", lambda: _run(engine.cmd_done, task="нет такой", step="1"))
    snap("undo_notdone", lambda: _run(engine.cmd_undo, task="налоги", step="2"))
    snap("undo_skip", lambda: _run(engine.cmd_undo, task="страховку", step="1"))
    snap("undo_yesterday", lambda: _run(engine.cmd_undo, task="Закрытая", step="1"))
    snap("undo_empty_log", lambda: _run(engine.cmd_undo, task="без даты", step="1"))
    snap("feed_after", lambda: _run(engine.cmd_feed, now=NOW))
    snap("backlog_after", lambda: _run(engine.cmd_backlog, now=NOW))
    return шаги


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in NEW_FIELDS}
    if isinstance(obj, list):
        return [_strip(x) for x in obj]
    return obj


def test_лента_и_отметки_совпадают_со_снимком(vault):
    actual = _scenario(vault)
    if os.environ.get("GOLDEN_UPDATE"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        pytest.skip("снимок обновлён")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert [s["op"] for s in actual] == [s["op"] for s in expected]
    for got, want in zip(actual, expected):
        assert _strip(got["result"]) == _strip(want["result"]), got["op"]


def test_снимок_детерминирован(vault, tmp_path_factory, monkeypatch):
    """Два прогона на двух пустых сторах дают один и тот же снимок: иначе
    тест выше ловил бы шум генератора, а не изменения кода."""
    первый = _scenario(vault)
    второй_каталог = tmp_path_factory.mktemp("vault2")
    monkeypatch.setattr(engine, "VAULT", второй_каталог)
    monkeypatch.setattr(engine, "KB_DIR", второй_каталог / "База")
    assert _scenario(второй_каталог) == первый
