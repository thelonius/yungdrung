#!/usr/bin/env python3
"""Слой хранения Yungdrung — SQLite вместо markdown-вольта.

Единственный источник правды теперь один файл `Вольт.sqlite` в корне VAULT
(там, где раньше лежали папки `Задачи/` и `База/`). Модуль не держит
модульного состояния и не кэширует соединений: `engine.py` дёргается и как
отдельный CLI-процесс на каждую команду, и внутри долгоживущего `server.py`
с несколькими потоками, а объекты `sqlite3.Connection` нельзя делить между
потоками (то же самое уже прокомментировано в `backup.py`). Поэтому
`connect()` всегда открывает новое соединение, а закрывает его вызывающий.

Формы словарей, которые отдают `load_tasks()` / `load_notes()` и вся
CRUD-обвязка, зафиксированы как контракт для `engine.py` — не переставляй
ключи и не меняй вложенность без явного пересмотра контракта.

Пример FTS5-запроса для будущего полнотекстового поиска (R24) — просто
референс, отдельной функции под него в модуле нет:

    SELECT tasks.title, snippet(tasks_fts, 1, '»', '«', '…', 8)
    FROM tasks_fts JOIN tasks ON tasks.id = tasks_fts.rowid
    WHERE tasks_fts MATCH ? ORDER BY rank LIMIT 20;

  (параметр — например, 'подшипник OR грант'; snippet() достаёт кусок body
  вокруг совпадения, rank — встроенное ранжирование bm25 у fts5).
"""
import json
import sys
import sqlite3
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("нужен pyyaml: pip install pyyaml")

DB_TIMEOUT = 5  # секунд — как у backup.py: не виснуть намертво на блокировке

# --- ссылка на задачу без файловой системы ---------------------------------

class TaskRef:
    """Замена Path из старого движка. Везде в кодовой базе от неё требуется
    только .stem (проверено разведкой по репозиторию) — раньше это было имя
    файла без расширения, теперь просто title задачи."""

    __slots__ = ("stem",)

    def __init__(self, stem):
        self.stem = stem

    def __repr__(self):
        return f"TaskRef({self.stem!r})"

    def __eq__(self, other):
        return isinstance(other, TaskRef) and self.stem == other.stem

    def __hash__(self):
        return hash(self.stem)


# --- схема -------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL UNIQUE,
  created TEXT NOT NULL,
  start_date TEXT,
  cancelled INTEGER NOT NULL DEFAULT 0,
  cancelled_reason TEXT,
  body TEXT NOT NULL DEFAULT '',
  extra_json TEXT
);

CREATE TABLE IF NOT EXISTS task_tags (
  task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  tag TEXT NOT NULL,
  PRIMARY KEY (task_id, position)
);

CREATE TABLE IF NOT EXISTS steps (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  step_no INTEGER NOT NULL,
  position INTEGER NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  start_date TEXT,
  control_date TEXT,
  completed_date TEXT,
  note TEXT,
  UNIQUE (task_id, step_no),
  UNIQUE (task_id, position)
);

CREATE TABLE IF NOT EXISTS step_log (
  id INTEGER PRIMARY KEY,
  step_id INTEGER NOT NULL REFERENCES steps(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  date TEXT NOT NULL,
  event TEXT NOT NULL,
  reason TEXT,
  was TEXT,
  to_date TEXT,
  UNIQUE (step_id, seq)
);

CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  extra_json TEXT
);

CREATE TABLE IF NOT EXISTS note_tags (
  note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  tag TEXT NOT NULL,
  PRIMARY KEY (note_id, position)
);

CREATE TABLE IF NOT EXISTS note_aliases (
  note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  alias TEXT NOT NULL,
  PRIMARY KEY (note_id, position)
);

CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
  title, body, content='tasks', content_rowid='id'
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
  title, body, content='notes', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS tasks_fts_ai AFTER INSERT ON tasks BEGIN
  INSERT INTO tasks_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;

CREATE TRIGGER IF NOT EXISTS tasks_fts_ad AFTER DELETE ON tasks BEGIN
  INSERT INTO tasks_fts(tasks_fts, rowid, title, body)
  VALUES ('delete', old.id, old.title, old.body);
