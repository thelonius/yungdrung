#!/usr/bin/env python3
"""Движок шагов Yungdrung.

Единственный, кто пишет шаги и даты в вольт. Без LLM: всё, что здесь считается —
выборки по датам и статусам. На выходе JSON, чтобы поверх можно было повесить
любой интерфейс.

Вывод всегда в UTF-8, независимо от кодировки консоли. Кто вызывает движок из
кода и разбирает JSON — обязан читать его как UTF-8 явно: на Windows кодировка
локали другая, и русский текст молча превратится в «Ð“Ñ€Ð°Ð½Ñ‚».
Для subprocess это `encoding="utf-8"`.

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
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

# Консоль Windows по умолчанию не в UTF-8 (обычно cp866), а мы печатаем русский
# текст и типографику: «кавычки», тире, многоточие. Без этого «нет задачи по
# «грант»» падает UnicodeEncodeError вместо внятного сообщения — причём на
# машине заказчика, который такое не починит. Делаем до первого вывода.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass  # поток подменён или не текстовый — печатать всё равно нечем испортить

try:
    import yaml
except ImportError:
    sys.exit("нужен pyyaml: pip install pyyaml")

import worktime

SCHEMA = 1
VAULT = Path(os.environ.get("YUNGDRUNG_VAULT", Path(__file__).resolve().parent))
TASKS_DIR = VAULT / "Задачи"

OPEN = "pending"
DONE = "done"
SKIPPED = "skipped"
FAILED = "failed"

# Справочник причин, раздел 5.4 ТЗ. Стартовый набор, дальше редактируется.
# Причина обязательна при «не сделано», переносе и провале: без неё счётчик
# переносов показывает, что шаг буксует, но не показывает, обо что.
REASONS = [
    "жду ответа от другого человека",
    "не было денег",
    "не было времени",
    "передумал, надо переформулировать",
    "внешние обстоятельства",
    "не хватило информации",
    "моя лень",
]

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
    FAILED: "провален",
}

EVENT_RU = {
    "done": "сделан",
    "failed": "провален",
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


# Файлы, которые не удалось разобрать при последнем чтении вольта. Собираются
# здесь, чтобы попасть в JSON, а не только в stderr: заказчик правит шаги руками,
# и опечатка в YAML не должна означать, что задача молча исчезла из трекера и из
# утренней сборки. Молчаливая потеря опаснее падения — падение хотя бы заметно.
BROKEN = []


def load_tasks():
    if not TASKS_DIR.is_dir():
        sys.exit(f"нет папки задач: {TASKS_DIR}")
    BROKEN.clear()
    out = []
    for path in sorted(TASKS_DIR.glob("*.md")):
        try:
            meta, body = parse_file(path)
        except Exception as e:
            reason = " ".join(str(e).split())[:200]
            BROKEN.append({"file": path.name, "error": reason})
            print(f"[не разобран {path.name}: {reason}]", file=sys.stderr)
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
    return step.get("status", OPEN) in (DONE, SKIPPED, FAILED)


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


STEPS_START = "<!-- шаги: пишет движок, править руками не нужно -->"
STEPS_END = "<!-- /шаги -->"

# Маркеры статуса шага в теле заметки. Галочки-чекбоксы намеренно не используем:
# в Obsidian они кликабельные, заказчик отметил бы шаг мышкой, движок затёр бы
# это при следующей записи — и получилось бы два писателя одного поля.
MARK = {DONE: "✓", SKIPPED: "×", FAILED: "✗", OPEN: "•"}


def render_steps(task, today):
    """Шаги человеческим списком — в тело заметки.

    Зачем: в панели свойств Obsidian массив объектов не рисуется, там видна
    обрезанная JSON-строка. Заказчик кликает по задаче из таблицы Bases и
    упирается в нечитаемое. Здесь он видит список.

    Побочно чинится вторая дыра: `[[ссылка]]` внутри названия шага живёт во
    frontmatter, а его Obsidian ссылками не считает. В теле — считает, поэтому
    ссылки на заметки базы знаний из названий шагов начинают работать.
    """
    lines = [STEPS_START, "**Шаги**", ""]
    for step in steps_of(task):
        status = step.get("status", OPEN)
        tail = []
        due = as_date(step.get("control_date"))
        if status == DONE:
            completed = as_date(step.get("completed_date"))
            tail.append(f"сделан {completed:%d.%m.%Y}" if completed else "сделан")
        elif status == SKIPPED:
            tail.append("снят")
        elif status == FAILED:
            tail.append("не будет сделан")
        elif due:
            tail.append(f"контроль {due:%d.%m.%Y}")
            if due < today:
                tail.append(f"просрочен на {(today - due).days} дн.")
        else:
            tail.append("дата не назначена")

        stalled = stall_count(step)
        if stalled >= 3 and status == OPEN:
            reason = next((e.get("reason") for e in reversed(step.get("log") or [])
                            if e.get("reason")), None)
            tail.append(f"буксует, отметок «не сделан»: {stalled}"
                         + (f" ({reason})" if reason else ""))

        lines.append(f"{MARK.get(status, '•')} **{step.get('id')}.** "
                      f"{step.get('title', '')} — {' · '.join(tail)}")
    lines += ["", STEPS_END]
    return "\n".join(lines)


def put_steps_into_body(body, block):
    """Блок шагов переписываем, всё остальное в теле не трогаем.

    Тело принадлежит заказчику: там его заметки по задаче. Движок владеет только
    участком между маркерами. Если маркеров нет — вставляем блок сверху, текст
    заказчика уезжает под него.
    """
    start = body.find(STEPS_START)
    end = body.find(STEPS_END)
    if start != -1 and end != -1 and end > start:
        tail = body[end + len(STEPS_END):]
        return body[:start] + block + tail
    return block + "\n\n" + body.lstrip("\n") if body.strip() else block + "\n"


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
    closed = sum(1 for s in steps if is_closed(s))

    summary = {
        "schema": SCHEMA,
        "status": STATUS_RU[task_status(task, today)],
        "current_step": step.get("title") if step else None,
        "control_date": as_date(step.get("control_date")) if step else None,
        "stalled": stall_count(step) if step else 0,
        "progress": f"{closed}/{len(steps)}" if steps else None,
    }
    body = put_steps_into_body(task["body"], render_steps(task, today))
    changed = (any(meta.get(k) != v for k, v in summary.items())
               or body != task["body"])
    meta.update(summary)
    task["body"] = body

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
        write_file(task["path"], ordered, body)
    return changed


# --- команды ---------------------------------------------------------------

def feed_item(task, step, now, work):
    """Строка ленты. Всё вычислено здесь: морда только показывает.

    Раздел 6.1 ТЗ перечисляет, что видно в строке: название шага, название задачи,
    время контроля, теги, счётчик переносов, если больше нуля.
    """
    control = step.get("control_date")
    показ = worktime.show_at(control, work) if control else None
    return {
        "task": task["path"].stem,
        "step": step.get("id"),
        "title": step.get("title"),
        "note": step.get("note"),
        "control_at": str(control) if control else None,
        "show_at": показ.isoformat() if показ else None,
        "state": worktime.due_state(control, now, work),
        "postponed": stall_count(step),
        "stalled": stall_count(step) >= 3,
        "tags": task["meta"].get("tags") or [],
        "last_reason": next(
            (e.get("reason") for e in reversed(step.get("log") or []) if e.get("reason")),
            None),
        "actions": ["done", "notdone", "defer", "skip"],
    }


def collect_open(now, work):
    """Открытые шаги всех задач, разложенные по состоянию.

    Одним проходом, потому что лента и завал — это один и тот же набор, просто
    разрезанный по-разному, и считать его дважды значит однажды разойтись.
    """
    лента, завал, ждут = [], [], []
    for task in load_tasks():
        step = current_step(task)
        if step is None:
            continue
        item = feed_item(task, step, now, work)
        if item["state"] == "overdue":
            завал.append(item)
        elif worktime.in_horizon(step.get("control_date"), now, work):
            лента.append(item)
        else:
            ждут.append(item)
    ключ = lambda i: (i["show_at"] or "9999", i["task"])
    return sorted(лента, key=ключ), sorted(завал, key=ключ), sorted(ждут, key=ключ)


def cmd_feed(args, today):
    """Лента «Что сегодня» — раздел 6.1 ТЗ.

    Просроченное в строки не попадает: по 6.1 оно живёт отдельной плашкой, потому
    что пятнадцать красных строк парализуют экран. Счётчик отдаёт отдельно.
    """
    now = _now(args, today)
    work = _work(args)
    лента, завал, ждут = collect_open(now, work)
    ближайший = ждут[0] if ждут else None
    return {
        "now": now.isoformat(),
        "feed": лента,
        "overdue_count": len(завал),
        "counts": {"overdue": len(завал), "today": len(лента), "waiting": len(ждут)},
        "next_ahead": ближайший,
        "stalled_count": sum(1 for i in лента + завал if i["stalled"]),
        "broken": list(BROKEN),
    }


def cmd_backlog(args, today):
    """Разбор завала — раздел 6.9 ТЗ. Всё просроченное, худшее сверху."""
    now = _now(args, today)
    work = _work(args)
    _, завал, _ = collect_open(now, work)
    завал.sort(key=lambda i: (not i["stalled"], i["show_at"] or "9999"))
    return {"now": now.isoformat(), "backlog": завал, "count": len(завал),
            "broken": list(BROKEN)}


def _now(args, today):
    """Момент, от которого считаем. `--today` задаёт дату, время берём текущее —
    так тесты и утренние прогоны воспроизводимы, а живой запуск точен."""
    заданный = getattr(args, "now", None) if args else None
    if заданный:
        return worktime.as_datetime(заданный)
    сейчас = datetime.now()
    return сейчас if today == сейчас.date() else datetime.combine(today, сейчас.time())


def _work(args):
    return worktime.settings(
        start=getattr(args, "work_start", None) if args else None,
        end=getattr(args, "work_end", None) if args else None,
        weekends=getattr(args, "weekends", None) if args else None,
    )


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
    return {"today": today.isoformat(), "due": due, "stalled": stalled,
            "broken": list(BROKEN)}


ДНИ_НЕДЕЛИ = {
    "пн": 0, "понедельник": 0, "вт": 1, "вторник": 1, "ср": 2, "среда": 2,
    "чт": 3, "четверг": 3, "пт": 4, "пятница": 4, "сб": 5, "суббота": 5,
    "вс": 6, "воскресенье": 6,
}

ОТНОСИТЕЛЬНЫЕ = {"сегодня": 0, "завтра": 1, "послезавтра": 2}


def _не_время(token):
    raise ValueError(f"не время: {token}")


def parse_time_part(token):
    """«14:00», «9.30», «18-45» → time. Не время — None, и это не ошибка:
    вызывающий просто поймёт, что времени в строке не было."""
    m = re.fullmatch(r"(\d{1,2})[:.\-](\d{2})", token)
    if not m:
        return None
    часы, минуты = int(m.group(1)), int(m.group(2))
    if часы > 23 or минуты > 59:
        raise ValueError(f"не время: {token}")
    return dtime(часы, минуты)


def parse_date_input(text, today):
    """Человеческий ввод → date или datetime. Здесь, а не в браузере.

    Форма и CLI обязаны понимать ввод одинаково, иначе появятся задачи, которые
    завелись через форму, но не читаются движком. Поэтому разбор один, а форма
    только показывает, во что он превратился.

    Понимает: 2026-08-18 · 18.08.2026 · 18.08 · сегодня · завтра · послезавтра ·
    +3 (через три дня) · пн, вторник (ближайший такой день после сегодня).

    Со временем: «завтра 14:00», «18.08 09:30», «+3 18:00». Просто «14:00» —
    сегодня в это время. Без времени возвращается date, и шаг считается
    назначенным на весь день: рабочие часы для него считаются от начала дня.
    """
    if text is None:
        return None
    s = str(text).strip().lower().replace("ё", "е")
    if not s:
        return None

    # Время отделяем до всего остального: «18.08 09:30» — это дата и время, а не
    # два непонятных числа. Голое «14:00» означает сегодня в это время.
    части = s.split()
    if len(части) > 1:
        часть_времени = parse_time_part(части[-1])
        if часть_времени is not None:
            день = parse_date_input(" ".join(части[:-1]), today)
            if день is None:
                raise ValueError(f"есть время, но нет даты: {text!r}")
            return datetime.combine(as_date(день), часть_времени)
    elif ":" in s:
        return datetime.combine(today, parse_time_part(s) or _не_время(s))

    if s in ОТНОСИТЕЛЬНЫЕ:
        return today + timedelta(days=ОТНОСИТЕЛЬНЫЕ[s])

    if s.startswith("+") and s[1:].isdigit():
        return today + timedelta(days=int(s[1:]))

    день = ДНИ_НЕДЕЛИ.get(s.replace("воскресенье", "воскресенье"))
    if день is None:
        день = ДНИ_НЕДЕЛИ.get(s)
    if день is not None:
        вперёд = (день - today.weekday()) % 7 or 7  # «пн» в понедельник — следующий
        return today + timedelta(days=вперёд)

    m = re.fullmatch(r"(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2}|\d{4}))?", s)
    if m:
        д, мес, год = int(m.group(1)), int(m.group(2)), m.group(3)
        if год is None:
            дата = date(today.year, мес, д)
            # «18.08» в сентябре — это следующий год, а не прошедшая дата
            return дата if дата >= today else date(today.year + 1, мес, д)
        год = int(год)
        return date(год + 2000 if год < 100 else год, мес, д)

    return as_date(s)  # ISO и всё, что понимает datetime.fromisoformat


# Windows не пускает эти символы в имена файлов, а имя задачи — это имя файла.
# Проверяем и на маке тоже: вольт уезжает на Windows, и задача, заведённая здесь,
# должна там открыться.
FORBIDDEN_IN_NAME = set('\\/:*?"<>|')

# Имя файла целиком (с расширением и путём) на Windows ограничено 260 символами.
# С запасом на путь к вольту берём предел на само название.
MAX_TITLE = 120


def validate_new_task(data, existing_names, today):
    """Проверка задачи до записи. Возвращает список ошибок по полям.

    Отдельно от формы намеренно: правила должны быть в одном месте, иначе форма
    и CLI разойдутся, и в вольт попадёт то, что движок потом не прочитает.
    Ошибки возвращаются списком, а не первым попавшимся исключением, — форме надо
    подсветить все проблемные поля разом, а не гонять человека по кругу.
    """
    errors = []

    title = (data.get("title") or "").strip()
    if not title:
        errors.append({"field": "title", "error": "Название задачи обязательно"})
    elif len(title) > MAX_TITLE:
        errors.append({"field": "title",
                       "error": f"Название длиннее {MAX_TITLE} символов"})
    elif set(title) & FORBIDDEN_IN_NAME:
        плохие = "".join(sorted(set(title) & FORBIDDEN_IN_NAME))
        errors.append({"field": "title",
                       "error": f"В названии нельзя символы {плохие} — это имя файла"})
    elif title.lower() in {n.lower() for n in existing_names}:
        errors.append({"field": "title",
                       "error": "Задача с таким названием уже есть"})
    elif title != title.strip(". "):
        errors.append({"field": "title",
                       "error": "Название не должно кончаться точкой или пробелом"})

    steps = data.get("steps") or []
    if not steps:
        errors.append({"field": "steps", "error": "Нужен хотя бы один шаг"})
    for i, step in enumerate(steps):
        поле = f"steps.{i}"
        if not (step.get("title") or "").strip():
            errors.append({"field": f"{поле}.title", "error": "Название шага обязательно"})
        raw = (step.get("control_date") or "").strip()
        if raw:
            try:
                parse_date_input(raw, today)
            except (ValueError, TypeError):
                errors.append({"field": f"{поле}.control_date",
                               "error": "Дату не понял. Можно: 18.08 · завтра · +3 · пн"})
    return errors


def build_task(data, today):
    """Данные формы → frontmatter задачи. Без записи на диск.

    Идентификаторы шагов раздаёт движок, а не форма: они должны быть плотными и
    по порядку, иначе `done <задача> 3` будет попадать не туда.
    """
    steps = []
    for i, step in enumerate(data.get("steps") or [], start=1):
        raw = (step.get("control_date") or "").strip()
        steps.append({
            "id": i,
            "title": step["title"].strip(),
            "status": OPEN,
            "control_date": parse_date_input(raw, today) if raw else None,
            "completed_date": None,
            "log": [],
        })
    tags = [t.strip() for t in (data.get("tags") or []) if t and t.strip()]
    meta = {
        "schema": SCHEMA,
        "type": "task",
        "title": data["title"].strip(),
        "created": today,
        "tags": tags,
        "steps": steps,
    }
    return meta


def cmd_create(args, today):
    """Завести задачу. Единственный путь создания — и из формы, и из CLI.

    JSON на входе: {"title": "...", "tags": [...], "steps": [{"title": "...",
    "control_date": "2026-08-20"}], "body": "..."}
    """
    raw = sys.stdin.read() if args.json == "-" else args.json
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [{"field": None, "error": f"битый JSON: {e}"}]}

    existing = [t["path"].stem for t in load_tasks()]
    errors = validate_new_task(data, existing, today)
    if errors:
        return {"ok": False, "errors": errors}

    meta = build_task(data, today)
    path = TASKS_DIR / f"{meta['title']}.md"
    if path.exists():
        return {"ok": False, "errors": [{"field": "title", "error": "Файл уже существует"}]}

    task = {"path": path, "meta": meta, "body": (data.get("body") or "").strip() + "\n"}
    save(task, today)
    return {"ok": True, "task": path.stem, "path": str(path),
            "steps": len(meta["steps"]), "status": task["meta"]["status"]}


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
            "forced": bool(args.force), "broken": list(BROKEN)}


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
    return {"today": today.isoformat(), "tasks": out, "broken": list(BROKEN)}


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


def cmd_fail(args, today):
    """«Не будет сделано» — шаг закрывается проваленным, задача идёт дальше.

    Четвёртый исход из раздела 6.4 ТЗ, намеренно менее заметный в интерфейсе:
    он не должен становиться лёгким путём отмахнуться. Отличается от «снят» тем,
    что снятый шаг перестал быть нужен, а проваленный был нужен и не случился —
    и в истории это разные вещи.
    """
    task = find_task(args.task)
    step = get_step(task, args.step)
    if is_closed(step):
        sys.exit(f"шаг {args.step} уже {step.get('status')}")
    step["status"] = FAILED
    log_event(step, "failed", today, reason=args.reason)
    nxt = current_step(task)
    if nxt and not nxt.get("control_date"):
        nxt["control_date"] = today
    save(task, today)
    return {"ok": True, "task": task["path"].stem, "step": args.step, "status": FAILED,
            "next_step": nxt.get("id") if nxt else None,
            "task_status": task_status(task, today)}


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
    sub.add_parser("feed", help="лента «Что сегодня»").set_defaults(func=cmd_feed)
    sub.add_parser("backlog", help="всё просроченное — разбор завала").set_defaults(func=cmd_backlog)
    sub.add_parser("list", help="все задачи").set_defaults(func=cmd_list)
    r = sub.add_parser("refresh", help="пересчитать сводку во всех задачах (перед сборкой)")
    r.add_argument("--force", action="store_true",
                   help="переписать все файлы, даже если сводка не изменилась — "
                        "нужно после смены схемы, чтобы привести вольт к новому виду")
    r.set_defaults(func=cmd_refresh)

    c = sub.add_parser("create", help="завести задачу из JSON (её же зовёт форма)")
    c.add_argument("json", help='JSON или "-" для stdin')
    c.set_defaults(func=cmd_create)

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

    fl = sub.add_parser("fail", help="не будет сделано — шаг провален, задача идёт дальше")
    fl.add_argument("task")
    fl.add_argument("step")
    fl.add_argument("--reason", required=True, help="причина обязательна")
    fl.set_defaults(func=cmd_fail)

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
