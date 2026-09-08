#!/usr/bin/env python3
"""Снимок OpenAPI приложения в `api/openapi.json`.

Файл в git: из него `make types` собирает `apps/web/src/api/schema.d.ts`, а
тест `test_снимок_openapi_свежий` следит, чтобы модель, поменянная в
`core/models.py`, не разошлась со снимком молча.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.app import app  # noqa: E402

if __name__ == "__main__":
    out = ROOT / "api" / "openapi.json"
    out.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"{out.relative_to(ROOT)}: {len(app.openapi()['paths'])} путей")