END;

CREATE TRIGGER IF NOT EXISTS tasks_fts_au AFTER UPDATE ON tasks BEGIN
  INSERT INTO tasks_fts(tasks_fts, rowid, title, body)
  VALUES ('delete', old.id, old.title, old.body);
  INSERT INTO tasks_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_ai AFTER INSERT ON notes BEGIN
  INSERT INTO notes_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_ad AFTER DELETE ON notes BEGIN
  INSERT INTO notes_fts(notes_fts, rowid, title, body)
  VALUES ('delete', old.id, old.title, old.body);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_au AFTER UPDATE ON notes BEGIN
  INSERT INTO notes_fts(notes_fts, rowid, title, body)
  VALUES ('delete', old.id, old.title, old.body);
  INSERT INTO notes_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
"""


def connect(db_path):
    """Новое соединение с базой вольта. PRAGMA + схема — при каждом вызове:
    PRAGMA foreign_keys живёт на уровне соединения (не файла), и его надо
    ставить каждый раз; журнал WAL и схема идемпотентны (IF NOT EXISTS)."""
    conn = sqlite3.connect(str(db_path), timeout=DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    return conn


# --- служебное ---------------------------------------------------------------

def _iso(value):
    """date/datetime → ISO-строка как есть; всё остальное — str(...) или None.

    YAML (safe_load) сам разбирает даты вида 2026-08-05 в datetime.date —
    именно такие значения приходят из старых frontmatter при миграции, и их
    надо привести к строке для хранения и лексикографической сортировки."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


TASK_KNOWN_KEYS = {
    "schema", "type", "title", "created", "start_date",
    "cancelled", "cancelled_reason", "tags", "steps",
}

NOTE_KNOWN_KEYS = {"type", "slug", "title", "body", "tags", "aliases", "synonyms"}


def _extract_extra(d, known_keys):
    return {k: v for k, v in d.items() if k not in known_keys}


