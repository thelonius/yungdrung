#!/usr/bin/env python3
"""Сверка старого `server.py` и FastAPI-приложения: один и тот же сценарий
запросов против двух одинаково засеянных демо-сторов, сравнение кодов и тел.

Выполняется один раз, перед тем как `server.py` станет запускатором (REFACTOR.md,
срез 1b): старые маршруты перенесены в `api/legacy.py` механически, а тестов у
старого сервера не было — это единственная проверка, что перенос ничего не
поменял. Повторить позже можно, достав старый сервер из истории:

    git show 5def9a9:server.py > /tmp/old_server.py
    python3 tools/http_parity.py --old "python3 /tmp/old_server.py"

Сценарий проходит все GET-маршруты и все POST-маршруты старого сервера, кроме
`/api/restore` (перезаписывает стор по абсолютному пути копии — на двух разных
сторах сравнивать нечего). Пути к стору в ответах нормализуются в `<VAULT>`.
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()


def seed(vault: Path):
    env = {**os.environ, "YUNGDRUNG_VAULT": str(vault)}
    subprocess.run([sys.executable, "demo.py"], cwd=ROOT, env=env, check=True,
                   capture_output=True)


def start(cmd: str, port: int, vault: Path):
    env = {**os.environ, "YUNGDRUNG_VAULT": str(vault), "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(cmd.replace("{port}", str(port)).split(), cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/reasons", timeout=1)
            return proc
        except (urllib.error.URLError, ConnectionError):
            if proc.poll() is not None:
                sys.exit(f"сервер упал: {proc.stderr.read().decode()}")
            time.sleep(0.1)
    sys.exit(f"сервер на {port} не поднялся")


def call(port, method, path, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    url = f"http://127.0.0.1:{port}" + urllib.parse.quote(path, safe="/?=&%+:.,")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()
    except (ConnectionError, urllib.error.URLError) as e:
        # Старый сервер на необработанном исключении обрывал соединение без
        # ответа — это тоже поведение, и оно должно попасть в отчёт строкой,
        # а не остановить сценарий.
        return 599, "", repr(e).encode()


def normalize(body: bytes, ctype: str, vault: Path):
    if "json" in ctype:
        try:
            obj = json.loads(body.decode("utf-8"))
        except ValueError:
            return body[:80]
        text = json.dumps(obj, ensure_ascii=False, sort_keys=True)
        text = text.replace(str(vault), "<VAULT>")
        # Момент «сейчас», имена и размеры копий несут время создания: между
        # двумя прогонами оно разное по определению.
        text = re.sub(r"\d{4}-\d{2}-\d{2}[T_ ]\d{2}[-:]\d{2}[-:]\d{2}(\.\d+)?", "<STAMP>", text)
        text = re.sub(r"\d{4}-\d{2}-\d{2}-\d{6}", "<STAMP>", text)
        text = re.sub(r'"bytes": \d+', '"bytes": <N>', text)
        return text
    if "html" in ctype or "css" in ctype or "javascript" in ctype:
        return ("text", len(body))
    return ("bytes", len(body))


def scenario(port, vault, seen):
    """Последовательность запросов. Часть тел зависит от предыдущих ответов
    (имена задач, id записей) — берётся из `seen` того же сервера, поэтому
    сценарий одинаков для обоих, если одинаковы данные."""
    def get(label, path):
        seen[label] = call(port, "GET", path)
        return seen[label]

    def post(label, path, payload):
        seen[label] = call(port, "POST", path, payload)
        return seen[label]

    def js(label):
        return json.loads(seen[label][2])

    for page in ("/", "/лента", "/новая", "/шаблоны", "/задача", "/настройки", "/архив",
                 "/задачи", "/база", "/база/запись", "/style.css", "/feed.js",
                 "/нет-такой", "/api/нет-такого", "/..%2f..%2fetc/passwd.css"):
        get(f"GET {page}", page)

    get("feed", "/api/feed")
    get("backlog", "/api/backlog")
    get("tasks", "/api/tasks")
    get("tasks done", "/api/tasks?status=done")
    задачи = js("tasks")["tasks"]
    первая = next(t["task"] for t in задачи if t.get("status") in ("overdue", "due"))
    get("task", f"/api/task?name={первая}")
    get("task 404", "/api/task?name=несуществующая")
    get("reasons", "/api/reasons")
    get("templates", "/api/templates")
    get("templates start", "/api/templates?start=2026-10-01")
    get("archive", "/api/archive")
    get("archive tag", "/api/archive?tag=демо&since=2026-01-01")
    get("search", "/api/search?q=смета")
    get("search empty", "/api/search?q=")
    get("backups", "/api/backups")
    get("attachments", f"/api/attachments?task={первая}")
    get("attachments 404", "/api/attachments?task=нет")
    get("kb notes", "/api/kb/notes")
    get("settings", "/api/settings")
    get("kb exclusions", "/api/kb/exclusions")

    post("parse-date ok", "/api/parse-date", {"text": "завтра в 9"})
    post("parse-date bad", "/api/parse-date", {"text": "мусор"})
    post("parse-date empty", "/api/parse-date", {"text": ""})
    причина = js("reasons")["reasons"][0]

    post("create ok", "/api/create", {
        "title": "Сверка HTTP", "tags": ["сверка"], "body": "Тело.",
        "steps": [{"title": "Первый", "control_date": "сегодня"},
                  {"title": "Группа", "mode": "par", "steps": [
                      {"title": "А", "control_date": "+2"}, {"title": "Б", "control_date": "+3"}]},
                  {"title": "Последний", "control_date": "+10"}]})
    post("create invalid", "/api/create", {"title": "", "steps": []})
    post("create dup", "/api/create", {"title": "Сверка HTTP", "steps": [{"title": "x"}]})
    post("task-warnings", "/api/task-warnings", {"title": "Сверка HTTP", "steps": [
        {"title": "Первый", "control_date": "вчера"}]})
    post("steps-check", "/api/steps-check", {"task": "Сверка HTTP", "start_date": "+5",
                                             "steps": [{"title": "x", "control_date": "+1"}]})

    post("action done", "/api/action", {"op": "done", "task": "Сверка HTTP", "step": 1})
    post("action done again", "/api/action", {"op": "done", "task": "Сверка HTTP", "step": 1})
    post("action notdone", "/api/action", {"op": "notdone", "task": "Сверка HTTP", "step": 3,
                                           "reason": причина})
    post("action notdone no reason", "/api/action", {"op": "notdone", "task": "Сверка HTTP",
                                                     "step": 3})
    post("action defer", "/api/action", {"op": "defer", "task": "Сверка HTTP", "step": 3,
                                         "reason": причина, "to": "+5 15:00"})
    post("action defer bad date", "/api/action", {"op": "defer", "task": "Сверка HTTP",
                                                  "step": 3, "reason": причина, "to": "мусор"})
    post("action fail", "/api/action", {"op": "fail", "task": "Сверка HTTP", "step": 4,
                                        "reason": причина})
    post("action skip group", "/api/action", {"op": "skip", "task": "Сверка HTTP", "step": 2})
    post("action undo", "/api/action", {"op": "undo", "task": "Сверка HTTP", "step": 4})
    post("action unknown", "/api/action", {"op": "взлететь", "task": "Сверка HTTP", "step": 1})
    post("action no step", "/api/action", {"op": "done", "task": "Сверка HTTP"})
    post("action unknown task", "/api/action", {"op": "done", "task": "нет такой", "step": 1})

    завал = js("backlog")["backlog"][:2]
    post("backlog-bulk defer", "/api/backlog-bulk", {
        "op": "defer", "reason": причина, "to": "+4",
        "items": [{"task": i["task"], "step": i["step"]} for i in завал]})
    post("backlog-bulk empty", "/api/backlog-bulk", {"op": "done", "items": []})

    post("task-update", "/api/task-update", {"task": "Сверка HTTP", "data": {
        "title": "Сверка HTTP", "tags": ["сверка", "ещё"], "body": "Новое тело."}})
    post("task-update no task", "/api/task-update", {})
    post("template-from-task", "/api/template-from-task", {"task": "Сверка HTTP",
                                                           "name": "Шаблон сверки"})
    post("template-create", "/api/template-create", {
        "name": "Квартальный отчёт", "tags": ["отчётность"],
        "steps": [{"title": "Собрать выписки", "offset_days": 0, "time_of_day": "10:00"},
                  {"title": "Отправить", "offset_days": 3}]})
    post("template-preview", "/api/template-preview", {
        "name": "x", "start": "2026-10-01",
        "steps": [{"title": "a", "offset_days": 0}, {"title": "b", "offset_days": 31}]})
    post("recurrence-parse", "/api/recurrence-parse", {"text": "каждый вторник"})
    post("recurrence-parse bad", "/api/recurrence-parse", {"text": "иногда"})
    правило = js("recurrence-parse").get("rule") or {"freq": "weekly", "byweekday": [1]}
    правило = {**правило, "anchor": "2026-09-01"}
    post("recurrence-preview", "/api/recurrence-preview", {"anchor": "2026-09-01",
                                                           "rule": правило})
    post("template-recurrence", "/api/template-recurrence", {"name": "Квартальный отчёт",
                                                             "rule": правило})
    post("recur", "/api/recur", {"name": "Квартальный отчёт"})
    post("from-template", "/api/from-template", {"name": "Квартальный отчёт",
                                                 "start": "2026-10-05", "title": "Отчёт Q3"})
    post("template-recurrence clear", "/api/template-recurrence", {"name": "Квартальный отчёт",
                                                                   "clear": True})
    post("template-delete", "/api/template-delete", {"name": "Шаблон сверки"})
    post("template-delete 404", "/api/template-delete", {"name": "нет такого"})

    post("attachments-add", "/api/attachments-add", {"task": "Сверка HTTP", "filename": "s.png",
                                                     "data": PNG, "caption": "схема"})
    post("attachments-add bad", "/api/attachments-add", {"task": "Сверка HTTP",
                                                         "filename": "s.png", "data": "%%%"})
    get("attachments after", "/api/attachments?task=Сверка HTTP")
    вложение = js("attachments after")["attachments"][0]["id"]
    get("attachment bytes", f"/вложение/{вложение}")
    get("attachment bytes 404", "/вложение/99999")
    get("attachment bytes bad", "/вложение/abc")
    post("attachments-delete", "/api/attachments-delete", {"id": вложение})

    post("task-cancel", "/api/task-cancel", {"task": "Отчёт Q3", "reason": причина})
    post("task-cancel again", "/api/task-cancel", {"task": "Отчёт Q3", "reason": причина})
    post("task-reopen", "/api/task-reopen", {"task": "Сверка HTTP", "step": 1})
    post("task-close", "/api/task-close", {"task": "Сверка HTTP"})
    post("task-delete", "/api/task-delete", {"task": "Отчёт Q3"})
    post("task-delete 404", "/api/task-delete", {"task": "нет такой"})

    заметки = js("kb notes").get("notes") or []
    имя_заметки = заметки[0]["title"] if заметки else "Василий Говнов"
    post("kb/note-create", "/api/kb/note-create", {"title": "Сверочная запись",
                                                   "aliases": ["сверка"], "body": "Текст."})
    post("kb/note-create dup", "/api/kb/note-create", {"title": "Сверочная запись"})
    нов = js("kb/note-create").get("note")
    get("kb/note", f"/api/kb/note?id={нов}")
    post("kb/note-update", "/api/kb/note-update", {"id": нов, "title": "Сверочная запись",
                                                   "aliases": ["сверка", "проверка"],
                                                   "body": "Другой текст."})
    post("kb/scan", "/api/kb/scan", {"text": f"Позвонить {имя_заметки} про сверку",
                                     "source_type": "task", "source_id": "Сверка HTTP"})
    гипотезы = js("kb/scan").get("hypotheses") or []
    if гипотезы:
        post("kb/confirm", "/api/kb/confirm", {"source_type": "task", "source_id": "Сверка HTTP",
                                               "mentions": [гипотезы[0]]})
        post("kb/reject", "/api/kb/reject", {"mention": гипотезы[0], "mute": True})
        get("kb exclusions after", "/api/kb/exclusions")
        исключения = js("kb exclusions after").get("exclusions") or []
        if исключения:
            post("kb/forget", "/api/kb/forget", {"key": {
                "kb_entry_id": исключения[0]["kb_entry_id"], "text": исключения[0]["text"]}})
    post("kb/note-delete", "/api/kb/note-delete", {"id": нов})

    настройки = js("settings")
    post("settings save", "/api/settings", настройки)
    post("settings save bad", "/api/settings", {"notifications": {"start": "25:00"}})
    post("reasons-add", "/api/reasons-add", {"name": "Сверочная причина"})
    post("reasons-add dup", "/api/reasons-add", {"name": "Сверочная причина"})
    post("reasons-rename", "/api/reasons-rename", {"old_name": "Сверочная причина",
                                                   "new_name": "Причина сверки"})
    post("reasons-archive", "/api/reasons-archive", {"name": "Причина сверки"})
    post("tags-add", "/api/tags-add", {"name": "сверочный", "color": "red", "pinned": True})
    post("tags-toggle-pinned", "/api/tags-toggle-pinned", {"name": "сверочный"})
    post("tags-rename", "/api/tags-rename", {"old_name": "сверочный", "new_name": "сверенный"})
    post("tags-merge", "/api/tags-merge", {"source": "сверенный", "target": "сверка"})
    post("tags-merge 404", "/api/tags-merge", {"source": "нет", "target": "сверка"})

    копии = vault / "копии"
    post("backup", "/api/backup", {"dest": str(копии), "force": True})
    get("backups after", f"/api/backups?dest={копии}")
    post("export-json", "/api/export-json", {"to": str(vault / "выгрузка.json")})
    post("bad json", "/api/create", None)
    seen["bad json"] = call(port, "POST", "/api/create", None)
    # тело не JSON
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/create", data="{не json".encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            seen["broken body"] = (r.status, r.headers.get("Content-Type", ""), r.read())
    except urllib.error.HTTPError as e:
        seen["broken body"] = (e.code, e.headers.get("Content-Type", ""), e.read())

    get("feed after", "/api/feed")
    get("tasks after", "/api/tasks")
    get("archive after", "/api/archive")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", default="python3 server.py --port {port} --no-open")
    ap.add_argument("--new", default="python3 -m uvicorn api.app:app --host 127.0.0.1 "
                                     "--port {port} --log-level error")
    ap.add_argument("--ports", default="8871,8872")
    a = ap.parse_args()
    p_old, p_new = (int(x) for x in a.ports.split(","))

    tmp = Path(tempfile.mkdtemp(prefix="yungdrung-parity-"))
    v_old, v_new = tmp / "old", tmp / "new"
    v_old.mkdir()
    v_new.mkdir()
    seed(v_old)
    seed(v_new)

    procs = [start(a.old, p_old, v_old), start(a.new, p_new, v_new)]
    try:
        old, new = {}, {}
        scenario(p_old, v_old, old)
        scenario(p_new, v_new, new)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=10)

    расхождения = 0
    for label in old:
        so, co, bo = old[label]
        sn, cn, bn = new.get(label, (None, "", b""))
        no, nn = normalize(bo, co, v_old), normalize(bn, cn, v_new)
        if so != sn or no != nn:
            расхождения += 1
            print(f"✗ {label}: код {so} → {sn}")
            if no != nn:
                so_, sn_ = str(no), str(nn)
                i = next((k for k, (x, y) in enumerate(zip(so_, sn_)) if x != y),
                         min(len(so_), len(sn_)))
                a, b = max(0, i - 120), i + 180
                print(f"    старый: …{so_[a:b]}…")
                print(f"    новый:  …{sn_[a:b]}…")
    print(f"\nзапросов {len(old)}, расхождений {расхождения}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if расхождения else 0


if __name__ == "__main__":
    sys.exit(main())
