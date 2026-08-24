#!/usr/bin/env python3
"""SQLite-хранилище задач и шагов — переезд с markdown-вольта.

Единственный слой в проекте, который знает про SQL. Всё, что вокруг —
`engine.py`, `server.py` — работает со словарями `{"path": TaskRef, "meta":
{...}, "body": str}`, той же формой, что раньше давал разбор YAML. Так весь
блок вычислений в `engine.py` (task_status, current_step, is_closed,
collect_open и остальные) не тронут переездом вообще — они уже работают над
словарём, а не над файлом.

Без пула соединений: `server.py` — `ThreadingHTTPServer`, а `sqlite3.Connection`
нельзя шарить между потоками без ручной дисциплины блокировок. Каждый метод
открывает своё соединение, работает внутри `with conn:` (автокоммит на успехе,
автооткат на исключении) и закрывает — для локального однопользовательского
файла это дешевле, чем городить пул ради несуществующей конкурентности.

Теги на задаче остаются свободным списком имён в `meta["tags"]`, как и раньше:
`build_task`/`apply_task_edit` никогда не проверяли имя тега по справочнику
настроек, и это не баг, а расчёт — тег, которого ещё нет в реестре, заводится
здесь же с цветом по умолчанию, а не отбрасывается.
"""
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# Версия схемы БД, она же PRAGMA user_version. Поднимается на единицу при
# каждом несовместимом изменении структуры; migrate_schema доращивает старый
# файл по шагам. База заказчика живёт у него и не синхронизирована с нашей —
# обновление кода через git pull обязано молча и безопасно доводить его файл
# до текущей версии при первом же открытии.
SCHEMA = 2

DEFAULT_TAG_COLOR = "#999999"