def _json_default(value):
    """Незнакомые поля старого frontmatter могут прийти из YAML как
    datetime.date (например top-level control_date-дубликат сводки) — json
    их не умеет сам, а терять их из extra_json нельзя."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _dumps_extra(extra):
    return json.dumps(extra, ensure_ascii=False, default=_json_default) if extra else None


def _insert_list(conn, table, fk_col, value_col, entity_id, values):
    for i, value in enumerate(values):
        conn.execute(
            f"INSERT INTO {table} ({fk_col}, position, {value_col}) VALUES (?,?,?)",
            (entity_id, i, value),
        )


def _insert_log(conn, step_id, log):
    for i, entry in enumerate(log or []):
        conn.execute(
            "INSERT INTO step_log (step_id, seq, date, event, reason, was, to_date) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                step_id,
                i,
                _iso(entry.get("date")),
                entry.get("event"),
                entry.get("reason"),
                _iso(entry.get("was")),
                _iso(entry.get("to")),
            ),
        )


def _insert_steps(conn, task_id, steps):
    # "id" — стабильная метка шага (не меняется при перестановке), позиция в
    # списке — отдельная величина: заказчик может переставить местами два
    # шага, оставив им прежние id (см. test_update_переупорядочивает_сохраняя_id).
    # Сортировать по step_no вместо позиции значит игнорировать перестановку.
    for position, step in enumerate(steps or []):
        cur = conn.execute(
            "INSERT INTO steps (task_id, step_no, position, title, status, "
            "start_date, control_date, completed_date, note) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                step["id"],
                position,
                step["title"],
                step.get("status") or "pending",
                _iso(step.get("start_date")),
                _iso(step.get("control_date")),
                _iso(step.get("completed_date")),
                step.get("note"),
            ),
        )
        _insert_log(conn, cur.lastrowid, step.get("log"))


def _write_task(conn, meta, body=""):
    """Полная вставка задачи (tasks + task_tags + steps + step_log) из
    словаря meta любой полноты — используется и create_task() (шаги свежие,
    log пуст), и migrate_markdown_vault() (шаги и журнал уже сложившиеся)."""
    extra = _extract_extra(meta, TASK_KNOWN_KEYS)
    cur = conn.execute(
        "INSERT INTO tasks (title, created, start_date, cancelled, "
        "cancelled_reason, body, extra_json) VALUES (?,?,?,?,?,?,?)",
        (
            meta["title"],
            _iso(meta.get("created")),
            _iso(meta.get("start_date")),
            1 if meta.get("cancelled") else 0,
            meta.get("cancelled_reason"),
            body or "",
            _dumps_extra(extra),
        ),
    )
    task_id = cur.lastrowid
    _insert_list(conn, "task_tags", "task_id", "tag", task_id, meta.get("tags") or [])
    _insert_steps(conn, task_id, meta.get("steps") or [])
    return task_id


def _write_note(conn, note):
    extra = _extract_extra(note, NOTE_KNOWN_KEYS)
    cur = conn.execute(
        "INSERT INTO notes (slug, title, body, extra_json) VALUES (?,?,?,?)",
        (
            note["slug"],
            note["title"],
            note.get("body") or "",
            _dumps_extra(extra),
        ),
    )
    note_id = cur.lastrowid
    _insert_list(conn, "note_tags", "note_id", "tag", note_id, note.get("tags") or [])
    _insert_list(conn, "note_aliases", "note_id", "alias", note_id, note.get("aliases") or [])
    return note_id


def _row_to_task(conn, row):
    tags = [r["tag"] for r in conn.execute(
        "SELECT tag FROM task_tags WHERE task_id=? ORDER BY position", (row["id"],)
    ).fetchall()]

    steps = []
    step_rows = conn.execute(
        "SELECT * FROM steps WHERE task_id=? ORDER BY position", (row["id"],)
    ).fetchall()
    for srow in step_rows:
        log = []
        log_rows = conn.execute(
            "SELECT * FROM step_log WHERE step_id=? ORDER BY seq", (srow["id"],)
        ).fetchall()
        for lrow in log_rows:
            entry = {"date": lrow["date"], "event": lrow["event"]}
            if lrow["reason"] is not None:
                entry["reason"] = lrow["reason"]
            if lrow["was"] is not None:
                entry["was"] = lrow["was"]
            if lrow["to_date"] is not None:
                entry["to"] = lrow["to_date"]
            log.append(entry)
        steps.append({
            "id": srow["step_no"],
            "title": srow["title"],
            "status": srow["status"],
            "start_date": srow["start_date"],
            "control_date": srow["control_date"],
            "completed_date": srow["completed_date"],
            "note": srow["note"],
            "log": log,
        })

    meta = {
        "schema": 1,
        "type": "task",
        "title": row["title"],
        "created": row["created"],
        "start_date": row["start_date"],
        "cancelled_reason": row["cancelled_reason"],
        "tags": tags,
        "steps": steps,
    }
    if row["cancelled"]:
        meta["cancelled"] = True
    extra = json.loads(row["extra_json"]) if row["extra_json"] else {}
    meta.update(extra)

    return {"id": row["id"], "path": TaskRef(row["title"]), "meta": meta, "body": row["body"]}


def _row_to_note(conn, row):
    tags = [r["tag"] for r in conn.execute(
        "SELECT tag FROM note_tags WHERE note_id=? ORDER BY position", (row["id"],)
    ).fetchall()]
    aliases = [r["alias"] for r in conn.execute(
        "SELECT alias FROM note_aliases WHERE note_id=? ORDER BY position", (row["id"],)
    ).fetchall()]
    note = {
        "id": row["id"],
        "slug": row["slug"],
        "title": row["title"],
        "body": row["body"],
        "tags": tags,
        "aliases": aliases,
    }
    extra = json.loads(row["extra_json"]) if row["extra_json"] else {}
    note.update(extra)
    return note


# --- контракт: задачи --------------------------------------------------------

def load_tasks(conn):
    rows = conn.execute("SELECT * FROM tasks ORDER BY title").fetchall()
    return [_row_to_task(conn, row) for row in rows]


def load_notes(conn):
    """Форма как раньше отдавал engine.load_kb_entries(): "id" здесь — slug
    записи (как раньше был path.stem), а не числовой id строки в БД."""
    rows = conn.execute("SELECT * FROM notes ORDER BY slug").fetchall()
    out = []
    for row in rows:
        aliases = [r["alias"] for r in conn.execute(
            "SELECT alias FROM note_aliases WHERE note_id=? ORDER BY position", (row["id"],)
        ).fetchall()]
        out.append({"id": row["slug"], "title": row["title"], "aliases": aliases})
    return out


def find_task_exact(conn, title):
    row = conn.execute("SELECT * FROM tasks WHERE title=?", (title,)).fetchone()
    return _row_to_task(conn, row) if row is not None else None


def task_exists(conn, title):
    return conn.execute("SELECT 1 FROM tasks WHERE title=?", (title,)).fetchone() is not None


def create_task(conn, meta):
    with conn:
        _write_task(conn, meta, body="")
    return find_task_exact(conn, meta["title"])


def save_task(conn, task):
    meta = task["meta"]
    title = meta["title"]
    row = conn.execute("SELECT id FROM tasks WHERE title=?", (title,)).fetchone()
    if row is None:
        raise ValueError(f"задача «{title}» не найдена")
    task_id = row["id"]
    extra = _extract_extra(meta, TASK_KNOWN_KEYS)
    with conn:
        conn.execute(
            "UPDATE tasks SET created=?, start_date=?, cancelled=?, "
            "cancelled_reason=?, body=?, extra_json=? WHERE id=?",
            (
                _iso(meta.get("created")),
                _iso(meta.get("start_date")),
                1 if meta.get("cancelled") else 0,
                meta.get("cancelled_reason"),
                task.get("body") or "",
                _dumps_extra(extra),
                task_id,
            ),
        )
        conn.execute("DELETE FROM task_tags WHERE task_id=?", (task_id,))
        _insert_list(conn, "task_tags", "task_id", "tag", task_id, meta.get("tags") or [])
        # steps DELETE каскадит на step_log через FK (foreign_keys=ON у connect())
        conn.execute("DELETE FROM steps WHERE task_id=?", (task_id,))
        _insert_steps(conn, task_id, meta.get("steps") or [])


def rename_task(conn, task_id, new_title):
    with conn:
        try:
            conn.execute("UPDATE tasks SET title=? WHERE id=?", (new_title, task_id))
        except sqlite3.IntegrityError:
            raise ValueError(f"название «{new_title}» уже занято")


def delete_task(conn, task_id):
    with conn:
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))


# --- контракт: заметки базы знаний -------------------------------------------

def create_note(conn, note):
    with conn:
        _write_note(conn, note)
    return find_note_exact(conn, note["slug"])


def update_note(conn, note_id, note):
    extra = _extract_extra(note, NOTE_KNOWN_KEYS)
    with conn:
        try:
            conn.execute(
                "UPDATE notes SET slug=?, title=?, body=?, extra_json=? WHERE id=?",
                (
                    note["slug"],
                    note["title"],
                    note.get("body") or "",
                    _dumps_extra(extra),
                    note_id,
                ),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"slug «{note['slug']}» уже занят")
        conn.execute("DELETE FROM note_tags WHERE note_id=?", (note_id,))
        _insert_list(conn, "note_tags", "note_id", "tag", note_id, note.get("tags") or [])
        conn.execute("DELETE FROM note_aliases WHERE note_id=?", (note_id,))
        _insert_list(conn, "note_aliases", "note_id", "alias", note_id, note.get("aliases") or [])
    return find_note_exact(conn, note["slug"])


def delete_note(conn, note_id):
    with conn:
        conn.execute("DELETE FROM notes WHERE id=?", (note_id,))


def find_note_exact(conn, slug):
    row = conn.execute("SELECT * FROM notes WHERE slug=?", (slug,)).fetchone()
    return _row_to_note(conn, row) if row is not None else None


# --- одноразовая миграция из markdown-вольта --------------------------------

def _short_err(e):
    return " ".join(str(e).split())[:200]


def _parse_md(path):
    """Frontmatter + тело по старой схеме — разбор реализован здесь же (не
    импортируем engine.py), чтобы не тянуть цикл зависимостей и чтобы
    миграция работала даже когда в engine.py файлового кода уже не будет."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise ValueError(f"{path.name}: нет frontmatter")
    _, fm, body = raw.split("---", 2)
    meta = yaml.safe_load(fm) or {}
    return meta, body.lstrip("\n")


