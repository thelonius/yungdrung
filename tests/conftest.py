#!/usr/bin/env python3
"""Общая фикстура вольта и обвязка задач для тестов движка.

Раньше test_engine.py/test_card.py/test_feed.py по отдельности monkeypatch'или
engine.VAULT/TASKS_DIR/KB_DIR на временную папку и заводили задачу вручную —
писали YAML во frontmatter и клали файл рядом через write_text(). После
переезда вольта на SQLite (storage.py, см. CLAUDE.md: «Markdown-вольт, не
SQLite → SQLite») папок «Задачи»/«База» и файла на задачу больше нет: вольт —
один файл `Вольт.sqlite`. Писать задачу «руками» значит звать
storage.create_task(), а не write_text() по YAML.

Обвязка ниже сохраняет старую сигнатуру `task(vault, name, steps, **fields)` /
`step(number, title, ...)`, чтобы тела уже написанных тестов не пришлось
переписывать: меняется то, что стоит за вызовом, а не как его зовут. Каждый
из трёх файлов теперь просто импортирует нужные имена отсюда вместо того,
чтобы определять свою копию.
"""
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402
import storage  # noqa: E402

# Дата, которой заводится задача внутри task(), если тест не назвал свою через
# fields["created"]. Значения не видно снаружи: первый же run(engine.cmd_*) в
# теле теста пересчитывает сводку через настоящий save() со своим "today", и
# то, что здесь, к этому моменту уже переписано. Важно только, что это valid
# date — engine.save() должен на нём отработать не падая.
_HELPER_TODAY = date(2026, 8, 1)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Временный вольт вместо репозитория: SQLite-файл внутри tmp_path.

    Раньше здесь ещё создавалась папка «Задачи» и патчились TASKS_DIR/KB_DIR —
    обе константы ушли вместе с файловым вольтом, engine.py теперь знает
    только DB_PATH.
    """
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    monkeypatch.setattr(engine, "DB_PATH", tmp_path / "Вольт.sqlite")
    return tmp_path


def step(number, title, *, status="pending", control_date=None,
        completed_date=None, log=None):
    return {"id": number, "title": title, "status": status,
            "control_date": control_date, "completed_date": completed_date,
            "log": log if log is not None else []}


def task(vault, name, steps, *, body="Тело заметки, его пишет заказчик.\n", **fields):
    """Заводит задачу через storage.create_task напрямую — тем же низкоуровневым
    путём, что и миграция markdown-вольта, а не запись YAML-файла вручную
    (файла для задачи больше нет). Сводку верхнего уровня намеренно не считаем:
    как и раньше её проставляет движок (engine.save(), через любую из команд
    cmd_*), а тесты проверяют, что он это делает — если считать её здесь же,
    первый же `refresh` в теле теста увидит уже посчитанную сводку и не
    отрапортует о ней как об изменении.

    Возвращает `storage.TaskRef` — как и Path, он несёт `.stem` с именем
    задачи, поэтому старые вызовы вида `task(...)` → `path.stem` и
    `read(path)` продолжают работать без правок.
    """
    meta = {"schema": 1, "type": "task", "title": name, "created": _HELPER_TODAY}
    meta.update(fields)
    meta["steps"] = steps
    conn = storage.connect(engine.DB_PATH)
    try:
        created = storage.create_task(conn, meta)
        # create_task() пишет body="" безусловно (см. его контракт) — тело
        # заказчика докладываем отдельным UPDATE, как это делает save_task().
        conn.execute("UPDATE tasks SET body=? WHERE id=?", (body, created["id"]))
        conn.commit()
    finally:
        conn.close()
    return storage.TaskRef(name)


def _parse_dateish(value):
    """Строка ISO-формата → date/datetime, как раньше отдавал YAML.

    storage.py хранит даты TEXT-строками (сознательное решение слоя
    хранения — см. его докстринг про SQLite без типа даты), а не объектами
    date/datetime. Старые тесты сравнивают значения с `date(...)`/
    `datetime(...)` напрямую; конвертация здесь, в тестовой обвязке, держит
    эту разницу подальше от контракта storage.py, а не размывает его.
    """
    if not isinstance(value, str):
        return value
    if _DATE_RE.match(value):
        return date.fromisoformat(value)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return value


def _coerce_dates(meta):
    for key in ("created", "start_date", "control_date"):
        if key in meta:
            meta[key] = _parse_dateish(meta[key])
    for s in meta.get("steps") or []:
        for key in ("start_date", "control_date", "completed_date"):
            if key in s:
                s[key] = _parse_dateish(s[key])
        for entry in s.get("log") or []:
            for key in ("date", "was", "to"):
                if key in entry:
                    entry[key] = _parse_dateish(entry[key])
    return meta


def read(name):
    """Задача целиком по имени — раньше был `engine.parse_file(путь_к_файлу)`.

    Принимает то же, что раньше принимал путь до файла: `Path` и
    `storage.TaskRef` годятся без изменений, потому что у обоих есть `.stem` с
    именем задачи — вызовы вида `read(vault / "Задачи" / "Имя.md")`
    продолжают работать без правок, даже притом что такого файла на диске
    никогда не было. Голая строка с именем задачи тоже подходит.
    """
    имя = name.stem if hasattr(name, "stem") else name
    conn = storage.connect(engine.DB_PATH)
    try:
        found = storage.find_task_exact(conn, имя)
    finally:
        conn.close()
    if found is None:
        raise FileNotFoundError(f"нет задачи «{имя}» в БД {engine.DB_PATH}")
    meta = _coerce_dates(dict(found["meta"]))
    return meta, found["body"]