# Поля задачи, которые движок вычисляет заново при каждом save() (статус,
# текущий шаг, дата контроля, буксование, прогресс) сюда не идут — колонок под
# них нет. Раньше это была денормализация под таблицу Obsidian Bases; без
# Obsidian в них нет смысла, а source of truth и так остаётся в шагах.
SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tasks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT NOT NULL UNIQUE,
    schema            INTEGER NOT NULL DEFAULT 1,
    created           TEXT NOT NULL,
    start_date        TEXT NOT NULL,
    cancelled         INTEGER NOT NULL DEFAULT 0,
    cancelled_reason  TEXT,
    body              TEXT NOT NULL DEFAULT '',
    extra             TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    task_id           INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_id           INTEGER NOT NULL,
    position          INTEGER NOT NULL,
    title             TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    start_date        TEXT,
    control_date      TEXT,
    completed_date    TEXT,
    note              TEXT,
    parent_id         INTEGER,
    mode              TEXT,
    PRIMARY KEY (task_id, step_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_steps_position ON steps(task_id, position);

CREATE TABLE IF NOT EXISTS step_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id           INTEGER NOT NULL,
    step_id           INTEGER NOT NULL,
    date              TEXT NOT NULL,
    event             TEXT NOT NULL,
    reason            TEXT,
    was               TEXT,
    to_date           TEXT,
    FOREIGN KEY (task_id, step_id) REFERENCES steps(task_id, step_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_step_log_step ON step_log(task_id, step_id, id);

CREATE TABLE IF NOT EXISTS tags (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    color   TEXT NOT NULL,
    pinned  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS task_tags (
    task_id  INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE RESTRICT,
    PRIMARY KEY (task_id, tag_id)
);

CREATE TABLE IF NOT EXISTS reasons (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    archived  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS kb_notes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    aliases      TEXT,
    body         TEXT NOT NULL DEFAULT '',
    legacy_file  TEXT,
    extra        TEXT
);

CREATE TABLE IF NOT EXISTS kb_links (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_entry_id   INTEGER NOT NULL REFERENCES kb_notes(id) ON DELETE CASCADE,
    source_type   TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    offset_start  INTEGER NOT NULL,
    offset_end    INTEGER NOT NULL,
    confirmed_at  TEXT NOT NULL,
    matched       TEXT NOT NULL,
    UNIQUE (kb_entry_id, source_type, source_id, offset_start, offset_end)
);
CREATE INDEX IF NOT EXISTS idx_kb_links_source ON kb_links(source_type, source_id);

CREATE TABLE IF NOT EXISTS kb_exclusions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_entry_id  INTEGER REFERENCES kb_notes(id) ON DELETE CASCADE,
    text         TEXT NOT NULL
);

-- Метаданные файлов-вложений (attachments.py) — задача или шаг, по образцу
-- kb_links. Байты живут на диске под sha256, здесь только описание. Новая
-- таблица не требует ALTER на старой базе: CREATE IF NOT EXISTS её заводит
-- сама при первом подключении после обновления кода.
CREATE TABLE IF NOT EXISTS attachments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type  TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    filename     TEXT NOT NULL,
    mime         TEXT NOT NULL,
    bytes        INTEGER NOT NULL,
    caption      TEXT,
    added        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attachments_source ON attachments(source_type, source_id);
"""

# Поля задачи, которые движок сам вычисляет при каждом save() и которые
# _assemble_task не читает из строки — их не нужно путать с «неизвестными
# полями фронтматтера» при сборке extra на будущих этапах.
_KNOWN_TASK_FIELDS = {
    "schema", "type", "title", "created", "start_date", "cancelled",
    "cancelled_reason", "tags", "steps", "status", "current_step",
    "control_date", "progress", "stalled",
}


@dataclass(frozen=True)
class TaskRef:
    """Замена `Path` в форме задачи. `.stem` называется так же, как у Path,
    чтобы `task["path"].stem` по всему engine.py остался рабочим без правок."""
    id: int
    stem: str


class DuplicateTitle(Exception):
    """Название уже занято другой задачей — тот же смысл, что раньше нёс
    `path.exists()`, только источник истины теперь UNIQUE(title) в БД."""


def migrate_schema(conn):
    """Создать недостающее и дорастить старый файл до текущей SCHEMA.

    CREATE IF NOT EXISTS покрывает только новые таблицы; колонки в уже
    существующей таблице он не добавит. Поэтому рядом — ALTER TABLE по
    версиям, под защитой PRAGMA user_version: на актуальном файле проверка
    стоит одно чтение прагмы. Данные миграция не переписывает никогда —
    только добавляет пустые колонки.

    v2: parent_id и mode у шагов — группы подшагов (последовательные и
    параллельные). Старые шаги получают NULL, то есть остаются плоской
    последовательной цепочкой — поведение до миграции.
    """
    conn.executescript(SCHEMA_SQL)
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA:
        имеющиеся = {r[1] for r in conn.execute("PRAGMA table_info(steps)")}
        if "parent_id" not in имеющиеся:
            conn.execute("ALTER TABLE steps ADD COLUMN parent_id INTEGER")
            conn.execute("ALTER TABLE steps ADD COLUMN mode TEXT")
        conn.execute(f"PRAGMA user_version = {SCHEMA}")


def _iso(value):
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _parse_date_or_datetime(value):
    """Обратное `_iso` для дат: по длине строки различаем «весь день» от
    «дата со временем» — ровно то же разделение, что в as_date/parse_time_part."""
    if value is None:
        return None
    return datetime.fromisoformat(value) if len(value) > 10 else date.fromisoformat(value)


class Store:
    def __init__(self, db_path):
        self.path = Path(db_path)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=5)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        migrate_schema(conn)
        return conn

    # --- чтение --------------------------------------------------------

    def load_tasks(self):
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY title").fetchall()
            return [self._assemble_task(conn, row) for row in rows]

    def _assemble_task(self, conn, row):
        steps = conn.execute(
            "SELECT * FROM steps WHERE task_id=? ORDER BY position", (row["id"],)
        ).fetchall()
        tags = conn.execute(
            "SELECT t.name FROM tags t JOIN task_tags tt ON tt.tag_id = t.id "
            "WHERE tt.task_id=? ORDER BY t.name", (row["id"],)
        ).fetchall()
        meta = {
            "schema": row["schema"],
            "type": "task",
            "title": row["title"],
            "created": _parse_date_or_datetime(row["created"]),
            "start_date": _parse_date_or_datetime(row["start_date"]),
            "tags": [t["name"] for t in tags],
            "steps": [self._assemble_step(conn, s) for s in steps],
        }
        if row["cancelled"]:
            meta["cancelled"] = True
            meta["cancelled_reason"] = row["cancelled_reason"]
        if row["extra"]:
            meta.update(json.loads(row["extra"]))
        return {"path": TaskRef(row["id"], row["title"]), "meta": meta,
                "body": row["body"] or ""}

    def _assemble_step(self, conn, s):
        log = conn.execute(
            "SELECT * FROM step_log WHERE task_id=? AND step_id=? ORDER BY id",
            (s["task_id"], s["step_id"]),
        ).fetchall()
        return {
            "id": s["step_id"],
            "title": s["title"],
            "status": s["status"],
            "start_date": _parse_date_or_datetime(s["start_date"]),
            "control_date": _parse_date_or_datetime(s["control_date"]),
            "completed_date": _parse_date_or_datetime(s["completed_date"]),
            "note": s["note"],
            "parent": s["parent_id"],
            "mode": s["mode"],
            "log": [self._assemble_log_entry(e) for e in log],
        }

    @staticmethod
    def _assemble_log_entry(e):
        entry = {"date": _parse_date_or_datetime(e["date"]), "event": e["event"]}
        if e["reason"] is not None:
            entry["reason"] = e["reason"]
        if e["was"] is not None:
            entry["was"] = _parse_date_or_datetime(e["was"])
        if e["to_date"] is not None:
            entry["to"] = _parse_date_or_datetime(e["to_date"])
        return entry

    def find_task_id(self, title):
        """Точное совпадение по названию — id или None. Используется миграцией
        и разбором ссылок базы знаний, не нечётким поиском задачи."""
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM tasks WHERE title=?", (title,)).fetchone()
            return row["id"] if row else None

    # --- запись ----------------------------------------------------------

    def _resolve_tag_ids(self, conn, names):
        """Имя тега → id, заводя отсутствующий тег с цветом по умолчанию.
        Реестр тегов (цвет/закрепление) сегодня не проверяет имена на задаче —
        см. модульный docstring; переезд это поведение не меняет."""
        ids = []
        for name in names:
            row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO tags (name, color, pinned) VALUES (?, ?, 0)",
                    (name, DEFAULT_TAG_COLOR))
                ids.append(cur.lastrowid)
            else:
                ids.append(row["id"])
        return ids

    def save_task(self, task, today):
        """Создать или переписать задачу целиком — один путь для обоих,
        различаются только по наличию `task["path"].id`.

        Шаги и журнал каждый раз удаляются и вставляются заново (каскадом от
        `DELETE FROM steps`), а не диффятся построчно: шагов у задачи мало,
        входящий список из `steps_of(task)` уже полный и авторитетный (его же
        `apply_task_edit`/`build_task` строят целиком), и рассинхронизация от
        частичного апдейта опаснее, чем цена пересоздания нескольких строк.
        """
        meta = task["meta"]
        ref = task["path"]
        title = meta["title"]
        extra = {k: v for k, v in meta.items() if k not in _KNOWN_TASK_FIELDS}

        with self._connect() as conn:
            try:
                if ref is None or ref.id is None:
                    cur = conn.execute(
                        "INSERT INTO tasks (title, schema, created, start_date, "
                        "cancelled, cancelled_reason, body, extra) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (title, meta.get("schema", SCHEMA), _iso(meta["created"]),
                         _iso(meta["start_date"]), int(bool(meta.get("cancelled"))),
                         meta.get("cancelled_reason"), task["body"],
                         json.dumps(extra, default=str) if extra else None))
                    task_id = cur.lastrowid
                else:
                    task_id = ref.id
                    conn.execute(
                        "UPDATE tasks SET title=?, schema=?, created=?, start_date=?, "
                        "cancelled=?, cancelled_reason=?, body=?, extra=? WHERE id=?",
                        (title, meta.get("schema", SCHEMA), _iso(meta["created"]),
                         _iso(meta["start_date"]), int(bool(meta.get("cancelled"))),
                         meta.get("cancelled_reason"), task["body"],
                         json.dumps(extra, default=str) if extra else None, task_id))
            except sqlite3.IntegrityError as e:
                raise DuplicateTitle(title) from e

            conn.execute("DELETE FROM steps WHERE task_id=?", (task_id,))
            for position, step in enumerate(meta.get("steps") or []):
                conn.execute(
                    "INSERT INTO steps (task_id, step_id, position, title, status, "
                    "start_date, control_date, completed_date, note, parent_id, mode) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (task_id, step["id"], position, step["title"],
                     step.get("status", "pending"), _iso(step.get("start_date")),
                     _iso(step.get("control_date")), _iso(step.get("completed_date")),
                     step.get("note"), step.get("parent"), step.get("mode")))
                for entry in step.get("log") or []:
                    conn.execute(
                        "INSERT INTO step_log (task_id, step_id, date, event, reason, "
                        "was, to_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (task_id, step["id"], _iso(entry.get("date")), entry["event"],
                         entry.get("reason"), _iso(entry.get("was")),
                         _iso(entry.get("to"))))

            conn.execute("DELETE FROM task_tags WHERE task_id=?", (task_id,))
            for tag_id in self._resolve_tag_ids(conn, meta.get("tags") or []):
                conn.execute(
                    "INSERT OR IGNORE INTO task_tags (task_id, tag_id) VALUES (?, ?)",
                    (task_id, tag_id))

        task["path"] = TaskRef(task_id, title)

    def delete_task(self, task_id):
        with self._connect() as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    # --- вложения --------------------------------------------------------

    def add_attachment(self, source_type, source_id, sha256, filename, mime,
                       size, caption, added):
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO attachments (source_type, source_id, sha256, filename, "
                "mime, bytes, caption, added) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (source_type, source_id, sha256, filename, mime, size, caption,
                 _iso(added)))
            return cur.lastrowid

    def list_attachments(self, source_type, source_id):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM attachments WHERE source_type=? AND source_id=? "
                "ORDER BY id", (source_type, source_id)).fetchall()
            return [dict(r) for r in rows]

    def get_attachment(self, attachment_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
            return dict(row) if row else None

    def delete_attachment(self, attachment_id):
        # Файл на диске не трогаем: он адресован содержимым, тот же sha256
        # может принадлежать ещё одной строке (та же картинка в другой
        # задаче), да и мусор от него безопасен — не порча, просто лишний
        # файл, который однажды подберёт отдельная команда сборки.
        with self._connect() as conn:
            conn.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))

    def rename_tag_everywhere(self, old_name, new_name):
        """Переименование ИЛИ слияние — вызывающий (engine.rename_tag_everywhere)
        не различает их, потому что settings.py тоже не различает: `rename_tag`
        и `merge_tags` в справочнике уже отработали до этого вызова, здесь
        только задачи. Если целевого имени в `tags` ещё нет — просто переименовать
        строку. Если есть — это слияние: перевесить `task_tags` на существующий
        id цели (без дублей) и убрать исходную строку. Простой `UPDATE ... SET
        name=` на слиянии упал бы в UNIQUE(name) — target уже занят.

        Возвращает число задач, у которых стоял old_name (для отчёта в API).
        """
        with self._connect() as conn:
            source = conn.execute("SELECT id FROM tags WHERE name=?", (old_name,)).fetchone()
            if source is None:
                return 0
            source_id = source["id"]
            задето = conn.execute(
                "SELECT COUNT(*) FROM task_tags WHERE tag_id=?", (source_id,)
            ).fetchone()[0]

            target = conn.execute("SELECT id FROM tags WHERE name=?", (new_name,)).fetchone()
            if target is None:
                conn.execute("UPDATE tags SET name=? WHERE id=?", (new_name, source_id))
            else:
                target_id = target["id"]
                conn.execute(
                    "INSERT OR IGNORE INTO task_tags (task_id, tag_id) "
                    "SELECT task_id, ? FROM task_tags WHERE tag_id=?",
                    (target_id, source_id))
                conn.execute("DELETE FROM task_tags WHERE tag_id=?", (source_id,))
                conn.execute("DELETE FROM tags WHERE id=?", (source_id,))
            return задето