def _normalize_task_meta(meta, stem):
    title = str(meta.get("title") or stem).strip()
    if not title:
        raise ValueError(f"{stem}: нет названия")
    if meta.get("created") is None:
        raise ValueError(f"{stem}: нет поля created")

    steps_in = meta.get("steps") or []
    steps = []
    for i, s in enumerate(steps_in, start=1):
        log_in = s.get("log") or []
        log = []
        for entry in log_in:
            e = {"date": entry.get("date"), "event": entry.get("event")}
            for k in ("reason", "was", "to"):
                if entry.get(k) is not None:
                    e[k] = entry.get(k)
            log.append(e)
        steps.append({
            "id": s.get("id", i),
            "title": s.get("title", ""),
            "status": s.get("status") or "pending",
            "start_date": s.get("start_date"),
            "control_date": s.get("control_date"),
            "completed_date": s.get("completed_date"),
            "note": s.get("note"),
            "log": log,
        })

    out = dict(meta)  # сохраняем все исходные ключи — незнакомые уйдут в extra_json
    out["title"] = title
    out["steps"] = steps
    return out


def _normalize_note_meta(meta, body, stem):
    title = str(meta.get("title") or stem).strip()
    if not title:
        raise ValueError(f"{stem}: нет названия")
    out = dict(meta)
    out["slug"] = stem
    out["title"] = title
    out["body"] = body
    out["tags"] = meta.get("tags") or []
    out["aliases"] = meta.get("aliases") or meta.get("synonyms") or []
    return out


