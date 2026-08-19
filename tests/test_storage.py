#!/usr/bin/env python3
"""Тесты слоя хранения (`storage.py`) — SQLite вместо markdown-вольта.

Модуль тестируется в изоляции: без monkeypatch на engine.py, без импорта
engine.py вообще. Формы возвращаемых словарей проверяются построчно, потому
что от них дословно зависит контракт со следующим агентом, который пишет
engine.py поверх этого модуля.
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import storage  # noqa: E402

ПРИМЕРЫ = Path(__file__).resolve().parent.parent / "примеры"


# --- вспомогательное ---------------------------------------------------------

def _simple_task_meta(title="Задача", created="2026-08-01"):
    return {
        "schema": 1,
        "type": "task",
        "title": title,
        "created": created,
        "start_date": created,
        "tags": ["тег1", "тег2"],
        "steps": [
            {
                "id": 1,
                "title": "Первый шаг",
                "status": "pending",
                "start_date": created,
                "control_date": "2026-08-05",
                "completed_date": None,
                "note": None,
                "log": [],
            },
            {
                "id": 2,
                "title": "Второй шаг",
                "status": "pending",
                "start_date": None,
                "control_date": None,
                "completed_date": None,
                "note": None,
                "log": [],
            },
        ],
    }


# --- схема / connect ---------------------------------------------------------

def test_connect_creates_schema_idempotently(tmp_path):
    db_path = tmp_path / "Вольт.sqlite"
    conn1 = storage.connect(db_path)
    conn1.close()
    # второй connect на тот же файл не должен падать (CREATE ... IF NOT EXISTS)
    conn2 = storage.connect(db_path)
    tables = {
        r[0] for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"tasks", "task_tags", "steps", "step_log", "notes",
            "note_tags", "note_aliases"} <= tables
    conn2.close()


def test_connect_sets_pragmas(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    conn.close()


# --- create_task / load_tasks -----------------------------------------------

def test_create_task_and_load_tasks_roundtrip(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    meta = _simple_task_meta("Круговой прогон")
    created = storage.create_task(conn, meta)

    assert created["path"].stem == "Круговой прогон"
    assert created["body"] == ""
    assert created["meta"]["title"] == "Круговой прогон"
    assert created["meta"]["created"] == "2026-08-01"
    assert created["meta"]["start_date"] == "2026-08-01"
    assert created["meta"]["tags"] == ["тег1", "тег2"]
    assert "cancelled" not in created["meta"]
    assert created["meta"]["cancelled_reason"] is None
    assert len(created["meta"]["steps"]) == 2
    assert created["meta"]["steps"][0]["id"] == 1
    assert created["meta"]["steps"][0]["log"] == []

    tasks = storage.load_tasks(conn)
    assert len(tasks) == 1
    assert tasks[0]["path"].stem == "Круговой прогон"
    assert tasks[0]["meta"] == created["meta"]
    conn.close()


def test_multistep_task_with_log_roundtrip(tmp_path):
    """Многошаговая задача с журналом из done/not_done/defer с was/to/reason —
    как в примеры/Замена подшипника в колесе.md и примеры/Заявка на грант ФПГ.md."""
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    meta = {
        "schema": 1,
        "type": "task",
        "title": "Замена подшипника",
        "created": "2026-07-20",
        "start_date": "2026-07-20",
        "tags": ["сервис"],
        "steps": [
            {
                "id": 1,
                "title": "Заказать подшипник",
                "status": "done",
                "start_date": "2026-07-20",
                "control_date": "2026-07-20",
                "completed_date": "2026-07-20",
                "note": None,
                "log": [{"date": "2026-07-20", "event": "done"}],
            },
            {
                "id": 2,
                "title": "Снять колесо, заменить",
                "status": "pending",
                "start_date": None,
                "control_date": "2026-08-12",
                "completed_date": None,
                "note": None,
                "log": [
                    {"date": "2026-07-25", "event": "not_done",
                     "was": "2026-07-25", "to": "2026-08-01", "reason": "не было времени"},
                    {"date": "2026-08-01", "event": "not_done",
                     "was": "2026-08-01", "to": "2026-08-08", "reason": "сервис не отвечал"},
                    {"date": "2026-08-08", "event": "defer",
                     "was": "2026-08-08", "to": "2026-08-12", "reason": "мастер в отпуске"},
                ],
            },
        ],
    }
    storage.create_task(conn, meta)

    loaded = storage.find_task_exact(conn, "Замена подшипника")
    assert loaded is not None
    steps = loaded["meta"]["steps"]
    assert steps[0]["status"] == "done"
    assert steps[0]["log"] == [{"date": "2026-07-20", "event": "done"}]
    assert steps[1]["log"] == meta["steps"][1]["log"]
    assert [e["event"] for e in steps[1]["log"]] == ["not_done", "not_done", "defer"]
    conn.close()


def test_cancelled_false_omits_key_true_includes_it(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    meta_open = _simple_task_meta("Не отменена")
    task = storage.create_task(conn, meta_open)
    assert "cancelled" not in task["meta"]

    meta_cancelled = _simple_task_meta("Отменена")
    meta_cancelled["cancelled"] = True
    meta_cancelled["cancelled_reason"] = "не нужна больше"
    task2 = storage.create_task(conn, meta_cancelled)
    assert task2["meta"]["cancelled"] is True
    assert task2["meta"]["cancelled_reason"] == "не нужна больше"
    conn.close()


def test_extra_json_roundtrip(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    meta = _simple_task_meta("С незнакомым полем")
    meta["status"] = "ждёт"
    meta["progress"] = "1/2"
    meta["current_step"] = "Первый шаг"
    task = storage.create_task(conn, meta)
    assert task["meta"]["status"] == "ждёт"
    assert task["meta"]["progress"] == "1/2"
    assert task["meta"]["current_step"] == "Первый шаг"

    reloaded = storage.find_task_exact(conn, "С незнакомым полем")
    assert reloaded["meta"]["status"] == "ждёт"
    assert reloaded["meta"]["progress"] == "1/2"
    conn.close()


# --- save_task ---------------------------------------------------------------

def test_save_task_reflected_in_next_load(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    meta = _simple_task_meta("Правится")
    task = storage.create_task(conn, meta)

    task["meta"]["steps"][0]["status"] = "done"
    task["meta"]["steps"][0]["completed_date"] = "2026-08-03"
    task["meta"]["steps"][0]["log"].append({"date": "2026-08-03", "event": "done"})
    task["body"] = "новое тело задачи"
    storage.save_task(conn, task)

    reloaded = storage.find_task_exact(conn, "Правится")
    assert reloaded["meta"]["steps"][0]["status"] == "done"
    assert reloaded["meta"]["steps"][0]["completed_date"] == "2026-08-03"
    assert reloaded["meta"]["steps"][0]["log"] == [{"date": "2026-08-03", "event": "done"}]
    assert reloaded["body"] == "новое тело задачи"
    conn.close()


def test_save_task_unknown_title_raises(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    fake = {"meta": _simple_task_meta("Нет такой"), "body": ""}
    with pytest.raises(ValueError):
        storage.save_task(conn, fake)
    conn.close()


# --- rename_task / delete_task ----------------------------------------------

def test_rename_task_to_taken_name_raises_and_keeps_original(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    t1 = storage.create_task(conn, _simple_task_meta("Первая"))
    storage.create_task(conn, _simple_task_meta("Вторая"))

    with pytest.raises(ValueError):
        storage.rename_task(conn, t1["id"], "Вторая")

    still_there = storage.find_task_exact(conn, "Первая")
    assert still_there is not None
    assert still_there["meta"]["title"] == "Первая"
    conn.close()


def test_rename_task_success(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    t1 = storage.create_task(conn, _simple_task_meta("Старое имя"))
    storage.rename_task(conn, t1["id"], "Новое имя")
    assert storage.find_task_exact(conn, "Старое имя") is None
    renamed = storage.find_task_exact(conn, "Новое имя")
    assert renamed is not None
    conn.close()


def test_delete_task_cascades(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    meta = _simple_task_meta("Удаляемая")
    meta["steps"][0]["log"] = [{"date": "2026-08-01", "event": "done"}]
    task = storage.create_task(conn, meta)
    task_id = task["id"]

    assert conn.execute("SELECT COUNT(*) FROM steps WHERE task_id=?", (task_id,)).fetchone()[0] == 2
    storage.delete_task(conn, task_id)

    assert storage.find_task_exact(conn, "Удаляемая") is None
    assert conn.execute("SELECT COUNT(*) FROM steps WHERE task_id=?", (task_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM task_tags WHERE task_id=?", (task_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM step_log").fetchone()[0] == 0
    conn.close()


# --- find_task_exact / task_exists ------------------------------------------

def test_find_task_exact_and_task_exists(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    storage.create_task(conn, _simple_task_meta("Найдётся"))
    assert storage.task_exists(conn, "Найдётся") is True
    assert storage.task_exists(conn, "Не существует") is False
    assert storage.find_task_exact(conn, "Не существует") is None
    conn.close()


# --- FTS5 --------------------------------------------------------------------

def test_fts_search_finds_task_by_title_and_body_word(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    meta = _simple_task_meta("Уникальный подшипник 6805")
    task = storage.create_task(conn, meta)
    task["body"] = "текст про мотор-колесо и смазку"
    storage.save_task(conn, task)

    hits_title = conn.execute(
        "SELECT rowid FROM tasks_fts WHERE tasks_fts MATCH ?", ("подшипник",)
    ).fetchall()
    assert len(hits_title) == 1

    hits_body = conn.execute(
        "SELECT rowid FROM tasks_fts WHERE tasks_fts MATCH ?", ("смазку",)
    ).fetchall()
    assert len(hits_body) == 1
    conn.close()


def test_fts_search_reflects_update(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    task = storage.create_task(conn, _simple_task_meta("Задача для FTS"))
    task["body"] = "исходное содержание"
    storage.save_task(conn, task)

    before = conn.execute(
        "SELECT rowid FROM tasks_fts WHERE tasks_fts MATCH ?", ("исходное",)
    ).fetchall()
    assert len(before) == 1

    task = storage.find_task_exact(conn, "Задача для FTS")
    task["body"] = "заменённое содержание"
    storage.save_task(conn, task)

    stale = conn.execute(
        "SELECT rowid FROM tasks_fts WHERE tasks_fts MATCH ?", ("исходное",)
    ).fetchall()
    assert stale == []

    fresh = conn.execute(
        "SELECT rowid FROM tasks_fts WHERE tasks_fts MATCH ?", ("заменённое",)
    ).fetchall()
    assert len(fresh) == 1
    conn.close()


def test_fts_search_notes(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    note = {"slug": "6805", "title": "6805", "body": "Типоразмер подшипника",
            "tags": ["запчасти"], "aliases": []}
    storage.create_note(conn, note)

    hits = conn.execute(
        "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?", ("Типоразмер",)
    ).fetchall()
    assert len(hits) == 1
    conn.close()


# --- notes -------------------------------------------------------------------

def test_load_notes_form_with_aliases(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    storage.create_note(conn, {
        "slug": "Василий Говнов", "title": "Василий Говнов",
        "body": "Согласующий по грантовым заявкам.",
        "tags": ["люди"], "aliases": ["Вася", "ВГ"],
    })
    notes = storage.load_notes(conn)
    assert notes == [{"id": "Василий Говнов", "title": "Василий Говнов",
                       "aliases": ["Вася", "ВГ"]}]
    conn.close()


def test_note_crud(tmp_path):
    conn = storage.connect(tmp_path / "Вольт.sqlite")
    created = storage.create_note(conn, {
        "slug": "заметка", "title": "Заметка", "body": "тело",
        "tags": ["тег"], "aliases": ["алиас"],
    })
    assert created["slug"] == "заметка"
    assert created["title"] == "Заметка"
    assert created["tags"] == ["тег"]
    assert created["aliases"] == ["алиас"]

    found = storage.find_note_exact(conn, "заметка")
    assert found == created

    updated = storage.update_note(conn, created["id"], {
        "slug": "заметка", "title": "Заметка (правлено)", "body": "новое тело",
        "tags": ["тег", "тег2"], "aliases": [],
    })
    assert updated["title"] == "Заметка (правлено)"
    assert updated["body"] == "новое тело"
    assert updated["tags"] == ["тег", "тег2"]
    assert updated["aliases"] == []

    storage.delete_note(conn, created["id"])
    assert storage.find_note_exact(conn, "заметка") is None
    conn.close()


# --- migrate_markdown_vault --------------------------------------------------

def _copy_примеры(vault_path):
    (vault_path / "Задачи").mkdir(parents=True, exist_ok=True)
    (vault_path / "База").mkdir(parents=True, exist_ok=True)
    tasks = ["Замена подшипника в колесе.md", "Заявка на грант ФПГ.md"]
    notes = ["6805.md", "Василий Говнов.md"]
    for name in tasks:
        shutil.copy(ПРИМЕРЫ / name, vault_path / "Задачи" / name)
    for name in notes:
        shutil.copy(ПРИМЕРЫ / name, vault_path / "База" / name)


def test_migrate_markdown_vault_imports_examples_without_loss(tmp_path):
    vault_path = tmp_path / "вольт"
    _copy_примеры(vault_path)
    db_path = tmp_path / "Вольт.sqlite"

    result = storage.migrate_markdown_vault(vault_path, db_path)

    assert result["tasks"] == 2
    assert result["notes"] == 2
    assert result["broken"] == []

    conn = storage.connect(db_path)
    tasks = storage.load_tasks(conn)
    assert {t["path"].stem for t in tasks} == {
        "Замена подшипника в колесе", "Заявка на грант ФПГ",
    }

    подшипник = storage.find_task_exact(conn, "Замена подшипника в колесе")
    steps = подшипник["meta"]["steps"]
    assert len(steps) == 2
    assert steps[0]["status"] == "done"
    assert steps[1]["status"] == "pending"
    assert len(steps[1]["log"]) == 3
    assert [e["event"] for e in steps[1]["log"]] == ["not_done", "not_done", "not_done"]
    assert steps[1]["log"][-1]["reason"] == "мастер в отпуске до сентября"
    assert steps[1]["log"][-1]["to"] == "2026-08-12"
    # ссылка на заметку базы в названии шага не потеряна
    assert steps[0]["title"] == "Заказать подшипник [[6805]]"
    # незнакомые (для новой схемы) поля старого frontmatter сохранены как есть
    assert подшипник["meta"]["status"] == "просрочена"
    assert подшипник["meta"]["stalled"] == 3

    грант = storage.find_task_exact(conn, "Заявка на грант ФПГ")
    grant_steps = грант["meta"]["steps"]
    assert grant_steps[1]["log"][0]["event"] == "defer"
    assert grant_steps[1]["log"][0]["was"] == "2026-08-13"
    assert grant_steps[1]["log"][0]["to"] == "2026-08-15"
    assert grant_steps[2]["log"] == []

    notes = storage.load_notes(conn)
    assert {n["id"] for n in notes} == {"6805", "Василий Говнов"}
    подшипник_note = storage.find_note_exact(conn, "6805")
    assert подшипник_note["tags"] == ["запчасти"]
    assert "мотор-колёсах" in подшипник_note["body"]

    conn.close()


def test_migrate_markdown_vault_broken_file_does_not_abort(tmp_path):
    vault_path = tmp_path / "вольт"
    _copy_примеры(vault_path)
    (vault_path / "Задачи" / "битая.md").write_text(
        "это не frontmatter, просто текст без ---\n", encoding="utf-8"
    )
    db_path = tmp_path / "Вольт.sqlite"

    result = storage.migrate_markdown_vault(vault_path, db_path)

    assert result["tasks"] == 2
    assert result["notes"] == 2
    assert len(result["broken"]) == 1
    assert result["broken"][0]["file"] == "битая.md"


def test_migrate_markdown_vault_missing_dirs_ok(tmp_path):
    vault_path = tmp_path / "пустой_вольт"
    vault_path.mkdir()
    result = storage.migrate_markdown_vault(vault_path, tmp_path / "Вольт.sqlite")
    assert result == {"tasks": 0, "notes": 0, "broken": []}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
