#!/usr/bin/env python3
"""Проверка на здоровье слоёв (CLAUDE.md, §Архитектура; SLICE2_SPEC §7 п.3).

`core.tasks`, `core.templates`, `core.recur`, `core.attachments` — прикладной
слой над стором и движком шагов, а `engine.py` — CLI-адаптер поверх него.
Зависимость должна идти в одну сторону: `core` не имеет права тянуть за собой
`engine` (там `argparse`, глобальный `VAULT`, `sys.exit`). Импорт настоящим
процессом, а не monkeypatch, честнее: только так видно, что `core.*` не тащит
`engine` транзитивно через какой-нибудь чужой импорт.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHECK = (
    "import sys, core.tasks, core.templates, core.recur, core.attachments; "
    "assert 'engine' not in sys.modules, sorted(m for m in sys.modules if m == 'engine')"
)


def test_core_не_тянет_engine():
    result = subprocess.run(
        [sys.executable, "-c", CHECK],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_openapi_содержит_только_api_v1():
    """Дополняет `test_api.py::test_легаси_маршрутов_нет_в_openapi`: здесь —
    что вообще все пути схемы среза 2 под `/api/v1/`, список из критерия
    приёмки §7 п.4 спецификации."""
    paths = json.loads((ROOT / "api" / "openapi.json").read_text(encoding="utf-8"))["paths"]
    assert all(k.startswith("/api/v1/") for k in paths)
    need = {
        "/api/v1/tasks", "/api/v1/tasks/quick", "/api/v1/tasks/plan",
        "/api/v1/tasks/resolve", "/api/v1/tasks/{task_id}",
        "/api/v1/tasks/{task_id}/cancel", "/api/v1/tasks/{task_id}/close",
        "/api/v1/tasks/{task_id}/steps/{step_id}/reopen",
        "/api/v1/tasks/{task_id}/attachments",
        "/api/v1/tasks/{task_id}/kb-confirm",
        "/api/v1/kb/scan", "/api/v1/kb/reject",
        "/api/v1/templates", "/api/v1/templates/preview",
        "/api/v1/templates/from-task", "/api/v1/templates/{name}",
        "/api/v1/templates/{name}/preview",
        "/api/v1/templates/{name}/instantiate",
        "/api/v1/templates/{name}/recurrence",
        "/api/v1/templates/{name}/attachments",
        "/api/v1/recurrence/parse", "/api/v1/recurrence/preview",
        "/api/v1/attachments/{id}", "/api/v1/attachments/{id}/bytes",
    }
    missing = need - set(paths)
    assert not missing, missing