def migrate_markdown_vault(vault_path, db_path):
    """Одноразовый импорт markdown-вольта в SQLite. Каждый файл — отдельная
    транзакция: ошибка в одном файле не топит уже успешно перенесённые."""
    vault_path = Path(vault_path)
    conn = connect(db_path)
    try:
        broken = []
        tasks_count = 0
        notes_count = 0

        tasks_dir = vault_path / "Задачи"
        if tasks_dir.is_dir():
            for path in sorted(tasks_dir.glob("*.md")):
                try:
                    meta, body = _parse_md(path)
                except Exception as e:
                    broken.append({"file": path.name, "error": _short_err(e)})
                    continue
                if meta.get("type") != "task":
                    continue
                try:
                    task_meta = _normalize_task_meta(meta, path.stem)
                    with conn:
                        _write_task(conn, task_meta, body)
                    tasks_count += 1
                except Exception as e:
                    broken.append({"file": path.name, "error": _short_err(e)})

        base_dir = vault_path / "База"
        if base_dir.is_dir():
            for path in sorted(base_dir.glob("*.md")):
                try:
                    meta, body = _parse_md(path)
                except Exception as e:
                    broken.append({"file": path.name, "error": _short_err(e)})
                    continue
                if meta.get("type") != "note":
                    continue
                try:
                    note = _normalize_note_meta(meta, body, path.stem)
                    with conn:
                        _write_note(conn, note)
                    notes_count += 1
                except Exception as e:
                    broken.append({"file": path.name, "error": _short_err(e)})

        return {"tasks": tasks_count, "notes": notes_count, "broken": broken}
    finally:
        conn.close()


# --- CLI ---------------------------------------------------------------------

def _cli():
    import argparse

    parser = argparse.ArgumentParser(
        prog="storage.py",
        description="Слой хранения Yungdrung (SQLite). Команды: migrate.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_migrate = sub.add_parser("migrate", help="импорт markdown-вольта в SQLite")
    p_migrate.add_argument("vault_path", help="путь к вольту (там, где были Задачи/ и База/)")
    p_migrate.add_argument(
        "--db", default=None,
        help="путь к sqlite-файлу (по умолчанию <vault_path>/Вольт.sqlite)",
    )

    args = parser.parse_args()
    if args.cmd == "migrate":
        vault_path = Path(args.vault_path)
        db_path = Path(args.db) if args.db else vault_path / "Вольт.sqlite"
        result = migrate_markdown_vault(vault_path, db_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    # Как в engine.py: консоль Windows по умолчанию не UTF-8, а тут русский текст.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    _cli()
