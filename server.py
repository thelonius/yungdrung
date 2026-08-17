#!/usr/bin/env python3
"""Форма ввода задач. Локальная страница, никуда не смотрит наружу.

  python3 server.py            поднять на 127.0.0.1:8765 и открыть браузер
  python3 server.py --port 9000 --no-open

Сервер ничего не пишет в вольт сам: он зовёт функции движка. Правило единственного
писателя остаётся в силе, а валидация живёт в одном месте — иначе форма и CLI
разойдутся, и в вольт попадёт то, что движок потом не прочитает.

Слушает только петлевой адрес. Наружу этот сервис смотреть не должен: он пишет
файлы в вольт и не имеет никакой аутентификации.
"""
import argparse
import json
import sys
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        """Стандартный лог сыплет строкой на каждый запрос, включая опрос дат при
        каждом нажатии клавиши. Оставляем только ошибки."""
        if not str(args[1] if len(args) > 1 else "").startswith(("2", "3")):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # --- отдача ---

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # Форма правит файлы на диске: пусть никакая посторонняя страница не
        # сможет дёрнуть её запросом из соседней вкладки.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str))

    def _static(self, name, ctype):
        path = STATIC / name
        if not path.is_file():
            return self._json(404, {"error": f"нет файла {name}"})
        self._send(200, path.read_bytes(), ctype)

    # --- маршруты ---

    def do_GET(self):
        route = self.path.split("?")[0]
        if route in ("/", "/index.html"):
            return self._static("index.html", "text/html; charset=utf-8")
        if route == "/style.css":
            return self._static("style.css", "text/css; charset=utf-8")
        if route == "/app.js":
            return self._static("app.js", "text/javascript; charset=utf-8")
        if route == "/api/tasks":
            return self._json(200, engine.cmd_list(None, date.today()))
        self._json(404, {"error": "нет такого адреса"})

    def do_POST(self):
        route = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or "{}")
        except (ValueError, json.JSONDecodeError) as e:
            return self._json(400, {"ok": False,
                                    "errors": [{"field": None, "error": f"битый запрос: {e}"}]})

        if route == "/api/parse-date":
            return self._json(200, self._parse_date(payload))
        if route == "/api/create":
            return self._json(200, self._create(payload))
        self._json(404, {"error": "нет такого адреса"})

    # --- действия ---

    def _parse_date(self, payload):
        """Показать, во что превратится введённое. Разбирает движок, не браузер."""
        today = date.today()
        try:
            parsed = engine.parse_date_input(payload.get("text"), today)
        except (ValueError, TypeError):
            return {"ok": False}
        if parsed is None:
            return {"ok": True, "date": None, "label": None}
        дни = (parsed - today).days
        подпись = {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get(дни)
        if подпись is None:
            подпись = f"{parsed:%d.%m.%Y}, " + (
                f"через {дни} дн." if дни > 0 else f"{-дни} дн. назад")
        return {"ok": True, "date": parsed.isoformat(), "label": подпись,
                "past": дни < 0}

    def _create(self, payload):
        today = date.today()
        existing = [t["path"].stem for t in engine.load_tasks()]
        errors = engine.validate_new_task(payload, existing, today)
        if errors:
            return {"ok": False, "errors": errors}

        meta = engine.build_task(payload, today)
        path = engine.TASKS_DIR / f"{meta['title']}.md"
        if path.exists():
            return {"ok": False,
                    "errors": [{"field": "title", "error": "Файл уже существует"}]}
        task = {"path": path, "meta": meta,
                "body": (payload.get("body") or "").strip() + "\n"}
        engine.save(task, today)
        return {"ok": True, "task": path.stem, "status": task["meta"]["status"],
                "steps": len(meta["steps"])}


def main():
    ap = argparse.ArgumentParser(description="Форма ввода задач Yungdrung")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="не открывать браузер")
    args = ap.parse_args()

    if not engine.TASKS_DIR.is_dir():
        sys.exit(f"нет папки задач: {engine.TASKS_DIR}")

    адрес = f"http://127.0.0.1:{args.port}/"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Форма ввода задач: {адрес}")
    print(f"Вольт: {engine.VAULT}")
    print("Остановить — Ctrl+C")
    if not args.no_open:
        threading.Timer(0.5, webbrowser.open, [адрес]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлен")


if __name__ == "__main__":
    main()
