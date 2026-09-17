"""Маршруты прежнего `server.py`, перенесённые механически.

Каждый маршрут — тот же вызов `engine.cmd_x(_args(...))`, что был в цепочке
`if route ==`, с тем же шимом `_args` и той же формой ответа: старые страницы
в `static/` работают без правок. Сюда **ничего не дописывается**: новая
потребность идёт в `api/v1`, а этот файл тает по мере переезда страниц
(REFACTOR.md, срезы 2–3) и удаляется целиком после последней.

Ответы сериализуются как раньше — `json.dumps(..., default=str)`: `datetime`
в старых ответах печатается через пробел, а не «T», и страницы на это
рассчитывают. В OpenAPI маршруты не попадают.
"""
import base64
import json
from datetime import date, datetime
from types import SimpleNamespace

import engine
import settings as cfg
import templates as tpl
from fastapi import APIRouter, Request, Response

router = APIRouter(include_in_schema=False)


def _args(**поля):
    поля.setdefault("force", False)
    поля.setdefault("reason", None)
    поля.setdefault("to", None)
    return SimpleNamespace(**поля)


def _json(obj, code=200) -> Response:
    return Response(json.dumps(obj, ensure_ascii=False, default=str), status_code=code,
                    media_type="application/json; charset=utf-8",
                    headers={"X-Content-Type-Options": "nosniff"})


def _guarded(команда, args):
    """Движок останавливает конфликт состояния через `sys.exit`; странице нужен
    JSON, а не оборванное соединение."""
    try:
        return команда(args, date.today())
    except SystemExit as e:
        return {"ok": False, "errors": [{"field": None, "error": str(e)}]}


def _or_404(команда, args):
    try:
        return _json(команда(args, date.today()))
    except SystemExit as e:
        return _json({"error": str(e)}, 404)


# --- настройки -------------------------------------------------------------

def _settings_path():
    return cfg.settings_path(engine.VAULT)


def _settings_load():
    try:
        data = cfg.load(_settings_path())
    except cfg.SettingsError as e:
        return {"error": "; ".join(x.get("error", "") for x in e.errors)}
    # start/end — объекты time; форма ждёт «09:00», а str(time) печатает секунды.
    notif = data.get("notifications", {})
    for поле in ("start", "end"):
        if hasattr(notif.get(поле), "strftime"):
            notif[поле] = notif[поле].strftime("%H:%M")
    return data


def _settings_save(payload):
    try:
        return {"ok": True, "warnings": cfg.save(payload, _settings_path())}
    except cfg.SettingsError as e:
        return {"ok": False, "errors": e.errors}


def _settings_op(операция, *args):
    try:
        результат = операция(*args, path=_settings_path())
        return {"ok": True, "result": результат[0] if isinstance(результат, tuple)
                else результат}
    except cfg.SettingsError as e:
        return {"ok": False, "errors": e.errors}


def _tag_rewrite(операция, old_name, new_name):
    итог = _settings_op(операция, old_name, new_name)
    if итог.get("ok"):
        итог["tasks_updated"] = engine.rename_tag_everywhere(old_name, new_name)
    return итог


# --- действия ---------------------------------------------------------------

