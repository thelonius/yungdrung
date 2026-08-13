#!/usr/bin/env python3
"""Движок шагов Yungdrung.

Единственный, кто пишет шаги и даты в вольт. Без LLM: всё, что здесь считается —
выборки по датам и статусам. На выходе JSON, чтобы поверх можно было повесить
любой интерфейс.

  python3 engine.py next                        что требует внимания сегодня
  python3 engine.py done <задача> <шаг>         шаг сделан
  python3 engine.py notdone <задача> <шаг> --reason "не дозвонился"
  python3 engine.py defer <задача> <шаг> --to 2026-08-20 --reason "..."
  python3 engine.py list                        все задачи со статусами
  python3 engine.py show <задача>               одна задача целиком
  python3 engine.py export --to выгрузка.xlsx   весь вольт в Excel

Задача указывается частью имени файла: "грант" найдёт «Заявка на грант ФПГ».
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# Консоль Windows по умолчанию не в UTF-8 (обычно cp866), а мы печатаем русский
# текст и типографику: «кавычки», тире, многоточие. Без этого «нет задачи по
# «грант»» падает UnicodeEncodeError вместо внятного сообщения — причём на
# машине заказчика, который такое не починит. Делаем до первого вывода.
for поток in (sys.stdout, sys.stderr):
    try:
        поток.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass  # поток подменён или не текстовый — печатать всё равно нечем испортить

try:
    import yaml
except ImportError:
    sys.exit("нужен pyyaml: pip install pyyaml")

SCHEMA = 1
VAULT = Path(os.environ.get("YUNGDRUNG_VAULT", Path(__file__).resolve().parent))
TASKS_DIR = VAULT / "Задачи"

OPEN = "pending"
DONE = "done"
SKIPPED = "skipped"

# В вольт статус пишется по-русски: эти файлы читает заказчик, а не только движок.
# В JSON наружу уходят английские ключи — там интерфейс для бота.
STATUS_RU = {
    "overdue": "просрочена",
    "due": "сегодня",
    "waiting": "ждёт",
    "no_date": "без даты",
    "done": "закрыта",
    "empty": "нет шагов",
}

# Статусы и события шага в файле лежат по-английски — их читает движок. В Excel
# уходит перевод: тот файл открывает заказчик, и «not_done» ему ни о чём не говорит.
STEP_STATUS_RU = {
    OPEN: "ждёт",
    DONE: "сделан",
    SKIPPED: "снят",
}

EVENT_RU = {
    "done": "сделан",
    "not_done": "не сделан",
    "defer": "перенесён",
    "skipped": "снят",
}


# --- чтение и запись -------------------------------------------------------

def parse_file(path):
    """Frontmatter + тело. Тело сохраняем как есть: его пишет заказчик, не мы."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise ValueError(f"{path.name}: нет frontmatter")
    _, fm, body = raw.split("---", 2)
    return yaml.safe_load(fm) or {}, body.lstrip("\n")


class PlainDumper(yaml.SafeDumper):
    """Без якорей и ссылок: одна и та же дата в нескольких полях — обычное дело,
    а `&id001`/`*id001` Obsidian не разбирает и файл для него ломается."""

    def ignore_aliases(self, data):
        return True


def write_file(path, meta, body):
    """Атомарно: Obsidian держит файлы открытыми и кэширует, дописывать на месте нельзя.

    На Windows os.replace может ненадолго упасть с PermissionError, если файл в
    этот момент держит антивирус, OneDrive-индексатор или сам Obsidian — на маке
    так почти не бывает. Несколько коротких повторов дешевле, чем терять запись.
    """
    fm = yaml.dump(meta, Dumper=PlainDumper, allow_unicode=True, sort_keys=False,
                   default_flow_style=False)
    content = f"---\n{fm}---\n\n{body}"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2 * (attempt + 1))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_tasks():
    if not TASKS_DIR.is_dir():
        sys.exit(f"нет папки задач: {TASKS_DIR}")
    out = []
    for path in sorted(TASKS_DIR.glob("*.md")):
        try:
            meta, body = parse_file(path)
        except Exception as e:
            print(f"[пропущен {path.name}: {e}]", file=sys.stderr)
            continue
        if meta.get("type") != "task":
            continue
        out.append({"path": path, "meta": meta, "body": body})
    return out


def find_task(fragment):
    frag = fragment.lower()
    hits = [t for t in load_tasks() if frag in t["path"].stem.lower()]
    if not hits:
        sys.exit(f"нет задачи по «{fragment}»")
    if len(hits) > 1:
        sys.exit("подходит несколько: " + ", ".join(t["path"].stem for t in hits))
    return hits[0]


