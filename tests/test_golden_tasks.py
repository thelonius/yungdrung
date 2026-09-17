#!/usr/bin/env python3
"""Golden-тест жизненного цикла задачи — сетка безопасности среза 2.

Снимок снят на коде **до** переноса создания, карточки, правки, отмены,
закрытия и удаления в `core/tasks.py` и `domain/steps_plan.py` (REFACTOR.md,
срез 2). Смысл тот же, что у `test_golden_feed_mark.py`: перенос не имеет права
поменять ни одного байта в ответах CLI-адаптера `engine.cmd_*`, кроме полей,
добавленных осознанно и перечисленных в `NEW_FIELDS`. Существующие тесты
проверяют инварианты по отдельности; этот фиксирует форму ответа целиком:
порядок ошибок, точечные пути полей, тексты, русские и английские статусы
там, где они исторически разошлись.

Даты в ответе `show` сериализуются `default=str` — «2026-09-10 10:00:00» с
пробелом. Это форма CLI-адаптера, `/api/v1` отдаёт свою (`format_control`),
и сравнивать с ней этот снимок не надо.

Обновить снимок осознанно:

    GOLDEN_UPDATE=1 python3 -m pytest tests/test_golden_tasks.py

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

GOLDEN = Path(__file__).parent / "golden" / "tasks.json"

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
    данные. Тот же приём, что в `test_engine.task()` и `test_golden_feed_mark`."""
    conn = sqlite3.connect(str(vault / "стор.db"))
    store.migrate_schema(conn)

    def task(title, steps, *, cancelled=False, tags=(), body="Тело.\n"):
        cur = conn.execute(
            "INSERT INTO tasks (title, schema, created, start_date, cancelled, "
            "cancelled_reason, body) VALUES (?, 1, '2026-08-20', '2026-08-20', ?, ?, ?)",
            (title, int(cancelled), "передумали" if cancelled else None, body))
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
                    "was": "2026-09-03", "to": "2026-09-04"}]),
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
    task("Закрытая задача", [
        _step(1, "Сделано давно", status="done", completed="2026-08-25",
              log=[{"date": "2026-08-25", "event": "done"}]),
    ])
    # Блок шагов в теле — так писал markdown-вывод до `47cdc52`; у заказчика
    # такие задачи остались, и `show` обязан срезать блок из заметки.
    task("Старая с блоком шагов", [_step(1, "Единственный", control="2026-09-30")],
         body=f"Заметка руками.\n\n{engine.STEPS_START}\n- [ ] 1. Единственный\n"
              f"{engine.STEPS_END}\n")
    conn.commit()
    conn.close()


def _run(cmd, **fields):
    fields.setdefault("force", False)
    fields.setdefault("reason", None)
    fields.setdefault("to", None)
    return cmd(SimpleNamespace(**fields), TODAY)


def _create(data, **fields):
    return _run(engine.cmd_create, json=json.dumps(data, ensure_ascii=False), **fields)


def _update(task, data, **fields):
    return _run(engine.cmd_update, task=task, json=json.dumps(data, ensure_ascii=False),
                **fields)


ГРАНТ_ШАГИ = [
    {"title": "Собрать документы", "control_date": "10.09 10:00"},
    {"title": "Отправить", "control_date": "15.09", "note": "через сайт фонда"},
]


