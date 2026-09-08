"""Приложение FastAPI: страницы из `static/`, легаси-маршруты, `/api/v1`.

Запуск — `server.py` (он же открывает браузер) или напрямую:

    python3 -m uvicorn api.app:app --host 127.0.0.1 --port 8765
"""
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from api import errors, legacy
from api.v1 import router as v1

STATIC = Path(__file__).resolve().parent.parent / "static"
# Собранный клиент (REFACTOR.md, срез 1c). В git его нет: собирает
# `tools/update.py` на машине заказчика. Нет каталога — нет и маршрута, старые
# страницы работают как раньше.
DIST = Path(__file__).resolve().parent.parent / "apps" / "web" / "dist"

# Пользовательские маршруты по-русски — их видит человек, они не меняются
# (решение в REFACTOR.md). Пока страницы старые; новое приложение занимает `/`
# в срезе 1c, старая лента переезжает на `/старая`.
PAGES = {
    "/старая": "feed.html", "/новая": "index.html",
    "/шаблоны": "templates.html", "/задача": "task.html", "/настройки": "settings.html",
    "/архив": "archive.html", "/задачи": "list.html", "/база": "kb.html",
    "/база/запись": "kb-note.html",
}
# Лента — новое приложение (`apps/web/dist`, срез 1c принят 2026-09-08). Нет
# сборки — отдаётся прежняя страница: трекер работает и до первого
# `tools/update.py`, просто без клавиатуры и отмены.
FEED_ROUTES = ("/", "/лента")

app = FastAPI(title="Yungdrung", version="1",
              description="Трекер задач с последовательными шагами и контрольным временем. "
                          "Пишет только ядро, считает только ядро; оболочка показывает то, "
                          "что пришло (CONTRACT.md).",
              docs_url="/docs", redoc_url=None)
errors.install(app)
app.include_router(v1.router)
app.include_router(legacy.router)


def _not_found():
    return Response(json.dumps({"error": "нет такого адреса"}, ensure_ascii=False),
                    status_code=404, media_type="application/json; charset=utf-8")


def _static(name: str, media_type: str):
    # Имя приходит из URL: проверяем, что оно не уводит из папки.
    path = (STATIC / name).resolve()
    if STATIC.resolve() not in path.parents or not path.is_file():
        return Response(json.dumps({"error": f"нет файла {name}"}, ensure_ascii=False),
                        status_code=404, media_type="application/json; charset=utf-8")
    return FileResponse(path, media_type=media_type,
                        headers={"X-Content-Type-Options": "nosniff"})


def _feed_page():
    if (DIST / "index.html").is_file():
        return FileResponse(DIST / "index.html", media_type="text/html; charset=utf-8",
                            headers={"X-Content-Type-Options": "nosniff"})
    return _static("feed.html", "text/html; charset=utf-8")


for _route in FEED_ROUTES:
    app.add_api_route(_route, _feed_page, methods=["GET"], include_in_schema=False)

if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")


for _route, _file in PAGES.items():
    app.add_api_route(_route, (lambda f: (lambda: _static(f, "text/html; charset=utf-8")))(_file),
                      methods=["GET"], include_in_schema=False)


@app.get("/{name}.css", include_in_schema=False)
def css(name: str):
    return _static(f"{name}.css", "text/css; charset=utf-8")


@app.get("/{name}.js", include_in_schema=False)
def js(name: str):
    return _static(f"{name}.js", "text/javascript; charset=utf-8")


@app.get("/{rest:path}", include_in_schema=False)
def fallback(rest: str):
    return _not_found()


@app.post("/{rest:path}", include_in_schema=False)
def fallback_post(rest: str):
    return _not_found()