# --- вычисления ------------------------------------------------------------

def as_date(value):
    """Дата контроля может быть с временем или без: без времени — весь день."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).strip()).date()


def steps_of(task):
    return task["meta"].get("steps") or []


def is_closed(step):
    """Шаг закрыт, только если статус — один из двух известных нам.

    Всё незнакомое считаем открытым, а не закрытым. Вольт правится руками, и
    заказчик вполне может напечатать в Obsidian `status: сделан` вместо `done`.
    Раньше это роняло `list`/`next`/`refresh`/`export` целиком с traceback:
    «закрыт» и «открыт» проверялись двумя разными правилами, и на незнакомом
    статусе они расходились — задача не считалась закрытой, но и текущего шага
    в ней не находилось.
    """
    return step.get("status", OPEN) in (DONE, SKIPPED)


def current_step(task):
    """Первый незакрытый шаг. Шаги идут последовательно, параллельных нет."""
    for step in steps_of(task):
        if not is_closed(step):
            return step
    return None


def task_status(task, today):
    steps = steps_of(task)
    if not steps:
        return "empty"
    if all(is_closed(s) for s in steps):
        return "done"
    step = current_step(task)
    due = as_date(step.get("control_date"))
    if due is None:
        return "no_date"
    if due < today:
        return "overdue"
    if due == today:
        return "due"
    return "waiting"


def stall_count(step):
    """Сколько раз шаг не сделали. Отличает «ещё не дошли руки» от «буксует»."""
    return sum(1 for e in step.get("log") or [] if e.get("event") == "not_done")


def step_view(task, step, today):
    due = as_date(step.get("control_date"))
    return {
        "task": task["path"].stem,
        "step": step.get("id"),
        "title": step.get("title"),
        "control_date": str(step.get("control_date")) if step.get("control_date") else None,
        "overdue_days": (today - due).days if due and due < today else 0,
        "stalled": stall_count(step),
        "last_reason": next(
            (e.get("reason") for e in reversed(step.get("log") or []) if e.get("reason")), None
        ),
    }


# --- запись ----------------------------------------------------------------

def log_event(step, event, today, **fields):
    step.setdefault("log", [])
    entry = {"date": today, "event": event}
    entry.update({k: v for k, v in fields.items() if v is not None})
    step["log"].append(entry)


def get_step(task, step_id):
    for step in steps_of(task):
        if str(step.get("id")) == str(step_id):
            return step
    sys.exit(f"нет шага {step_id} в «{task['path'].stem}»")


def save(task, today, force=True):
    """Статус и сводка пересчитываются при каждой записи, руками их никто не ставит.

    Сводка дублирует то, что и так лежит в шагах, но Bases читает только свойства
    верхнего уровня и внутрь массива шагов не заглядывает. Без этих полей таблица
    в Obsidian показывает одни имена файлов. Источник правды остаётся в шагах:
    всё, что здесь, вычисляется заново при каждой записи.
    """
    meta = task["meta"]
    steps = steps_of(task)
    step = current_step(task)
    closed = sum(1 for s in steps if s.get("status") in (DONE, SKIPPED))

    summary = {
        "schema": SCHEMA,
        "status": STATUS_RU[task_status(task, today)],
        "current_step": step.get("title") if step else None,
        "control_date": as_date(step.get("control_date")) if step else None,
        "stalled": stall_count(step) if step else 0,
        "progress": f"{closed}/{len(steps)}" if steps else None,
    }
    changed = any(meta.get(k) != v for k, v in summary.items())
    meta.update(summary)

    # Порядок полей задаём явно: в редакторе свойств Obsidian сводка должна быть
    # сверху, а длинный массив шагов — в конце, иначе статуса не видно за простынёй.
    head = ["schema", "type", "title", "created", "status", "current_step",
            "control_date", "progress", "stalled", "tags"]
    ordered = {k: meta[k] for k in head if k in meta}
    ordered.update({k: v for k, v in meta.items() if k not in head and k != "steps"})
    if "steps" in meta:
        ordered["steps"] = meta["steps"]
    task["meta"] = ordered

    if changed or force:
        write_file(task["path"], ordered, task["body"])
    return changed


# --- команды ---------------------------------------------------------------

def cmd_next(args, today):
    due, stalled = [], []
    for task in load_tasks():
        status = task_status(task, today)
        if status not in ("overdue", "due", "no_date"):
            continue
        step = current_step(task)
        view = step_view(task, step, today)
        view["status"] = status
        due.append(view)
        if view["stalled"] >= 3:
            stalled.append(view)
    due.sort(key=lambda v: (-v["overdue_days"], v["task"]))
    return {"today": today.isoformat(), "due": due, "stalled": stalled}


def cmd_refresh(args, today):
    """Пересчитать сводку во всех задачах.

    Статус в файле устаревает сам по себе: задача становится просроченной оттого,
    что прошёл день, а не оттого, что кто-то её трогал. Гонять перед утренней
    сборкой. Файлы, где ничего не изменилось, не переписываются — иначе каждое утро
    получаем холостой коммит и перезагрузку вольта в Obsidian.
    """
    touched = []
    for task in load_tasks():
        changed = save(task, today, force=args.force)
        if changed or args.force:
            touched.append({"task": task["path"].stem,
                            "status": task["meta"]["status"],
                            "changed": changed})
    return {"today": today.isoformat(), "written": touched, "count": len(touched),
            "forced": bool(args.force)}


def cmd_list(args, today):
    out = []
    for task in load_tasks():
        steps = steps_of(task)
        step = current_step(task)
        out.append({
            "task": task["path"].stem,
            "status": task_status(task, today),
            "category": task["meta"].get("tags") or [],
            "steps_done": sum(1 for s in steps if s.get("status") in (DONE, SKIPPED)),
            "steps_total": len(steps),
            "current": step.get("title") if step else None,
        })
    return {"today": today.isoformat(), "tasks": out}


def cmd_show(args, today):
    task = find_task(args.task)
    return {
        "task": task["path"].stem,
        "status": task_status(task, today),
        "meta": {k: v for k, v in task["meta"].items() if k != "steps"},
        "steps": [
            {**{k: (str(v) if isinstance(v, (date, datetime)) else v)
                for k, v in s.items() if k != "log"},
             "log": s.get("log") or []}
            for s in steps_of(task)
        ],
    }


def cmd_done(args, today):
    task = find_task(args.task)
    step = get_step(task, args.step)
    if step.get("status") != OPEN:
        sys.exit(f"шаг {args.step} уже {step.get('status')}")
    step["status"] = DONE
    step["completed_date"] = today
    log_event(step, "done", today, reason=args.reason)

    # следующий шаг без даты никогда не всплывёт в сборке — ставим сегодня,
    # заказчик увидит его и при необходимости перенесёт
    nxt = current_step(task)
    assigned = None
    if nxt and not nxt.get("control_date"):
        nxt["control_date"] = today
        assigned = nxt.get("id")
    save(task, today)
    return {"ok": True, "task": task["path"].stem, "step": args.step, "status": DONE,
            "next_step": nxt.get("title") if nxt else None,
            "date_assigned_to_step": assigned,
            "task_status": task_status(task, today)}


def cmd_notdone(args, today):
    """Шаг остаётся открытым: причина обычно внешняя, решать всё равно надо."""
    task = find_task(args.task)
    step = get_step(task, args.step)
    if step.get("status") != OPEN:
        sys.exit(f"шаг {args.step} уже {step.get('status')}")
    new_date = date.fromisoformat(args.to) if args.to else today + timedelta(days=1)
    log_event(step, "not_done", today, reason=args.reason,
              was=as_date(step.get("control_date")), to=new_date)
    step["control_date"] = new_date
    save(task, today)
    count = stall_count(step)
    return {"ok": True, "task": task["path"].stem, "step": args.step, "status": OPEN,
            "next_check": new_date.isoformat(), "stalled": count,
            "hint": "шаг буксует, нужен другой ход" if count >= 3 else None}


def cmd_defer(args, today):
    task = find_task(args.task)
    step = get_step(task, args.step)
    if step.get("status") != OPEN:
        sys.exit(f"шаг {args.step} уже {step.get('status')}")
    to = date.fromisoformat(args.to)
    log_event(step, "defer", today, reason=args.reason,
              was=as_date(step.get("control_date")), to=to)
    step["control_date"] = to
    save(task, today)
    return {"ok": True, "task": task["path"].stem, "step": args.step,
            "next_check": to.isoformat()}


def cmd_skip(args, today):
    """Шаг снят: задача пошла другим путём, а не через этот шаг."""
    task = find_task(args.task)
    step = get_step(task, args.step)
    # Как и done/notdone/defer: закрытый шаг повторно не трогаем. Иначе снятие
    # уже сделанного шага оставляло бы completed_date и событие «сделан» в логе
    # рядом со статусом «снят» — запись, противоречащая сама себе.
    if is_closed(step):
        sys.exit(f"шаг {args.step} уже {step.get('status')}")
    step["status"] = SKIPPED
    log_event(step, "skipped", today, reason=args.reason)
    nxt = current_step(task)
    if nxt and not nxt.get("control_date"):
        nxt["control_date"] = today
    save(task, today)
    return {"ok": True, "task": task["path"].stem, "step": args.step, "status": SKIPPED,
            "task_status": task_status(task, today)}


# --- выгрузка в Excel ------------------------------------------------------

# Предел Excel на длину текста в ячейке. Тело заметки пишет заказчик, и упереться
# в него теоретически можно — лучше обрезать, чем получить нечитаемый файл.
MAX_CELL = 32767

# Управляющие символы xlsx не принимает: файл открывается с руганью на повреждение.
# Тело заметки приходит из внешнего редактора, так что чистим на всякий случай.
CONTROL_CHARS = re.compile(r"[\000-\010\013\014\016-\037]")

# Заголовок и ширина колонки. Ширину задаём руками, а не по содержимому: имена
# задач и причины переносов длинные, и по факту всё равно упираешься в потолок,
# а файл должен открываться готовым к чтению, без растаскивания колонок мышью.
TASK_COLUMNS = [
    ("Задача", 34), ("Заголовок", 30), ("Создана", 12), ("Статус", 14),
    ("Текущий шаг", 34), ("Контроль", 12), ("Прогресс", 10), ("Буксует", 9),
    ("Категории", 22), ("Заметка", 60),
]
STEP_COLUMNS = [
    ("Задача", 34), ("Шаг", 6), ("Название", 40), ("Статус", 10),
    ("Контроль", 12), ("Выполнен", 12), ("Не сделан, раз", 15),
    ("Последняя причина", 40),
]
EVENT_COLUMNS = [
    ("Задача", 34), ("Шаг", 6), ("Название", 34), ("Дата", 12),
    ("Событие", 14), ("Причина", 40), ("Было", 12), ("Стало", 12),
]


def put(ws, row, col, value):
    """Одна ячейка со всеми оговорками про Excel.

    Даты кладём объектами `date`: строкой Excel их не понимает, теряется сортировка
    и фильтр по периоду — та же причина, по которой даты пишутся датами и в YAML.

    Строку, начинающуюся с «=», openpyxl считает формулой. В теле заметки такая
    строка вполне возможна, и Excel потом ругается на весь файл — тип задаём явно.
    """
    cell = ws.cell(row=row, column=col)
    if isinstance(value, datetime):
        value = value.date()
    if value == "":
        value = None  # задача без категорий и без тела — просто пустая ячейка
    if isinstance(value, date):
        cell.value = value
        cell.number_format = "YYYY-MM-DD"
    elif isinstance(value, str):
        value = CONTROL_CHARS.sub("", value)
        if len(value) > MAX_CELL:
            value = value[:MAX_CELL - 1] + "…"
        cell.value = value
        cell.data_type = "s"
    else:
        cell.value = value
    return cell


def write_sheet(wb, title, columns, rows):
    """Лист целиком: шапка, ширины, данные, фильтр."""
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    ws = wb.create_sheet(title)
    for i, (name, width) in enumerate(columns, start=1):
        ws.cell(row=1, column=i, value=name).font = Font(bold=True)
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"  # шапка не уезжает при прокрутке длинной истории
    for r, values in enumerate(rows, start=2):
        for c, value in enumerate(values, start=1):
            put(ws, r, c, value)
    # Фильтр по шапке: сортировка по дате и отбор по задаче — первое, что человек
    # захочет сделать в тысяче строк истории.
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{ws.max_row}"
    return ws


def cmd_export(args, today):
    """Весь вольт в один .xlsx.

    Заказчику это не аналитика, а страховка «чтобы не проебалось»: файл уезжает ему
    в Telegram и лежит отдельно от вольта. Отсюда третий лист — плоский разворот
    `log` шагов. Сводку и статусы можно пересчитать из шагов заново, а переносы,
    причины и старые даты больше взять неоткуда: пропадут вместе с папкой.

    Только чтение: вольт после экспорта побайтово тот же.
    """
    # Импорт по требованию: openpyxl нужен одной команде из восьми, а грузится он
    # впятеро дольше всего остального движка. Отметку шага это тормозить не должно.
    try:
        from openpyxl import Workbook
    except ImportError:
        sys.exit("нужен openpyxl: pip install openpyxl")

    out = Path(args.to) if args.to else VAULT / f"Выгрузка {today.isoformat()}.xlsx"
    tasks, steps, events = [], [], []

    for task in load_tasks():
        name = task["path"].stem
        meta = task["meta"]
        # Статус и прогресс считаем заново, а не берём из файла: сводка устаревает
        # сама по себе, от того что прошёл день. В выгрузке должно стоять сегодня.
        status = task_status(task, today)
        step = current_step(task)
        all_steps = steps_of(task)
        closed = sum(1 for s in all_steps if s.get("status") in (DONE, SKIPPED))
        tags = meta.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tasks.append([
            name, meta.get("title"), as_date(meta.get("created")), STATUS_RU[status],
            step.get("title") if step else None,
            as_date(step.get("control_date")) if step else None,
            f"{closed}/{len(all_steps)}" if all_steps else None,
            stall_count(step) if step else 0,
            ", ".join(str(t) for t in tags), task["body"].strip(),
        ])

        for s in all_steps:
            step_title = s.get("title")
            steps.append([
                name, s.get("id"), step_title,
                STEP_STATUS_RU.get(s.get("status", OPEN), s.get("status")),
                as_date(s.get("control_date")), as_date(s.get("completed_date")),
                stall_count(s),
                next((e.get("reason") for e in reversed(s.get("log") or [])
                      if e.get("reason")), None),
            ])
            for e in s.get("log") or []:
                events.append([
                    name, s.get("id"), step_title, as_date(e.get("date")),
                    EVENT_RU.get(e.get("event"), e.get("event")), e.get("reason"),
                    as_date(e.get("was")), as_date(e.get("to")),
                ])

    wb = Workbook()
    wb.remove(wb.active)  # лист по умолчанию называется Sheet и нам не нужен
    for name, columns, rows in (("Задачи", TASK_COLUMNS, tasks),
                                ("Шаги", STEP_COLUMNS, steps),
                                ("История", EVENT_COLUMNS, events)):
        write_sheet(wb, name, columns, rows)

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return {"today": today.isoformat(), "file": str(out), "tasks": len(tasks),
            "steps": len(steps), "events": len(events)}


def main():
    p = argparse.ArgumentParser(description="Движок шагов Yungdrung")
    p.add_argument("--today", help="подменить сегодняшнюю дату (для проверок)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("next", help="что требует внимания").set_defaults(func=cmd_next)
    sub.add_parser("list", help="все задачи").set_defaults(func=cmd_list)
    r = sub.add_parser("refresh", help="пересчитать сводку во всех задачах (перед сборкой)")
    r.add_argument("--force", action="store_true",
                   help="переписать все файлы, даже если сводка не изменилась — "
                        "нужно после смены схемы, чтобы привести вольт к новому виду")
    r.set_defaults(func=cmd_refresh)

    s = sub.add_parser("show", help="одна задача целиком")
    s.add_argument("task")
    s.set_defaults(func=cmd_show)

    d = sub.add_parser("done", help="шаг сделан")
    d.add_argument("task"); d.add_argument("step"); d.add_argument("--reason")
    d.set_defaults(func=cmd_done)

    n = sub.add_parser("notdone", help="шаг не сделан, остаётся открытым")
    n.add_argument("task"); n.add_argument("step")
    n.add_argument("--reason", help="почему: не дозвонился, ушёл в отпуск, было некогда")
    n.add_argument("--to", help="когда спросить снова; по умолчанию завтра")
    n.set_defaults(func=cmd_notdone)

    f = sub.add_parser("defer", help="перенести шаг на дату")
    f.add_argument("task"); f.add_argument("step")
    f.add_argument("--to", required=True); f.add_argument("--reason")
    f.set_defaults(func=cmd_defer)

    k = sub.add_parser("skip", help="снять шаг")
    k.add_argument("task"); k.add_argument("step"); k.add_argument("--reason")
    k.set_defaults(func=cmd_skip)

    x = sub.add_parser("export", help="выгрузить весь вольт в Excel")
    x.add_argument("--to", help="куда писать; по умолчанию — «Выгрузка <дата>.xlsx» "
                                "в корне вольта")
    x.set_defaults(func=cmd_export)

    args = p.parse_args()
    today = date.fromisoformat(args.today) if args.today else date.today()
    print(json.dumps(args.func(args, today), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