def _scenario(vault):
    _seed(vault)
    шаги = []

    def snap(name, fn):
        try:
            result = fn()
        except SystemExit as e:
            result = {"exit": str(e)}
        шаги.append({"op": name, "result": json.loads(json.dumps(result, default=str,
                                                                    ensure_ascii=False))})

    snap("create", lambda: _create({
        "title": "Заявка на грант", "tags": ["фонд", " деньги "], "body": "Заметка.",
        "start_date": "08.09", "steps": ГРАНТ_ШАГИ}))
    snap("create_par_group", lambda: _create({
        "title": "Подписать договор",
        "steps": [
            {"title": "Подписи", "mode": "par", "steps": [
                {"title": "Арендодатель", "control_date": "12.09"},
                {"title": "Банк", "control_date": "13.09"}]},
            {"title": "Передать ключи", "control_date": "20.09"},
        ]}))
    snap("create_errors_by_path", lambda: _create({
        "title": "оплатить НАЛОГИ за третий квартал",
        "steps": [
            {"title": "", "control_date": "позавчера"},
            {"title": "Группа с датой", "control_date": "12.09", "steps": [
                {"title": "Лист"}]},
            {"title": "Кривой режим", "mode": "xx", "steps": [{"title": "Лист"}]},
            {"title": "Пустая группа", "mode": "par", "steps": []},
            {"title": "Раньше начала", "start_date": "20.09", "control_date": "15.09"},
        ]}))
    snap("create_bad_title_no_steps", lambda: _create({"title": "Отчёт/квартал", "steps": []}))
    snap("create_bad_json", lambda: _run(engine.cmd_create, json="{не json"))
    snap("show", lambda: _run(engine.cmd_show, task="грант", now=NOW))
    snap("show_par_group", lambda: _run(engine.cmd_show, task="договор", now=NOW))
    snap("show_strips_steps_block", lambda: _run(engine.cmd_show, task="блоком", now=NOW))
    snap("update_reorder", lambda: _update("грант", {
        "title": "Заявка на грант",
        "steps": [{"id": 2, "title": "Отправить", "control_date": "15.09"},
                  {"id": 1, "title": "Собрать документы", "control_date": "10.09 10:00"}]}))
    snap("update_missing_step_no_force", lambda: _update("грант", {
        "title": "Заявка на грант",
        "steps": [{"id": 1, "title": "Собрать документы", "control_date": "10.09 10:00"}]}))
    snap("update_missing_step_force", lambda: _update("грант", {
        "title": "Заявка на грант",
        "steps": [{"id": 1, "title": "Собрать документы", "control_date": "10.09 10:00"},
                  {"title": "Новый шаг", "control_date": "18.09"}]}, force=True))
    snap("update_rename_taken", lambda: _update("грант", {
        "title": "Позвонить Василию",
        "steps": [{"id": 1, "title": "Собрать документы", "control_date": "10.09 10:00"},
                  {"id": 3, "title": "Новый шаг", "control_date": "18.09"}]}))
    snap("update_rename", lambda: _update("грант", {
        "title": "Заявка на грант ФПГ", "tags": ["фонд"], "body": "Новая заметка.",
        "start_date": "07.09",
        "steps": [{"id": 1, "title": "Собрать документы", "control_date": "10.09 10:00"},
                  {"id": 3, "title": "Новый шаг", "control_date": "18.09",
                   "note": "заметка шага"}]}))
    snap("show_after_update", lambda: _run(engine.cmd_show, task="ФПГ", now=NOW))
    snap("update_bad_date_not_written", lambda: _update("ФПГ", {
        "title": "Заявка на грант ФПГ",
        "steps": [{"id": 1, "title": "Собрать документы", "control_date": "когда-нибудь"}]}))
    snap("update_unknown_task", lambda: _update("нет такой", {"title": "x", "steps": []}))

    данные_с_явным_стартом = {
        "title": "Проверка дат", "start_date": "10.09",
        "steps": [
            {"title": "Первый", "start_date": "2026-09-05", "control_date": "12.09"},
            {"title": "Второй", "start_date": "11.09", "control_date": "14.09"},
            {"title": "Группа", "mode": "seq", "steps": [
                {"title": "Внутри", "control_date": "16.09"},
                {"title": "Раньше", "start_date": "15.09", "control_date": "17.09"}]},
        ]}
    snap("resolve_steps", lambda: engine.resolve_steps(
        данные_с_явным_стартом["steps"], date(2026, 9, 10), TODAY))
    snap("soft_warnings", lambda: engine.soft_warnings(данные_с_явным_стартом, TODAY))
    snap("validate_new_task", lambda: engine.validate_new_task(
        данные_с_явным_стартом, ["проверка дат"], TODAY))

    snap("cancel", lambda: _run(engine.cmd_cancel, task="Позвонить", reason="  передумали "))
    snap("cancel_twice", lambda: _run(engine.cmd_cancel, task="Позвонить"))
    snap("close", lambda: _run(engine.cmd_close, task="Сделка"))
    snap("close_again", lambda: _run(engine.cmd_close, task="Сделка"))
    snap("close_cancelled", lambda: _run(engine.cmd_close, task="Отменённая"))
    snap("reopen", lambda: _run(engine.cmd_reopen, task="налоги", step="1"))
    snap("reopen_open_step", lambda: _run(engine.cmd_reopen, task="налоги", step="3"))
    snap("reopen_group", lambda: _run(engine.cmd_reopen, task="Сделка", step="1"))
    snap("reopen_unknown_step", lambda: _run(engine.cmd_reopen, task="налоги", step="9"))
    snap("delete", lambda: _run(engine.cmd_delete, task="Закрытая"))
    snap("delete_unknown", lambda: _run(engine.cmd_delete, task="Закрытая"))
    snap("feed_after", lambda: _run(engine.cmd_feed, now=NOW))
    return шаги


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in NEW_FIELDS}
    if isinstance(obj, list):
        return [_strip(x) for x in obj]
    return obj


def test_жизненный_цикл_задачи_совпадает_со_снимком(vault):
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