def _parse_date(payload):
    today = date.today()
    try:
        parsed = engine.parse_date_input(payload.get("text"), today, now=datetime.now())
    except (ValueError, TypeError):
        return {"ok": False}
    if parsed is None:
        return {"ok": True, "date": None, "label": None}
    день = engine.as_date(parsed)
    дни = (день - today).days
    подпись = {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get(дни)
    if подпись is None:
        подпись = f"{день:%d.%m.%Y}, " + (f"через {дни} дн." if дни > 0 else f"{-дни} дн. назад")
    if isinstance(parsed, datetime):
        подпись = f"{подпись} в {parsed:%H:%M}"
    return {"ok": True, "date": parsed.isoformat(), "label": подпись, "past": дни < 0}


ДЕЙСТВИЯ = {
    "done": (engine.cmd_done, False),
    "notdone": (engine.cmd_notdone, True),
    "defer": (engine.cmd_defer, True),
    "fail": (engine.cmd_fail, True),
    "skip": (engine.cmd_skip, False),
    "undo": (engine.cmd_undo, False),
}


def _parse_to(payload, today):
    сырая = (payload.get("to") or "").strip()
    if not сырая:
        return None, None
    try:
        return engine.parse_date_input(сырая, today, now=datetime.now()), None
    except (ValueError, TypeError):
        return None, {"field": "to",
                      "error": "Дату не понял. Можно: 18.08 · 15 марта · завтра · "
                               "+3 · пн · полдесятого · через час"}


def _action(payload):
    op = payload.get("op")
    if op not in ДЕЙСТВИЯ:
        return {"ok": False, "errors": [{"field": "op", "error": f"неизвестное действие: {op}"}]}
    команда, нужна_причина = ДЕЙСТВИЯ[op]
    ошибки = []
    if not payload.get("task") or payload.get("step") in (None, ""):
        ошибки.append({"field": None, "error": "не указан шаг"})
    причина = (payload.get("reason") or "").strip()
    if нужна_причина and not причина:
        ошибки.append({"field": "reason", "error": "причина обязательна"})
    today = date.today()
    дата, ошибка_даты = _parse_to(payload, today)
    if ошибка_даты:
        ошибки.append(ошибка_даты)
    elif дата is None and op == "defer":
        ошибки.append({"field": "to", "error": "нужна новая дата"})
    if ошибки:
        return {"ok": False, "errors": ошибки}
    args = _args(task=payload["task"], step=str(payload["step"]), reason=причина or None,
                 to=tpl.control_text(дата) if дата else None)
    return _guarded(команда, args)


def _backlog_bulk(payload):
    today = date.today()
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return {"ok": False, "errors": [{"field": "items", "error": "пустой список"}]}
    дата, ошибка_даты = _parse_to(payload, today)
    if ошибка_даты:
        return {"ok": False, "errors": [ошибка_даты]}
    args = _args(op=payload.get("op"), items=items,
                 reason=(payload.get("reason") or "").strip() or None,
                 to=tpl.control_text(дата) if дата else None)
    return engine.cmd_backlog_bulk(args, today)


def _task_update(payload):
    имя = payload.get("task")
    if not имя:
        return {"ok": False, "errors": [{"field": None, "error": "не указана задача"}]}
    return _guarded(engine.cmd_update, _args(
        task=имя, json=json.dumps(payload.get("data") or {}), force=bool(payload.get("force"))))


def _steps_check(payload):
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
            pass
    if задача:
        старые = {s["id"]: s for s in engine.steps_of(задача)}
        старт = старт or engine.as_date(задача["meta"].get("start_date")) or today
        resolved = engine.resolve_steps(payload.get("steps") or [], старт, today, старые=старые)
    else:
        resolved = engine.resolve_steps(payload.get("steps") or [], старт or today, today)
    errors = [e for r in engine.walk_resolved(resolved) for e in r["errors"]
              if e["field"].endswith("control_date")]
    return {"errors": errors}


def _attachment_add(payload):
    try:
        data = base64.b64decode(payload.get("data") or "", validate=True)
    except (ValueError, TypeError) as e:
        return {"ok": False, "errors": [{"field": "data", "error": f"битый файл: {e}"}]}
    return engine.cmd_attach(_args(
        task=payload.get("task"), step=payload.get("step"), template=payload.get("template"),
        filename=payload.get("filename"), caption=payload.get("caption"), data=data),
        date.today())


# --- таблицы маршрутов ------------------------------------------------------
#
# Ключ — путь, значение — функция от параметров запроса (GET) или тела (POST),
# возвращающая объект для `_json` или готовый `Response`. Тот же порядок, что
# был в `do_GET`/`do_POST`.

def _today():
    return date.today()


GET = {
    "/api/feed": lambda q: engine.cmd_feed(_args(), _today()),
    "/api/backlog": lambda q: engine.cmd_backlog(_args(), _today()),
    "/api/templates": lambda q: engine.cmd_templates(_args(start=q.get("start")), _today()),
    "/api/archive": lambda q: engine.cmd_archive(
        _args(tag=q.get("tag"), since=q.get("since"), until=q.get("until")), _today()),
    "/api/search": lambda q: engine.cmd_search(
        _args(text=q.get("q"), kind=q.get("kind"), limit=q.get("limit")), _today()),
    "/api/reasons": lambda q: {"reasons": engine.get_reasons()},
    "/api/tasks": lambda q: engine.cmd_list(_args(status=q.get("status")), _today()),
    "/api/task": lambda q: _or_404(engine.cmd_show, _args(task=q.get("name") or "")),
    "/api/backups": lambda q: engine.cmd_backup_list(_args(dest=q.get("dest")), _today()),
    "/api/attachments": lambda q: _or_404(engine.cmd_attachments, _args(
        task=q.get("task") or "", step=q.get("step"), template=q.get("template"))),
    "/api/kb/notes": lambda q: engine.cmd_kb_note_list(_args(), _today()),
    "/api/kb/note": lambda q: engine.cmd_kb_note_show(_args(id=q.get("id")), _today()),
    "/api/settings": lambda q: _settings_load(),
    "/api/kb/exclusions": lambda q: engine.cmd_kb_exclusions(_args(), _today()),
}

POST = {
    "/api/parse-date": _parse_date,
    "/api/create": lambda p: engine.cmd_create(_args(json=json.dumps(p)), _today()),
    "/api/action": _action,
    "/api/backlog-bulk": _backlog_bulk,
    "/api/from-template": lambda p: engine.cmd_from_template(
        _args(name=p.get("name"), start=p.get("start"), title=p.get("title")), _today()),
    "/api/template-from-task": lambda p: engine.cmd_template_from_task(
        _args(task=p.get("task"), name=p.get("name")), _today()),
    "/api/template-create": lambda p: engine.cmd_save_template(
        _args(json=json.dumps(p)), _today()),
    "/api/template-delete": lambda p: engine.cmd_template_delete(
        _args(name=p.get("name")), _today()),
    "/api/template-preview": lambda p: engine.cmd_template_preview(
        _args(json=json.dumps({k: v for k, v in p.items() if k != "start"}),
              start=p.get("start")), _today()),
    "/api/recurrence-parse": lambda p: engine.cmd_recurrence_parse(
        _args(text=p.get("text")), _today()),
    "/api/recurrence-preview": lambda p: engine.cmd_recurrence_preview(
        _args(anchor=p.get("anchor"), rule=p.get("rule")), _today()),
    "/api/template-recurrence": lambda p: engine.cmd_set_recurrence(
        _args(name=p.get("name"), rule=p.get("rule"), clear=bool(p.get("clear"))), _today()),
    "/api/recur": lambda p: engine.cmd_recur(
        _args(name=p.get("name"), force=bool(p.get("force")), limit=p.get("limit")), _today()),
    "/api/task-update": _task_update,
    "/api/task-warnings": lambda p: {"warnings": engine.soft_warnings(p, _today())},
    "/api/steps-check": _steps_check,
    "/api/task-cancel": lambda p: _guarded(engine.cmd_cancel, _args(
        task=p.get("task"), reason=p.get("reason"))),
    "/api/task-close": lambda p: _guarded(engine.cmd_close, _args(task=p.get("task"))),
    "/api/task-delete": lambda p: _guarded(engine.cmd_delete, _args(task=p.get("task"))),
    "/api/task-reopen": lambda p: _guarded(engine.cmd_reopen, _args(
        task=p.get("task"), step=str(p.get("step") or ""))),
    "/api/kb/scan": lambda p: engine.cmd_kb_scan(
        _args(text=p.get("text"), source_type=p.get("source_type"),
              source_id=p.get("source_id")), _today()),
    "/api/kb/confirm": lambda p: engine.cmd_kb_confirm(
        _args(source_type=p.get("source_type"), source_id=p.get("source_id"),
              mentions=p.get("mentions")), _today()),
    "/api/kb/reject": lambda p: engine.cmd_kb_reject(
        _args(mention=p.get("mention"), mute=bool(p.get("mute"))), _today()),
    "/api/kb/forget": lambda p: engine.cmd_kb_forget(_args(key=p.get("key")), _today()),
    "/api/backup": lambda p: engine.cmd_backup(
        _args(dest=p.get("dest"), keep=p.get("keep"), force=bool(p.get("force"))), _today()),
    "/api/restore": lambda p: engine.cmd_backup_restore(_args(file=p.get("file")), _today()),
    "/api/export-json": lambda p: engine.cmd_export_json(_args(to=p.get("to")), _today()),
    "/api/settings": _settings_save,
    "/api/reasons-add": lambda p: _settings_op(cfg.add_reason, p.get("name")),
    "/api/reasons-rename": lambda p: _settings_op(
        cfg.rename_reason, p.get("old_name"), p.get("new_name")),
    "/api/reasons-archive": lambda p: _settings_op(cfg.archive_reason, p.get("name")),
    "/api/tags-add": lambda p: _settings_op(
        cfg.add_tag, p.get("name"), p.get("color"), bool(p.get("pinned"))),
    "/api/tags-rename": lambda p: _tag_rewrite(
        cfg.rename_tag, p.get("old_name"), p.get("new_name")),
    "/api/tags-toggle-pinned": lambda p: _settings_op(cfg.toggle_tag_pinned, p.get("name")),
    "/api/kb/note-create": lambda p: engine.cmd_kb_note_create(
        _args(json=json.dumps(p)), _today()),
    "/api/kb/note-update": lambda p: engine.cmd_kb_note_update(
        _args(id=p.get("id"), json=json.dumps(p)), _today()),
    "/api/kb/note-delete": lambda p: engine.cmd_kb_note_delete(_args(id=p.get("id")), _today()),
    "/api/tags-merge": lambda p: _tag_rewrite(cfg.merge_tags, p.get("source"), p.get("target")),
    "/api/attachments-add": _attachment_add,
    "/api/attachments-delete": lambda p: _guarded(
        engine.cmd_attachment_delete, _args(id=p.get("id"))),
}


def _as_response(result) -> Response:
    return result if isinstance(result, Response) else _json(result)


def _make_get(fn):
    async def endpoint(request: Request) -> Response:
        return _as_response(fn(request.query_params))
    return endpoint


def _make_post(fn):
    async def endpoint(request: Request) -> Response:
        try:
            payload = json.loads((await request.body()) or b"{}")
        except (ValueError, json.JSONDecodeError) as e:
            return _json({"ok": False,
                          "errors": [{"field": None, "error": f"битый запрос: {e}"}]}, 400)
        return _as_response(fn(payload))
    return endpoint


for _route, _fn in GET.items():
    router.add_api_route(_route, _make_get(_fn), methods=["GET"])
for _route, _fn in POST.items():
    router.add_api_route(_route, _make_post(_fn), methods=["POST"])

# `/вложение/{id}` переехал в `api/v1/attachments.py` (роутер `public`, Р17
# спецификации среза 2) и подключается в `api/app.py` — дубль здесь убран,
# чтобы легаси-маршрут не перекрывал новый обработчик.
