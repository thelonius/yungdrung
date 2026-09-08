#!/usr/bin/env python3
"""Сервер для e2e: временный стор с десятью шагами на сегодня поверх десяти
тысяч закрытых задач, FastAPI на 8797 с собранным `apps/web/dist`.

Стор всегда временный: настоящий `стор.db` не читается и не пишется. Объём
закрытых — целевой профиль из PLAN.md: время ответа отметки в приёмке
должно мериться на нём, а не на пустой базе.
"""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

VAULT = Path(tempfile.mkdtemp(prefix="yungdrung-e2e-"))
os.environ["YUNGDRUNG_VAULT"] = str(VAULT)

import engine  # noqa: E402
import store  # noqa: E402

if not (ROOT / "apps" / "web" / "dist" / "index.html").is_file():
    sys.exit("нет apps/web/dist — сначала npm run build")

today = date.today()
for i in range(10):
    ответ = engine.cmd_create(SimpleNamespace(json=json.dumps({
        "title": f"Утренний шаг {i + 1:02d}",
        "tags": ["e2e"],
        "steps": [{"title": f"Сделать дело {i + 1}", "control_date": today.isoformat()},
                  {"title": "Следующее", "control_date": (today + timedelta(days=7)).isoformat()}],
    }), force=False, reason=None, to=None), today)
    assert ответ.get("ok"), ответ

conn = sqlite3.connect(str(VAULT / "стор.db"))
store.migrate_schema(conn)
conn.execute("PRAGMA synchronous=OFF")
давно = store._iso(today - timedelta(days=200))
conn.executemany(
    "INSERT INTO tasks (title, schema, created, start_date, cancelled, body) "
    "VALUES (?, 1, ?, ?, 0, '')",
    [(f"Закрытая задача {i:05d}", давно, давно) for i in range(10000)])
ids = [r[0] for r in conn.execute("SELECT id FROM tasks WHERE title LIKE 'Закрытая%'")]
conn.executemany(
    "INSERT INTO steps (task_id, step_id, position, title, status, completed_date) "
    "VALUES (?, 1, 0, 'Шаг', 'done', ?)",
    [(tid, давно) for tid in ids])
conn.executemany(
    "INSERT INTO step_log (task_id, step_id, date, event) VALUES (?, 1, ?, 'done')",
    [(tid, давно) for tid in ids])
conn.commit()
conn.close()

import uvicorn  # noqa: E402

uvicorn.run("api.app:app", host="127.0.0.1", port=8797, log_level="warning", access_log=False)
