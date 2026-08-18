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
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402
import settings as cfg  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"


def _param(path, name):
    """Один query-параметр или None. Для GET-запросов вроде предпросмотра
    шаблонов с датой старта — телу там взяться неоткуда."""
    значения = parse_qs(urlsplit(path).query).get(name)
    return значения[0] if значения else None


def _args(**поля):
    """Команды движка ждут объект с атрибутами — тот же, что даёт argparse.
    Так HTTP и командная строка зовут буквально одну функцию, и разойтись им
    негде."""
    поля.setdefault("force", False)
    поля.setdefault("reason", None)
    поля.setdefault("to", None)
    return SimpleNamespace(**поля)


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
        # Имя приходит из URL, поэтому проверяем, что оно не уводит из папки:
        # «..%2f..%2fetc/passwd» после декодирования — обычный путь наверх.
        path = (STATIC / name).resolve()
        if STATIC.resolve() not in path.parents or not path.is_file():
            return self._json(404, {"error": f"нет файла {name}"})
        self._send(200, path.read_bytes(), ctype)

    # --- маршруты ---

    def do_GET(self):
        route = unquote(self.path.split("?")[0])
        СТРАНИЦЫ = {"/": "feed.html", "/лента": "feed.html", "/новая": "index.html",
                    "/шаблоны": "templates.html", "/задача": "task.html",
                    "/задачи": "tasks.html", "/настройки": "settings.html"}
        if route in СТРАНИЦЫ:
            return self._static(СТРАНИЦЫ[route], "text/html; charset=utf-8")
        if route.endswith(".css"):
            return self._static(route.lstrip("/"), "text/css; charset=utf-8")
        if route.endswith(".js"):
            return self._static(route.lstrip("/"), "text/javascript; charset=utf-8")
        if route == "/api/feed":
            return self._json(200, engine.cmd_feed(_args(), date.today()))
        if route == "/api/backlog":
            return self._json(200, engine.cmd_backlog(_args(), date.today()))
        if route == "/api/templates":
            начало = _param(self.path, "start")
            return self._json(200, engine.cmd_templates(_args(start=начало), date.today()))
        if route == "/api/reasons":
            return self._json(200, {"reasons": engine.get_reasons()})
        if route == "/api/tasks":
            return self._json(200, engine.cmd_list(None, date.today()))
        if route == "/api/task":
            имя = _param(self.path, "name") or ""
            try:
                return self._json(200, engine.cmd_show(_args(task=имя), date.today()))
            except SystemExit as e:
                return self._json(404, {"error": str(e)})
        if route == "/api/backups":
            назначение = _param(self.path, "dest")
            return self._json(200, engine.cmd_backup_list(
                _args(dest=назначение), date.today()))
        if route == "/api/settings":
            return self._json(200, self._settings_load())
        self._json(404, {"error": "нет такого адреса"})

    def do_POST(self):
        route = unquote(self.path.split("?")[0])
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
        if route == "/api/action":
            return self._json(200, self._action(payload))
        if route == "/api/from-template":
            return self._json(200, engine.cmd_from_template(
                _args(name=payload.get("name"), start=payload.get("start"),
                      title=payload.get("title")), date.today()))
        if route == "/api/template-from-task":
            return self._json(200, engine.cmd_template_from_task(
                _args(task=payload.get("task"), name=payload.get("name")), date.today()))
        if route == "/api/recurrence-preview":
            return self._json(200, engine.cmd_recurrence_preview(
                _args(anchor=payload.get("anchor"), rule=payload.get("rule")), date.today()))
        if route == "/api/template-recurrence":
            return self._json(200, engine.cmd_set_recurrence(
                _args(name=payload.get("name"), rule=payload.get("rule"),
                      clear=bool(payload.get("clear"))), date.today()))
        if route == "/api/recur":
            return self._json(200, engine.cmd_recur(
                _args(name=payload.get("name"), force=bool(payload.get("force")),
                      limit=payload.get("limit")), date.today()))
        if route == "/api/task-update":
            return self._json(200, self._task_update(payload))
        if route == "/api/task-warnings":
            return self._json(200, {"warnings": engine.soft_warnings(payload, date.today())})
        if route == "/api/steps-check":
            return self._json(200, self._steps_check(payload))
        if route == "/api/task-cancel":
            return self._json(200, self._guarded(engine.cmd_cancel, _args(
                task=payload.get("task"), reason=payload.get("reason"))))
        if route == "/api/task-delete":
            return self._json(200, self._guarded(engine.cmd_delete,
                                                  _args(task=payload.get("task"))))
        if route == "/api/task-reopen":
            return self._json(200, self._guarded(engine.cmd_reopen, _args(
                task=payload.get("task"), step=str(payload.get("step") or ""))))
        if route == "/api/kb/scan":
            return self._json(200, engine.cmd_kb_scan(
                _args(text=payload.get("text"), source_type=payload.get("source_type"),
                      source_id=payload.get("source_id")), date.today()))
        if route == "/api/kb/confirm":
            return self._json(200, engine.cmd_kb_confirm(
                _args(source_type=payload.get("source_type"),
                      source_id=payload.get("source_id"),
                      mentions=payload.get("mentions")), date.today()))
        if route == "/api/kb/reject":
            return self._json(200, engine.cmd_kb_reject(
                _args(mention=payload.get("mention"),
                      mute=bool(payload.get("mute"))), date.today()))
        if route == "/api/backup":
            return self._json(200, engine.cmd_backup(
                _args(dest=payload.get("dest"), keep=payload.get("keep"),
                      force=bool(payload.get("force"))), date.today()))
        if route == "/api/restore":
            return self._json(200, engine.cmd_backup_restore(
                _args(file=payload.get("file")), date.today()))
        if route == "/api/export-json":
            return self._json(200, engine.cmd_export_json(
                _args(to=payload.get("to")), date.today()))
        if route == "/api/settings":
            return self._json(200, self._settings_save(payload))
        if route == "/api/reasons-add":
            return self._json(200, self._settings_op(cfg.add_reason, payload.get("name")))
        if route == "/api/reasons-rename":
            return self._json(200, self._settings_op(
                cfg.rename_reason, payload.get("old_name"), payload.get("new_name")))
        if route == "/api/reasons-archive":
            return self._json(200, self._settings_op(cfg.archive_reason, payload.get("name")))
        if route == "/api/tags-add":
            return self._json(200, self._settings_op(
                cfg.add_tag, payload.get("name"), payload.get("color"),
                bool(payload.get("pinned"))))
        if route == "/api/tags-rename":
            return self._json(200, self._tag_rewrite(
                cfg.rename_tag, payload.get("old_name"), payload.get("new_name")))
        if route == "/api/tags-toggle-pinned":
            return self._json(200, self._settings_op(cfg.toggle_tag_pinned, payload.get("name")))
        if route == "/api/tags-merge":
            return self._json(200, self._tag_rewrite(
                cfg.merge_tags, payload.get("source"), payload.get("target")))
        self._json(404, {"error": "нет такого адреса"})

    def _settings_path(self):
        # Тот же приём, что в engine._work: путь считаем от engine.VAULT, а не
        # от settings.cfg.VAULT — второй вычислен независимо при импорте и не
        # видит подмену вольта (тесты, --vault, что угодно другое).
        return cfg.settings_path(engine.VAULT)

    def _settings_load(self):
        try:
            data = cfg.load(self._settings_path())
        except cfg.SettingsError as e:
            return {"error": "; ".join(x.get("error", "") for x in e.errors)}
        # cfg.load() отдаёт start/end объектами time — для файла это верно
        # (settings.save сериализует их сама), а для голого json.dumps с
        # default=str time(9, 0) превращается в «09:00:00»: str(time) всегда
        # печатает секунды, даже нулевые. Форма ждёт «09:00», как и пишет файл.
        notif = data.get("notifications", {})
        for поле in ("start", "end"):
            if hasattr(notif.get(поле), "strftime"):
                notif[поле] = notif[поле].strftime("%H:%M")
        return data

    def _settings_save(self, payload):
        try:
            warnings = cfg.save(payload, self._settings_path())
            return {"ok": True, "warnings": warnings}
        except cfg.SettingsError as e:
            return {"ok": False, "errors": e.errors}

    def _settings_op(self, операция, *args):
        """Общая обёртка для действий над справочником причин и тегов: все они
        читают-меняют-пишут один и тот же файл и одинаково реагируют на ошибку
        валидации — писать эту тройку восемь раз незачем."""
        try:
            результат = операция(*args, path=self._settings_path())
            return {"ok": True, "result": результат[0] if isinstance(результат, tuple)
                    else результат}
        except cfg.SettingsError as e:
            return {"ok": False, "errors": e.errors}

    def _tag_rewrite(self, операция, old_name, new_name):
        """rename_tag и merge_tags меняют только справочник тегов — сами задачи
        правит движок отдельным шагом, см. `engine.rename_tag_everywhere`."""
        итог = self._settings_op(операция, old_name, new_name)
        if итог.get("ok"):
            итог["tasks_updated"] = engine.rename_tag_everywhere(old_name, new_name)
        return итог

    def _guarded(self, команда, args):
        """Обёртка для операций, которые движок останавливает через `sys.exit`
        на конфликте состояния (отменить уже отменённую, переоткрыть не сделанный
        шаг). CLI это прощает — процесс просто завершится с сообщением, странице
        же нужен JSON и код ответа, а не оборванное соединение."""
        try:
            return команда(args, date.today())
        except SystemExit as e:
            return {"ok": False, "errors": [{"field": None, "error": str(e)}]}

    # --- действия ---

    def _parse_date(self, payload):
        """Показать, во что превратится введённое. Разбирает движок, не браузер.

        Разбор возвращает либо дату, либо дату со временем («завтра 14:00»), и
        вычитать их из даты нельзя одинаково — раньше на вводе со временем запрос
        падал, а подсказка в форме молча оставалась пустой.
        """
        today = date.today()
        try:
            parsed = engine.parse_date_input(payload.get("text"), today)
        except (ValueError, TypeError):
            return {"ok": False}
        if parsed is None:
            return {"ok": True, "date": None, "label": None}

        день = engine.as_date(parsed)
        дни = (день - today).days
        подпись = {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get(дни)
        if подпись is None:
            подпись = f"{день:%d.%m.%Y}, " + (
                f"через {дни} дн." if дни > 0 else f"{-дни} дн. назад")
        if isinstance(parsed, datetime):
            подпись = f"{подпись} в {parsed:%H:%M}"
        return {"ok": True, "date": parsed.isoformat(), "label": подпись,
                "past": дни < 0}

    # Четыре исхода из раздела 6.4 ТЗ плюс «снят». Одной точкой входа, а не
    # четырьмя маршрутами: для морды это один и тот же жест «ответить про шаг»,
    # и добавление пятого исхода не должно трогать маршрутизацию.
    ДЕЙСТВИЯ = {
        "done": (engine.cmd_done, False),
        "notdone": (engine.cmd_notdone, True),
        "defer": (engine.cmd_defer, True),
        "fail": (engine.cmd_fail, True),
        "skip": (engine.cmd_skip, False),
    }

    def _action(self, payload):
        op = payload.get("op")
        if op not in self.ДЕЙСТВИЯ:
            return {"ok": False, "errors": [{"field": "op", "error": f"неизвестное действие: {op}"}]}
        команда, нужна_причина = self.ДЕЙСТВИЯ[op]

        ошибки = []
        if not payload.get("task") or payload.get("step") in (None, ""):
            ошибки.append({"field": None, "error": "не указан шаг"})
        причина = (payload.get("reason") or "").strip()
        if нужна_причина and not причина:
            ошибки.append({"field": "reason", "error": "причина обязательна"})

        today = date.today()
        дата = None
        сырая = (payload.get("to") or "").strip()
        if сырая:
            try:
                дата = engine.as_date(engine.parse_date_input(сырая, today))
            except (ValueError, TypeError):
                ошибки.append({"field": "to", "error": "Дату не понял. Можно: 18.08 · завтра · +3 · пн"})
        elif op == "defer":
            ошибки.append({"field": "to", "error": "нужна новая дата"})
        if ошибки:
            return {"ok": False, "errors": ошибки}

        args = _args(task=payload["task"], step=str(payload["step"]),
                     reason=причина or None,
                     to=дата.isoformat() if дата else None)
        try:
            return команда(args, today)
        except SystemExit as e:
            # Движок на конфликте состояния зовёт sys.exit с текстом. Для CLI это
            # нормально, для морды — нет: страница должна показать причину, а не
            # получить оборванное соединение.
            return {"ok": False, "errors": [{"field": None, "error": str(e)}]}

    def _task_update(self, payload):
        имя = payload.get("task")
        if not имя:
            return {"ok": False, "errors": [{"field": None, "error": "не указана задача"}]}
        args = _args(task=имя, json=json.dumps(payload.get("data") or {}),
                     force=bool(payload.get("force")))
        try:
            return engine.cmd_update(args, date.today())
        except SystemExit as e:
            return {"ok": False, "errors": [{"field": None, "error": str(e)}]}

    def _steps_check(self, payload):
        """Порядок дат сразу по мере ввода, не дожидаясь «Сохранить» — то же
        правило, что в validate_new_task/validate_task_edit (control не раньше
        start), но без записи и без требования названия задачи: форма спрашивает
        это на каждое изменение поля, а не один раз перед сохранением.
        """
        today = date.today()
        try:
            старт = engine.as_date(engine.parse_date_input(payload.get("start_date"), today)) \
                if (payload.get("start_date") or "").strip() else None
        except (ValueError, TypeError):
            старт = None

        имя = (payload.get("task") or "").strip()
        задача = None
        if имя:
            try:
                задача = engine.find_task(имя)
            except SystemExit:
                pass  # переименовывают на лету — считаем, что старых шагов ещё нет

        if задача:
            старые = {s["id"]: s for s in engine.steps_of(задача)}
            старт = старт or engine.as_date(задача["meta"].get("start_date")) or today
            resolved = engine.resolve_steps_for_edit(payload.get("steps") or [], старые,
                                                      старт, today)
        else:
            resolved = engine.resolve_steps(payload.get("steps") or [], старт or today, today)

        # Только про порядок дат — пустой заголовок посреди набора текста
        # не ошибка, а нормальное промежуточное состояние.
        errors = [e for r in resolved for e in r["errors"] if e["field"].endswith("control_date")]
        return {"errors": errors}

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
