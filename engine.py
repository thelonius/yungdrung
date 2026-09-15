#!/usr/bin/env python3
"""Движок шагов Yungdrung.

Единственный, кто пишет шаги и даты в стор. Без LLM: всё, что здесь считается —
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
  python3 engine.py export --to выгрузка.xlsx   весь стор в Excel

Задача указывается куском названия: "грант" найдёт «Заявка на грант ФПГ».
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

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

# Импорты ниже — после настройки кодировки потоков и после проверки pyyaml:
# понятная строка «нужен pyyaml» полезнее ImportError из середины модуля.
# Отсюда E402 по всему блоку, тот же приём, что в server.py.
import backup  # noqa: E402
import kb  # noqa: E402
import recurrence as rec  # noqa: E402
import settings as cfg  # noqa: E402
import store  # noqa: E402
import templates as tpl  # noqa: E402
import worktime  # noqa: E402
# Доменные функции и разбор дат переехали в `domain/` (REFACTOR.md, срез 1a).
# Здесь они реэкспортируются: тесты, `server.py` и `templates.py` зовут их как
# `engine.parse_date_input` и `engine.is_closed`, и ломать эти адреса ради
# переезда файла незачем — они уйдут вместе с самим `engine.py` в срезе 5.
from domain.names import FORBIDDEN_IN_NAME, MAX_TITLE, title_error  # noqa: E402,F401
from domain.ru_dates import (  # noqa: E402,F401
    ДНИ_НЕДЕЛИ, ОТНОСИТЕЛЬНЫЕ, as_date, parse_date_input, parse_stored_control,
    parse_time_part,
)
# Планирование шагов (проверка, дефолты дат, сборка и правка) переехало в
# `domain/steps_plan.py` (срез 2); здесь те же имена для тестов и `templates.py`.
import domain.steps_plan as steps_plan  # noqa: E402
from domain.steps_plan import (  # noqa: E402,F401
    MODES, _parse_optional_date, _финиш_узла, apply_task_edit, build_task,
    default_step_start, resolve_steps, soft_warnings, validate_new_task,
    validate_task_edit, walk_resolved,
)
from domain.steps import (  # noqa: E402,F401
    DONE, FAILED, OPEN, SKIPPED, STATUS_RU, _closure, current_step, current_steps,
    is_closed, is_group, leaves_of, stall_count, step_view, steps_of, task_status,
    task_summary,
)
import core.attachments as core_attachments  # noqa: E402
import core.clock  # noqa: E402
import core.feed as core_feed  # noqa: E402
import core.mark as core_mark  # noqa: E402
import core.persist as core_persist  # noqa: E402
import core.recur as core_recur  # noqa: E402
import core.tasks as core_tasks  # noqa: E402
import core.templates as core_templates  # noqa: E402
from core.context import Context  # noqa: E402
from core.errors import CoreError, NotFound, ValidationError  # noqa: E402
from core.models_tasks import TaskEditIn  # noqa: E402

SCHEMA = core_persist.TASK_SCHEMA
VAULT = Path(os.environ.get("YUNGDRUNG_VAULT", Path(__file__).resolve().parent))
KB_DIR = VAULT / "База"


def db_path():
    """Путь к базе. Функция, а не константа — по той же причине, что и
    `get_reasons()` ниже: `VAULT` в тестах подменяют через monkeypatch уже
    после импорта, и захардкоженный путь эту подмену не увидит."""
    return VAULT / "стор.db"


def _ctx():
    """Контекст ядра от текущего VAULT. Функция, а не константа: тесты подменяют
    `VAULT` через monkeypatch уже после импорта, и захардкоженный контекст эту
    подмену не увидел бы."""
    return Context(VAULT)


def get_store():
    return _ctx().store


def get_reasons():
    """Справочник причин, раздел 5.4 ТЗ. Причина обязательна при «не сделано»,
    переносе и провале: без неё счётчик переносов показывает, что шаг буксует,
    но не показывает, обо что.

    Список редактируется в настройках (settings.py) — здесь только чтение.
    Функция, а не константа: захардкоженный список нельзя переименовать или
    заархивировать из интерфейса, а settings.py уже это умеет. `VAULT` берём
    именно из engine (не из settings.cfg.VAULT — тот вычислен независимо при
    импорте и не отследит подмену в тестах через monkeypatch).
    """
    return cfg.active_reason_names(cfg.settings_path(VAULT))


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
    "reopened": "переоткрыт",
}


# --- чтение и запись -------------------------------------------------------

def parse_file(path):
    """Frontmatter + тело. Тело сохраняем как есть: его пишет заказчик, не мы."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise ValueError(f"{path.name}: нет frontmatter")
    _, fm, body = raw.split("---", 2)
    return yaml.safe_load(fm) or {}, body.lstrip("\n")


# Задачи теперь читает store.py — SQLite не оставляет полуразобранных строк
# так, как правленный руками YAML оставлял полуразобранные файлы. Список остаётся
# (всегда пустой) ради стабильности формы ответа: `cmd_feed`/`cmd_backlog`/
# `cmd_next`/`cmd_list`/`cmd_refresh` отдают "broken" по контракту, и снимать
# ключ без отдельного решения — не эта задача.
BROKEN = []


def load_tasks():
    return get_store().load_tasks()


def find_task(fragment):
    """Задача по куску названия. Собирается только найденная.

    Раньше здесь читался весь стор со шагами и журналом, чтобы сверить кусок
    строки с названиями, — и потому каждая отметка шага стоила столько же,
    сколько лента: 180 мс на пяти тысячах закрытых задач. Теперь названия
    приходят одной колонкой, а шаги и журнал собираются у одной задачи.

    Сравнение осталось в Python: `lower()` и `LIKE` в SQLite работают только по
    ASCII, и «ГРАНТ» не нашёл бы «грант».
    """
    return get_store().task_by_id(find_task_id(fragment))


def find_task_id(fragment):
    """Только id задачи по куску названия — без сборки шагов и журнала.
    Адаптерам отметок (`_mark`, `cmd_undo`) больше ничего и не нужно: задачу
    по id загрузит ядро, и собирать её здесь второй раз незачем."""
    frag = fragment.lower()
    hits = [(tid, title) for tid, title in get_store().titles() if frag in title.lower()]
    if not hits:
        sys.exit(f"нет задачи по «{fragment}»")
    if len(hits) > 1:
        sys.exit("подходит несколько: " + ", ".join(title for _, title in hits))
    return hits[0][0]


# Записи базы знаний, которые не разобрались при последнем чтении. Та же логика,
# что у BROKEN: опечатка в заметке заказчика не должна тихо выкидывать запись
# из автораспознавания без единого сигнала об этом.
KB_BROKEN = []


def load_kb_entries():
    """Записи базы знаний для kb.build_index — раздел 5.7 ТЗ.

    Источник — база, если в неё уже переехали, иначе `База/*.md`. Порядок
    именно такой: этап (b) переносит записи один раз, и после переноса markdown
    остаётся на диске как был (мы его не удаляем — это данные заказчика), но
    источником правды перестаёт быть. Читать оба разом нельзя: одна и та же
    запись дала бы два совпадения в тексте.
    """
    KB_BROKEN.clear()
    из_базы = get_store().load_kb_notes()
    if из_базы:
        return [{"id": з["id"], "title": з["title"], "aliases": з["aliases"]}
                for з in из_базы]
    if not KB_DIR.is_dir():
        return []
    out = []
    for path in sorted(KB_DIR.glob("*.md")):
        try:
            meta, _ = parse_file(path)
        except Exception as e:
            reason = " ".join(str(e).split())[:200]
            KB_BROKEN.append({"file": path.name, "error": reason})
            continue
        if meta.get("type") != "note":
            continue
        title = (meta.get("title") or "").strip()
        if not title:
            KB_BROKEN.append({"file": path.name, "error": "нет названия"})
            continue
        out.append({
            "id": path.stem,
            "title": title,
            "aliases": meta.get("aliases") or meta.get("synonyms") or [],
        })
    return out


# --- запись ----------------------------------------------------------------

log_event = core_mark.log_event


def get_step(task, step_id):
    """Найти шаг для отметки (`core.mark.find_step`); для CLI отказ — выход с
    текстом, как и раньше."""
    try:
        return core_mark.find_step(task, step_id)
    except CoreError as e:
        sys.exit(str(e))


_step_snapshot = core_mark.step_snapshot


STEPS_START = steps_plan.STEPS_START
STEPS_END = steps_plan.STEPS_END



def _sync_tags_to_catalog(tags):
    _ctx().sync_tags(tags)


def save(task, today, expected_step=None):
    """Единственный путь записи задачи — `core.persist.save_task`. Здесь только
    подстановка контекста: остальной engine.py зовёт `save(task, today)` из двух
    десятков мест, и адрес сохранён до демонтажа файла (срез 5)."""
    core_persist.save_task(_ctx(), task, today, expected_step=expected_step)


# --- команды ---------------------------------------------------------------



def cmd_feed(args, today):
    """Лента «Что сегодня» — раздел 6.1 ТЗ. Считает `core.feed`; здесь только
    аргументы CLI и `broken` из чтения базы знаний."""
    итог = core_feed.feed(_ctx(), _now(args, today), _work(args)).model_dump()
    итог["broken"] = list(BROKEN)
    return итог


def cmd_backlog(args, today):
    """Разбор завала — раздел 6.9 ТЗ, считает `core.feed.backlog`."""
    итог = core_feed.backlog(_ctx(), _now(args, today), _work(args)).model_dump()
    итог["broken"] = list(BROKEN)
    return итог


def _now(args, today):
    """Момент, от которого считаем: `--today` задаёт дату, время берём текущее
    (см. `core.clock.derive_now`)."""
    return core.clock.derive_now(today, getattr(args, "now", None) if args else None)


def _work(args):
    """Рабочие часы: настройки из файла — база, аргументы вызова — оверрайд
    поверх них (`Context.work`)."""
    return _ctx().work(
        start=getattr(args, "work_start", None) if args else None,
        end=getattr(args, "work_end", None) if args else None,
        weekends=getattr(args, "weekends", None) if args else None)


def cmd_next(args, today):
    due, stalled = [], []
    for task in load_tasks():
        status = task_status(task, today)
        if status not in ("overdue", "due", "no_date"):
            continue
        for step in current_steps(task):
            view = step_view(task, step, today)
            view["status"] = status
            due.append(view)
            if view["stalled"] >= 3:
                stalled.append(view)
    due.sort(key=lambda v: (-v["overdue_days"], v["task"]))
    return {"today": today.isoformat(), "due": due, "stalled": stalled,
            "broken": list(BROKEN)}


def cmd_create(args, today):
    """Завести задачу. Единственный путь создания — и из формы, и из CLI.

    JSON на входе: {"title": "...", "tags": [...], "steps": [{"title": "...",
    "control_date": "2026-08-20"}], "body": "..."}

    Проверка, сборка meta и запись — `core.tasks.create_task`; здесь только
    разбор JSON и перевод исключения в прежнюю форму CLI.
    """
    raw = sys.stdin.read() if args.json == "-" else args.json
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [{"field": None, "error": f"битый JSON: {e}"}]}

    try:
        task = core_tasks.create_task(_ctx(), data, today)
    except ValidationError as e:
        return {"ok": False, "errors": e.errors}
    return {"ok": True, "task": task["path"].stem, "task_id": task["path"].id,
            "steps": len(task["meta"]["steps"]), "status": task["meta"]["status"]}


def cmd_update(args, today):
    """Правка задачи из карточки: заголовок, даты, теги, заметка, состав и
    порядок шагов. Статусы шагов через `done`/`notdone`/`defer`/`fail`/`skip`.

    Проверка, применение правки, запись и перенос владения вложениями/базы
    знаний при переименовании — `core.tasks.update_task`. Поля из сырого JSON
    подставляются в `TaskEditIn` со значениями по умолчанию для отсутствующих
    ключей (`title`/`steps`), а не напрямую `TaskEditIn(**data)`: CLI и старая
    форма годами присылали неполные словари, и `validate_task_edit` сама
    превращает пустое название или пустой список шагов в обычную ошибку поля,
    а не в необработанное исключение Pydantic.
    """
    raw = sys.stdin.read() if args.json == "-" else args.json
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [{"field": None, "error": f"битый JSON: {e}"}]}

    task_id = find_task_id(args.task)
    edit = TaskEditIn(
        title=data.get("title") or "", start_date=data.get("start_date"),
        tags=data.get("tags"), body=data.get("body"), steps=data.get("steps") or [],
        force=bool(getattr(args, "force", False)))
    try:
        result = core_tasks.update_task(
            _ctx(), task_id, edit, today, _now(args, today), _work(args))
    except ValidationError as e:
        return {"ok": False, "errors": e.errors}
    return {"ok": True, "task": result.task, "task_id": result.task_id,
            "status": result.card.status_ru, "steps": result.steps}


def cmd_cancel(args, today):
    """Отменить задачу целиком — раздел 6.3 ТЗ, кнопка «Отменить задачу».

    Не «снять» (это про один шаг) и не удаление (файл и история остаются).
    Отменённая задача перестаёт быть просроченной или ждущей: её статус
    вычисляется первым делом в `task_status`, раньше любого правила про шаги.
    """
    task_id = find_task_id(args.task)
    try:
        card = core_tasks.cancel(_ctx(), task_id, getattr(args, "reason", None), today,
                                 _now(args, today), _work(args))
    except CoreError as e:
        sys.exit(str(e))
    return {"ok": True, "task": card.task, "task_id": card.task_id, "status": card.status_ru}


def cmd_close(args, today):
    """Закрыть задачу вручную — раздел 6.3 ТЗ, кнопка «Закрыть» в шапке карточки.

    Толкование неоднозначного пункта: ТЗ перечисляет кнопку в списке шапки без
    описания действия, что она делает — не факт от заказчика, а наша
    интерпретация, самая естественная пара к «Отменить». Отмена говорит «эта
    работа не нужна», закрытие — «работа сделана вся разом», не проходя
    оставшиеся шаги по одному. Отсюда «сделано», а не «снят»: задача была
    нужна и её довели до конца, просто не оставляя записи о каждом шаге.

    Отменённую задачу так не закрыть — это два разных исхода одной задачи, а
    не последовательность. Уже полностью закрытая — не ошибка: активных шагов
    нет с первого шага, и повтор ничего не портит (та же идемпотентность,
    что у `delete_attachment`).
    """
    task_id = find_task_id(args.task)
    try:
        result = core_tasks.close(_ctx(), task_id, today, _now(args, today), _work(args))
    except CoreError as e:
        sys.exit(str(e))
    return {"ok": True, "task": result.card.task, "task_id": result.task_id,
            "closed_steps": result.closed_steps, "task_status": result.card.task_status}


def cmd_delete(args, today):
    """Удалить задачу насовсем. Подтверждение — дело интерфейса, не движка:
    здесь только сам необратимый шаг."""
    task_id = find_task_id(args.task)
    result = core_tasks.delete(_ctx(), task_id)
    return {"ok": True, "task": result.task, "task_id": result.task_id,
            "deleted": result.deleted}


def cmd_reopen(args, today):
    """Отменить закрытие последнего сделанного шага — раздел 6.3.5 ТЗ:
    задача закрывается автоматически, «показывается подтверждение с
    возможностью отменить». Открывает конкретный шаг, не «последний вообще»:
    порядок закрытия и порядок в списке могут не совпасть при провале/снятии
    более раннего шага.
    """
    task_id = find_task_id(args.task)
    try:
        card = core_tasks.reopen(_ctx(), task_id, args.step, today,
                                 _now(args, today), _work(args))
    except CoreError as e:
        sys.exit(str(e))
    return {"ok": True, "task": card.task, "task_id": card.task_id, "step": args.step,
            "task_status": card.status_ru}


def _recurrence_view(шаблон):
    """Правило повторения с человеческой подписью — для карточки шаблона.
    Подпись считает `recurrence.describe`, а не морда: тот же принцип, что и
    везде — оболочка не пересказывает правило словами сама."""
    правило = шаблон.get("recurrence")
    if not правило:
        return None
    return {**правило, "description": rec.describe(
        {k: v for k, v in правило.items() if k != "anchor"})}


def cmd_templates(args, today):
    """Список шаблонов. Отдаём с предпросмотром на сегодня: заказчику надо видеть,
    какие даты получатся, а не только имена.

    Склад и вложения читаются через `core.templates.store`/`Context.store`, а не
    напрямую: тот же адрес стора, что видит `/api/v1`, — не два способа его
    собрать. Выходные для предпросмотра берутся из настроек заказчика
    (`ctx.work()`), не из дефолтов — иначе при включённой работе по выходным
    строка сама себе противоречила бы (см. `core.templates.preview`)."""
    ctx = _ctx()
    старт = parse_date_input(args.start, today) if getattr(args, "start", None) else today
    из_даты = as_date(старт)
    склад = core_templates.store(ctx)
    файлы = ctx.store
    out = []
    for шаблон in склад.all():
        out.append({
            "name": шаблон["name"],
            "tags": шаблон.get("tags") or [],
            "steps": len(шаблон.get("steps") or []),
            "attachments": len(файлы.list_attachments("template", шаблон["name"])),
            "preview": tpl.preview(шаблон, из_даты, ctx.work()),
            "recurrence": _recurrence_view(шаблон),
        })
    return {"templates": out, "count": len(out), "start": из_даты.isoformat()}


def cmd_template_preview(args, today):
    """Какие даты дадут шаги ещё не сохранённого шаблона. Раздел 5.6 ТЗ.

    Форма шаблона показывает сдвиги в днях, а днями человек не думает: «+10»
    выглядит правдоподобно ровно до того момента, когда попадает на праздники.
    Ту же роль играет `/api/parse-date` в форме задачи — считает и проверяет
    ядро, страница показывает ответ.

    Название здесь не проверяется намеренно: даты от него не зависят, а человек
    набирает шаги раньше, чем придумывает имя, и ругаться на пустое поле в
    предпросмотре незачем — на сохранении оно и так не пройдёт.
    """
    raw = sys.stdin.read() if args.json == "-" else args.json
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [{"field": None, "error": f"битый JSON: {e}"}]}

    # Расчёт переехал в `core.templates.preview` (§3.4 среза 2): та же проверка
    # и та же сборка строк, только плохая дата теперь не роняет команду
    # исключением, а возвращает `ok: false`, как остальные живые проверки.
    результат = core_templates.preview(_ctx(), data, getattr(args, "start", None), today)
    if not результат.ok:
        return {"ok": False, "errors": [e.model_dump() for e in результат.errors]}
    return {"ok": True, "start": результат.start.isoformat(),
            "start_text": результат.start_text,
            "steps": [s.model_dump() for s in результат.steps]}


def cmd_recurrence_preview(args, today):
    """Проверить и описать правило без сохранения — живая подпись в форме,
    та же роль, что у `/api/parse-date` для одиночной даты.

    Проверка и подпись переехали в `core.templates.preview_rule`; здесь
    остаётся только разбор JSON правила из CLI-аргумента."""
    try:
        правило = json.loads(args.rule) if isinstance(args.rule, str) else (args.rule or {})
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [{"field": "recurrence", "error": f"битый JSON: {e}"}]}

    результат = core_templates.preview_rule(
        getattr(args, "anchor", None), правило, today, _ctx().work())
    if not результат.ok:
        return {"ok": False, "errors": [e.model_dump() for e in результат.errors]}
    return {"ok": True, "description": результат.description,
            "anchor": результат.anchor.isoformat(),
            "preview": [{"date": d.date.isoformat(), "text": d.text} for d in результат.preview]}


def cmd_recurrence_parse(args, today):
    """Текст «каждый вторник» → правило повторения. Требование R23.

    Разбирает `recurrence.parse_text` — детерминированно, без модели: это
    словарь на два десятка слов, а не связная речь, и по границе из CLAUDE.md
    он на нашей стороне. Заодно правило не зависит от сети, а форма может
    дёргать разбор на каждое нажатие клавиши.

    Наружу уходит и правило, и его подпись: разобрав текст, надо показать
    человеку, как система его поняла, — «каждую неделю по вторникам» рядом с
    ближайшими датами. Иначе разбор молча съедает опечатку.
    """
    результат = core_templates.parse_rule(getattr(args, "text", None))
    if not результат.ok:
        return {"ok": False, "errors": [e.model_dump() for e in результат.errors]}
    return {"ok": True, "rule": результат.rule.model_dump(),
            "description": результат.description}


def cmd_set_recurrence(args, today):
    """Прикрепить или снять правило повторения с шаблона.

    Идёт через `core.templates.set_recurrence` → `Store.save` целиком, а не
    отдельным полем: у шаблона один путь записи, тот же, что у формы шагов, —
    иначе однажды разойдутся форматом.
    """
    if getattr(args, "clear", False):
        правило = None
    else:
        правило = args.rule if isinstance(args.rule, dict) else json.loads(args.rule)
    try:
        карточка = core_templates.set_recurrence(_ctx(), args.name, правило, today)
    except NotFound as e:
        return {"ok": False, "errors": [{"field": "name", "error": str(e)}]}
    except ValidationError as e:
        return {"ok": False, "errors": e.errors}
    return {"ok": True, "template": карточка.name,
            "recurrence": карточка.recurrence.model_dump(mode="json")
            if карточка.recurrence else None}


def cmd_save_template(args, today):
    """Создать шаблон с нуля или переписать существующий целиком.

    Раньше единственным путём завести шаблон было «сохранить как» у уже готовой
    задачи (`cmd_template_from_task`) — с нуля собрать было нечем, хотя
    `tpl.Store.save` шаблон без задачи-источника принимает и так. Здесь тот же
    вызов, что там, только данные приходят прямо от формы или из CLI, а не из
    развёрнутых дат существующей задачи.

    JSON на входе: {"name": "...", "tags": [...], "steps": [{"title": "...",
    "offset_days": 0, "time_of_day": "14:00"}], "body": "..."}
    """
    raw = sys.stdin.read() if args.json == "-" else args.json
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [{"field": None, "error": f"битый JSON: {e}"}]}

    try:
        карточка = core_templates.save(_ctx(), data, today)
    except ValidationError as e:
        return {"ok": False, "errors": e.errors}
    return {"ok": True, "template": карточка.name, "steps": len(карточка.steps)}


def cmd_template_delete(args, today):
    """Удалить шаблон насовсем. Подтверждение — дело интерфейса, не движка,
    как и у `cmd_delete` для задач.

    Файлы вложений шаблона на диске не трогаем: они адресованы своим sha256
    (`attachments.py`), и `cmd_delete` для задач строки в `attachments` тоже
    не чистит — тот же приём здесь для единообразия. Ссылка на исчезнувший
    шаблон никого не ломает: её читают только по source_type/source_id
    конкретной задачи или шаблона, а не перечислением всех строк подряд.
    """
    try:
        core_templates.delete(_ctx(), args.name)
    except NotFound as e:
        return {"ok": False, "errors": [{"field": "name", "error": str(e)}]}
    return {"ok": True, "template": args.name, "deleted": True}


def _create_task_from_data(данные, today, existing=None):
    """Общий путь записи новой задачи — из формы, из шаблона, из повторения.

    Один путь, а не три копии: второй писатель рано или поздно разойдётся с
    первым в мелочи вроде порядка полей или сводки. `existing` передают, когда
    список задач уже прочитан вызывающим (движок повторений создаёт несколько
    задач подряд, и читать стор заново перед каждой — лишний проход по файлам).
    """
    try:
        задача = core_tasks.create_task(
            _ctx(), данные, today, existing=existing,
            template_name=данные.get("template_name"), cycle_key=данные.get("cycle_key"))
    except ValidationError as e:
        return None, e.errors
    return задача, None


def copy_template_attachments(template_name, task_name, today):
    """Перенести файлы шаблона на заведённую из него задачу. Возвращает,
    сколько перенесено. Шим над `core.attachments.copy_template_to_task`:
    зовётся из `cmd_from_template` и `cmd_recur`, пока те не переехали."""
    return core_attachments.copy_template_to_task(_ctx(), template_name, task_name, today)


def cmd_from_template(args, today):
    """Завести задачу из шаблона."""
    склад = tpl.JsonStore(VAULT)
    шаблон = склад.get(args.name)
    if not шаблон:
        return {"ok": False, "errors": [{"field": "name",
                                         "error": f"нет шаблона «{args.name}»"}]}
    старт = as_date(parse_date_input(args.start, today)) if args.start else today
    данные = tpl.expand(шаблон, старт, title=args.title)

    задача, errors = _create_task_from_data(данные, today)
    if errors:
        return {"ok": False, "errors": errors}
    файлы = copy_template_attachments(шаблон["name"], задача["path"].stem, today)
    _record_manual_cycle(шаблон, задача["path"].stem, today)
    return {"ok": True, "task": задача["path"].stem, "template": шаблон["name"],
            "steps": len(задача["meta"]["steps"]), "attachments": файлы,
            "status": задача["meta"]["status"]}


# --- повторения --------------------------------------------------------

def recurrence_state_path(vault=None):
    """Журнал повторений лежит рядом с стором, но не в нём: это отметки «какой
    цикл был последним», а не данные заказчика. Тот же приём, что у файла
    доставки в notify.py — потеря файла означает лишний повтор, а не потерю
    задачи. Тонкая обёртка над `core.recur.state_path`: `vault` здесь только
    для тестов, что подменяют его отдельно от `engine.VAULT`."""
    return core_recur.state_path(Context(vault) if vault else _ctx())


def load_recurrence_state():
    return core_recur.load_state(_ctx())


def save_recurrence_state(state):
    core_recur.save_state(_ctx(), state)


def recurring_title(name, cycle_date):
    """Имя автосозданной задачи. Голое имя шаблона совпало бы с прошлым циклом —
    ровно та коллизия, что при ручном разворачивании ловит понятную ошибку
    в форме, а здесь заведение идёт без человека и споткнуться не о что."""
    return core_recur.recurring_title(name, cycle_date)


def _cycle_closed(task_name, today):
    """Закрыт ли цикл — по статусу задачи, которую он породил.

    Файл мог исчезнуть: заказчик вправе удалить задачу руками. Отсутствие
    считаем закрытием, а не блокировкой навсегда — иначе удалённая вручную
    задача остановила бы правило насовсем, и это тише любой ошибки.
    """
    return core_recur.cycle_closed(_ctx(), task_name, today)


def _recompute_previous(запись, today):
    """`previous` из журнала повторений с пересчитанным на сегодня `closed`.

    Статус закрытия не хранится, а вычисляется заново из фактического состояния
    задачи при каждом обращении (см. `_cycle_closed`) — и `cmd_recur`, и запись
    цикла из `from-template` должны считать его одинаково, иначе один сочтёт
    цикл открытым, а другой закрытым, и решения разойдутся.
    """
    return core_recur.recompute_previous(_ctx(), запись, today)


def _record_manual_cycle(шаблон, task_name, today):
    """Задача, заведённая вручную через «Завести задачу» (`from-template`) по
    шаблону с активным повторением, закрывает собой тот же цикл, который иначе
    следующим прогоном создал бы `recur` — issue #2. Без этой записи `recur` не
    видит ручную задачу (её имя не совпадает с `recurring_title`) и заводит для
    того же цикла второй экземпляр.

    Журнал правится так, будто цикл создал сам `recur`: тот же расчёт через
    `due_cycles`, и только если он в самом деле нашёл цикл к созданию — цикл,
    заведённый заранее (раньше своего дня по `lead_days`) или заблокированный
    незакрытым предыдущим, ручная задача не трогает. Логика — `core.recur`;
    здесь только адрес контекста.
    """
    core_recur.record_manual_cycle(_ctx(), шаблон, task_name, today)


def cmd_recur(args, today):
    """Прогнать шаблоны с правилом повторения: создать очередной цикл или
    записать пропуск. Раздел 5.12 ТЗ.

    Идемпотентно в границах контракта: незакрытый цикл при повторном вызове в
    тот же день снова даёт пропуск, а не вторую задачу — `due_cycles` сам не
    продвигает журнал, пока предыдущий цикл не закрыт.
    """
    склад = tpl.JsonStore(VAULT)
    state = load_recurrence_state()
    work = worktime.settings()
    задачи_кэш = [t["path"].stem for t in load_tasks()]

    отчёт = []
    for шаблон in склад.all():
        правило = шаблон.get("recurrence")
        if not правило:
            continue
        имя = шаблон["name"]
        if getattr(args, "name", None) and имя != args.name:
            continue
        запись = state.get(имя) or {}
        предыдущий = _recompute_previous(запись, today)

        якорь = as_date(правило["anchor"])
        сила = bool(getattr(args, "force", False)) and getattr(args, "name", None) == имя
        try:
            решения = rec.due_cycles(
                {k: v for k, v in правило.items() if k != "anchor"}, якорь, today,
                previous=предыдущий, work=work, force=сила,
                limit=getattr(args, "limit", None) or 12)
        except rec.RuleError as e:
            отчёт.append({"template": имя, "errors": e.errors, "created": [], "skipped": []})
            continue

        # Статус закрытия старого цикла пересчитывается заново на каждом вызове
        # из фактического состояния задачи (см. выше), а не хранится, — поэтому
        # если новых циклов в этом прогоне не появилось, запись про «previous»
        # трогать не нужно вовсе: она и так будет пересчитана в следующий раз.
        созданы, пропущены, сбой = [], [], None
        for решение in решения:
            if решение["action"] == "skip":
                пропущены.append({"date": решение["date"].isoformat(),
                                  "message": решение["message"]})
                continue
            title = recurring_title(имя, решение["date"])
            данные = tpl.expand(шаблон, решение["date"], title=title)
            # Откуда задача взялась — в колонки, а не в разбор названия потом.
            # `решение["key"]` это `recurrence.cycle_key`, тот же ключ, которым
            # журнал повторений отличает уже записанный цикл от нового.
            данные["template_name"] = имя
            данные["cycle_key"] = решение["key"]
            задача, errors = _create_task_from_data(данные, today, existing=задачи_кэш)
            if errors:
                # Название занято чем-то посторонним — не тем же циклом: имя
                # несёт дату, и наше собственное совпадение уже поймала бы
                # проверка выше по этому же циклу. Останавливаем это правило,
                # остальные шаблоны идут дальше своим чередом.
                сбой = errors
                break
            задачи_кэш.append(задача["path"].stem)
            copy_template_attachments(имя, задача["path"].stem, today)
            созданы.append({"date": решение["date"].isoformat(), "task": задача["path"].stem})
            запись["previous"] = {"date": решение["date"].isoformat(), "closed": False,
                                  "task": задача["path"].stem}

        if созданы:
            state[имя] = запись
        if сбой:
            отчёт.append({"template": имя, "errors": сбой,
                          "created": созданы, "skipped": пропущены})
        else:
            отчёт.append({"template": имя, "created": созданы, "skipped": пропущены})

    save_recurrence_state(state)
    return {"today": today.isoformat(), "templates": отчёт,
            "created": sum(len(t["created"]) for t in отчёт)}


def cmd_template_from_task(args, today):
    """Сделать шаблон из существующей задачи — «я это уже делал, повтори так же».

    Сдвиги считаются от даты первого шага, поэтому шаблон переносим на любую дату
    старта. Задача при этом не меняется. Кусок названия остаётся входом CLI —
    `core.templates.from_task` адресуется `task_id`, как и весь `/api/v1`; заодно
    из группового шага без даты шаблон больше не собирает шаг-пустышку — в шаблон
    идут только листья (`domain.steps.leaves_of`).
    """
    задача = find_task(args.task)
    try:
        карточка = core_templates.from_task(_ctx(), задача["path"].id, args.name, today)
    except ValidationError as e:
        return {"ok": False, "errors": e.errors}
    return {"ok": True, "template": карточка.name, "from_task": задача["path"].stem,
            "steps": len(карточка.steps)}


def cmd_refresh(args, today):
    """Сводка по всем задачам на указанный день. **Ничего не пишет.**

    Статус устаревает сам по себе: задача становится просроченной оттого, что
    прошёл день, а не оттого, что кто-то её трогал. Но следствие из этого не
    «надо всё перезаписать», а обратное: раз статус зависит только от шагов и
    от даты, то ни одно хранимое поле от «сегодня» не зависит, и записывать
    нечего.

    Так было не всегда, и до замеров этого никто не проверил. Раньше здесь
    стоял `for task in load_tasks(): save(task, today)`, а `save_task` удаляет
    и вставляет заново все шаги и все строки журнала задачи. То есть команда
    перезаписывала весь стор строками, байт в байт равными прежним:
    сводка в БД не хранится (колонок под неё нет, source of truth в шагах), а
    `render_steps`/`put_steps_into_body`, которые когда-то писали блок шагов в
    тело заметки, после переезда в БД не вызываются ниоткуда. Единственным
    следствием было обновление `task["meta"]` в памяти, которое команда тут же
    возвращала и выбрасывала.

    Стоило это 21 секунду на 1250 закрытых задачах и 71 секунду на 5000
    (`tools/bench.py`, замер в `PROTOCOL.md` от 2026-09-07) — при том, что
    `remind.py` звал команду на каждом пробуждении, а планировщик Windows будит
    его каждые пять минут. Плюс по 20 тысяч сожжённых значений автоинкремента
    `step_log.id` за прогон, из-за чего id строки журнала не был стабилен.

    Побочные эффекты, которые команда попутно выполняла, никуда не пропали, а
    переехали туда, где им место: поисковый индекс и справочник тегов чинит
    `reindex`, а при обычной работе их поддерживает `save()` на каждой записи.

    `--force` остался в ответе ради обратной совместимости и не значит ничего:
    писать больше нечего, а значит нечего и форсировать.
    """
    итог = []
    for task in load_tasks():
        сводка = task_summary(task, today)
        итог.append({"task": task["path"].stem, "status": сводка["status"]})
    return {"today": today.isoformat(), "tasks": итог, "count": len(итог),
            "forced": bool(args.force), "broken": list(BROKEN)}


def parse_period_date(text, today):
    """Дата для границы периода — «с даты», «по дату» в архиве.

    Отдельно от `parse_date_input` намеренно, а не тот же вызов: тот планирует
    вперёд («пн» значит ближайший понедельник **после** сегодня — так и должен
    вести себя срок задачи), а граница периода смотрит в прошлое. «18.08» без
    года при разборе вперёд ушло бы в следующий август — для фильтра истории
    это означало бы «ничего не найдено» вместо прошлого августа, который
    заказчик и имел в виду. Здесь бортик наоборот: день без года берётся не
    позже сегодня, а если такой день ещё не наступил в этом году — годом раньше.
    """
    if text is None:
        return None
    s = str(text).strip().lower().replace("ё", "е")
    if not s:
        return None
    if s == "сегодня":
        return today
    if s == "вчера":
        return today - timedelta(days=1)
    if s == "позавчера":
        return today - timedelta(days=2)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?", s)
    if m:
        день, месяц = int(m.group(1)), int(m.group(2))
        if m.group(3):
            год = int(m.group(3))
            return date(год + 2000 if год < 100 else год, месяц, день)
        try:
            кандидат = date(today.year, месяц, день)
        except ValueError:
            raise ValueError(f"не дата: {text!r}")
        return кандидат if кандидат <= today else date(today.year - 1, месяц, день)
    raise ValueError(f"не дата: {text!r}")


def _finished_date(task):
    """Дата, по которой архив фильтрует задачу периодом.

    Для закрытой — самое позднее `completed_date` среди листьев: момент, когда
    задача реально завершилась, а не когда её завели. Для отменённой листья
    даты закрытия могут не нести вовсе (отмена не требует закрывать шаги), тогда
    берётся дата начала — точнее взять неоткуда, а вовсе не фильтровать её по
    периоду означало бы, что отменённые задачи не находятся периодом никогда.
    """
    листья = leaves_of(task)
    завершения = [as_date(s["completed_date"]) for s in листья if s.get("completed_date")]
    if завершения:
        return max(завершения)
    return as_date(task["meta"].get("start_date"))


def cmd_archive(args, today):
    """История: закрытые и отменённые задачи. Раздел 5.9 ТЗ, требование R24.

    Фильтры — тег и период, дальше по тексту ищет `cmd_search`: полнотекстовый
    поиск и просмотр списком — разные операции с разными ответами, а не одна
    команда с необязательными полями.

    Циклы одного повторения сворачиваются в одну строку с возможностью
    развернуть (решение по Q24: заказчик выбрал именно так, а не список всех
    экземпляров подряд). Свёртка — по `template_name`, тому самому полю из
    схемы v3: группа получает `kind="cycle_group"` и список задач внутри,
    одиночная задача или шаблон с единственным закрытым циклом — `kind="task"`,
    сворачивать нечего.
    """
    тег = getattr(args, "tag", None)
    ошибки = []
    since = until = None
    try:
        if getattr(args, "since", None):
            since = parse_period_date(args.since, today)
    except ValueError:
        ошибки.append({"field": "since", "error": f"Дату не понял: «{args.since}»"})
    try:
        if getattr(args, "until", None):
            until = parse_period_date(args.until, today)
    except ValueError:
        ошибки.append({"field": "until", "error": f"Дату не понял: «{args.until}»"})
    if ошибки:
        return {"ok": False, "errors": ошибки}

    строки = []
    for task in load_tasks():
        status = task_status(task, today)
        if status not in ("done", "cancelled"):
            continue
        теги = task["meta"].get("tags") or []
        if тег and тег not in теги:
            continue
        когда = _finished_date(task)
        if since and (когда is None or когда < since):
            continue
        if until and (когда is None or когда > until):
            continue
        листья = leaves_of(task)
        строки.append({
            "task": task["path"].stem,
            "status": STATUS_RU[status],
            "date": когда.isoformat() if когда else None,
            "category": теги,
            "steps_total": len(листья),
            "template_name": task["meta"].get("template_name"),
            "cycle_key": task["meta"].get("cycle_key"),
        })

    группы, одиночные = {}, []
    for r in строки:
        (группы.setdefault(r["template_name"], []) if r["template_name"] else одиночные).append(r)

    out = []
    for имя, циклы in группы.items():
        циклы.sort(key=lambda r: r["cycle_key"] or "", reverse=True)
        if len(циклы) > 1:
            out.append({"kind": "cycle_group", "template_name": имя,
                       "count": len(циклы), "tasks": циклы})
        else:
            out.append({"kind": "task", **циклы[0]})
    for r in одиночные:
        out.append({"kind": "task", **r})

    def _дата_сортировки(item):
        if item["kind"] == "cycle_group":
            даты = [t["date"] for t in item["tasks"] if t["date"]]
            return max(даты) if даты else ""
        return item.get("date") or ""

    out.sort(key=_дата_сортировки, reverse=True)
    return {"ok": True, "today": today.isoformat(), "count": len(строки), "items": out}


def cmd_list(args, today):
    """Все задачи — старый CLI `list`, он же список для веб-морды (issue #4:
    без него бакет «ждут» и бездатные задачи были видны только числом в счётчике
    ленты, ни разу строкой).

    Фильтр по статусу — на стороне ядра: те же ключи, что даёт `task_status`
    (`overdue`/`due`/`waiting`/`no_date`/`done`/`cancelled`/`empty`), а не
    текст, который оболочке пришлось бы держать в синхроне с движком.
    Сортировка по дате контроля тоже здесь: «ждёт неделю» и «нет даты вовсе» —
    разные ситуации (`no_date` — свой статус), но подряд в списке бездатные
    задачи всё равно должны быть видны отдельной группой, а не перемешаны.
    """
    статус_фильтр = getattr(args, "status", None) if args else None
    out = []
    for task in load_tasks():
        status = task_status(task, today)
        if статус_фильтр and status != статус_фильтр:
            continue
        листья = leaves_of(task)
        step = current_step(task)
        активные = current_steps(task)
        контроли = sorted(as_date(s["control_date"]) for s in активные if s.get("control_date"))
        out.append({
            "task": task["path"].stem,
            "status": status,
            "status_text": STATUS_RU[status],
            "category": task["meta"].get("tags") or [],
            "steps_done": sum(1 for s in листья if s.get("status") in (DONE, SKIPPED)),
            "steps_total": len(листья),
            "current": step.get("title") if step else None,
            "control_date": контроли[0].isoformat() if контроли else None,
        })
    out.sort(key=lambda t: (t["control_date"] is None, t["control_date"] or "", t["task"]))
    return {"today": today.isoformat(), "tasks": out, "broken": list(BROKEN)}


def cmd_show(args, today):
    """Задача целиком — для карточки. `state` шага (просрочен/сегодня/ждёт)
    считается тут же, а не на странице: карточка не знает про рабочее время
    и не должна вычислять просрочку сама, тот же принцип, что у ленты.

    Заметка отдаётся без блока шагов: блок пишет `render_steps`, и в поле
    карточки ему делать нечего. Без `body` в ответе карточка показывала пустую
    заметку и отправляла эту пустоту обратно при первом же сохранении —
    заметка заказчика стиралась, хотя он её не трогал.

    Сводка (status/current_step/control_date/progress/stalled) в БД не хранится
    (см. save()), значит `task["meta"]` из Store.load_tasks() её не несёт —
    домешиваем `task_summary()` тем же приёмом, что и save(), иначе карточка
    получала бы задачу без срока и прогресса.

    Форма ответа CLI-адаптера отдельная от `core.tasks.card` (`TaskCard`):
    здесь плоский список шагов «как в БД» плюс вычисленные поля, там дерево
    `CardStep` под HTTP-контракт (§2.1, дерево, `row`/`actions` для формы).
    Переписывать пиннутую golden-снимком форму ради переиспользования кода
    не входит в задачу переноса — добавлен только `task_id`.
    """
    task = find_task(args.task)
    now = _now(args, today)
    work = _work(args)
    заметка, _ = _strip_steps_block(task["body"])
    meta = {**task["meta"], **task_summary(task, today)}
    закрыт, _дети = _closure(steps_of(task))
    активные_id = {s["id"] for s in current_steps(task)}
    return {
        "task": task["path"].stem,
        "task_id": task["path"].id,
        "status": task_status(task, today),   # английский, для сравнений в JS
        "body": заметка.strip(),
        "meta": {k: v for k, v in meta.items() if k != "steps"},  # meta["status"] — русский
        "steps": [
            {**{k: (str(v) if isinstance(v, (date, datetime)) else v)
                for k, v in s.items() if k != "log"},
             "log": s.get("log") or [],
             # closed отдаётся явно: у группы нет статуса, её закрытие
             # вычисляется из детей, и карточка не должна считать это сама
             "closed": закрыт(s),
             # active — по той же причине. Раздел 6.3 ТЗ: «активный подсвечен
             # цветом», но не всякий незакрытый лист активен — в
             # последовательной цепочке это только первый: `status="pending"`
             # у листьев дальше по цепочке тот же самый, а спрашивать про них
             # ещё рано. Карточка не должна знать про группы и порядок цепочки,
             # чтобы это вычислить, — она просто красит то, что ей сказали.
             "active": s["id"] in активные_id,
             # stalled — по той же причине. Карточка считала его сама и считала
             # иначе, чем движок: брала `not_done` и `defer`, тогда как обычный
             # одиночный `defer` в счётчик намеренно не идёт (`stall_count`,
             # тест `test_defer_does_not_count_as_stalling`), зато идёт массовый
             # `mass_defer`. Один и тот же шаг показывал в ленте и в карточке
             # разные числа.
             "stalled": stall_count(s),
             "state": (None if is_group(s) else
                       worktime.due_state(s.get("control_date"), now, work)
                       if not is_closed(s) else None)}
            for s in steps_of(task)
        ],
    }


def _mark(op, args, today, shape):
    """Общий адаптер одиночных отметок: кусок названия → `task_id`, вызов
    `core.mark`, перевод исключений в прежние ответы CLI — структурная ошибка
    словарём, конфликт и «нет шага» — выход с текстом. `shape` раскладывает
    `MarkResult` в прежнюю форму JSON конкретной команды: она записана в
    `INTEGRATION.md`, и бот на неё рассчитывает."""
    task_id = find_task_id(args.task)
    try:
        r = core_mark.mark(
            _ctx(), op, task_id, args.step,
            reason=getattr(args, "reason", None),
            to=parse_stored_control(args.to) if getattr(args, "to", None) else None,
            today=today, now=_now(args, today), work=_work(args))
    except ValidationError as e:
        return {"ok": False, "errors": e.errors}
    except CoreError as e:
        sys.exit(str(e))
    return {"ok": True, "task_id": r.task_id, "task": r.task, "step": args.step, **shape(r)}


def cmd_done(args, today):
    return _mark("done", args, today, lambda r: {
        "status": r.status, "next_step": r.next_step_title,
        "date_assigned_to_step": r.dates_assigned[0] if r.dates_assigned else None,
        "dates_assigned": r.dates_assigned, "task_status": r.task_status})


def _reason_error(reason):
    return core_mark.reason_error(_ctx(), reason)


def cmd_notdone(args, today):
    return _mark("notdone", args, today, lambda r: {
        "status": r.status, "next_check": r.next_check, "stalled": r.stalled,
        "hint": r.hint})


def _defer_step(task, step, today, to, reason, event="defer", expected_step=None):
    """Общая механика переноса — `core.mark.defer_step`; зовёт массовый перенос
    из разбора завала (`cmd_backlog_bulk`)."""
    core_mark.defer_step(_ctx(), task, step, today, to, reason, event=event,
                         expected_step=expected_step)


def cmd_defer(args, today):
    return _mark("defer", args, today, lambda r: {"next_check": r.next_check})


def cmd_fail(args, today):
    return _mark("fail", args, today, lambda r: {
        "status": r.status, "next_step": r.next_step_id, "task_status": r.task_status})


def cmd_skip(args, today):
    return _mark("skip", args, today, lambda r: {
        "status": r.status, "task_status": r.task_status})


def cmd_undo(args, today):
    """Отменить последнее сегодняшнее действие над шагом — `core.mark.undo`,
    обоснования там."""
    task_id = find_task_id(args.task)
    try:
        r = core_mark.undo(_ctx(), task_id, args.step,
                           today=today, now=_now(args, today), work=_work(args))
    except ValidationError as e:
        return {"ok": False, "errors": e.errors}
    except CoreError as e:
        sys.exit(str(e))
    return {"ok": True, "task_id": r.task_id, "task": r.task, "step": args.step,
            "undone": r.undone, "status": r.status, "control_date": r.control_date,
            "stalled": r.stalled, "task_status": r.task_status}


def cmd_backlog_bulk(args, today):
    """Массовые действия из разбора завала — вкладка «Списком», R20 ТЗ.

    Три операции над списком `{task, step}`: перенос всей пачки на одну дату
    с одной причиной, массовое «сделано», массовое «не будет сделано». Каждый
    элемент проходит ту же механику, что и одиночные `defer`/`done`/`fail` —
    здесь ничего не дублируется, элементы `done`/`fail` зовут сами эти команды,
    перенос зовёт общий с `cmd_defer` `_defer_step`.

    Один плохой элемент не роняет пачку: стор правится руками и другой
    процесс мог закрыть тот же шаг за это время, поэтому каждый элемент — свой
    try/except, а не общий. По каждому отдаётся успех или структурная ошибка
    {field, error} — то же правило, что и у одиночных операций (CONTRACT.md).
    Повтор пачки после обрыва связи не портит уже закрытые шаги: они просто
    попадут в ответе как ошибка «уже done/failed», а не продублируют запись —
    та же идемпотентность, что у одиночных команд.

    Причина обязательна для переноса и «не будет сделано», как и у одиночных
    `defer`/`fail`; для «сделано» — нет, как и у одиночного `done`. Дата для
    переноса приходит уже разобранной (ISO) — человеческий ввод вроде «+3»
    разбирает `parse_date_input`, а не эта команда, и не оболочка.
    """
    op = args.op
    items = args.items or []
    if isinstance(items, str):
        # CLI отдаёт JSON-строку (или "-" для stdin), сервер — уже готовый
        # список: тот же приём, что у `cmd_create` с телом задачи.
        сырое = sys.stdin.read() if items == "-" else items
        try:
            items = json.loads(сырое)
        except json.JSONDecodeError as e:
            return {"ok": False, "errors": [{"field": "items", "error": f"битый JSON: {e}"}]}
    reason = (args.reason or "").strip() or None
    to = parse_stored_control(args.to) if getattr(args, "to", None) else None

    if op not in ("defer", "done", "fail"):
        return {"ok": False, "errors": [{"field": "op", "error": f"неизвестное действие: {op}"}]}
    if op in ("defer", "fail") and not reason:
        return {"ok": False, "errors": [{"field": "reason", "error": "причина обязательна"}]}
    ошибка = _reason_error(reason)
    if ошибка:
        return {"ok": False, "errors": [ошибка]}
    if op == "defer" and to is None:
        return {"ok": False, "errors": [{"field": "to", "error": "нужна новая дата"}]}

    results = []
    for позиция in items:
        позиция = позиция or {}
        имя_задачи = позиция.get("task")
        id_шага = позиция.get("step")
        строка_шага = str(id_шага) if id_шага not in (None, "") else None
        try:
            if not имя_задачи or id_шага in (None, ""):
                raise ValueError("не указан шаг")
            if op == "defer":
                task = find_task(имя_задачи)
                step = get_step(task, id_шага)
                if step.get("status") != OPEN:
                    raise ValueError(f"шаг {id_шага} уже {step.get('status')}")
                expected_step = (step["id"], _step_snapshot(step))
                try:
                    _defer_step(task, step, today, to, reason, event="mass_defer",
                                expected_step=expected_step)
                except store.StepConflict as e:
                    raise ValueError(f"шаг {id_шага} уже {e.actual_status}") from e
                результат = {"ok": True, "task": task["path"].stem, "step": строка_шага,
                            "next_check": tpl.control_text(to), "stalled": stall_count(step)}
            elif op == "done":
                результат = cmd_done(
                    SimpleNamespace(task=имя_задачи, step=строка_шага, reason=reason), today)
            else:  # fail
                результат = cmd_fail(
                    SimpleNamespace(task=имя_задачи, step=строка_шага, reason=reason), today)
            results.append(результат)
        except (SystemExit, ValueError) as e:
            results.append({"ok": False, "task": имя_задачи, "step": строка_шага,
                            "errors": [{"field": None, "error": str(e)}]})

    return {"op": op, "count": len(results),
            "ok_count": sum(1 for r in results if r["ok"]),
            "fail_count": sum(1 for r in results if not r["ok"]),
            "items": results}


# --- база знаний -------------------------------------------------------

def _read_json_arg(value):
    """JSON: строкой, уже разобранным значением (так приходит из HTTP — сервер
    парсит тело запроса раньше) или «-» для чтения из stdin, как у `create`.
    Одна команда обслуживает и форму, и командную строку, и вход у них разный."""
    if isinstance(value, str):
        if value == "-":
            value = sys.stdin.read()
        return json.loads(value)
    return value


def _kb_stores():
    """Склады ссылок и отказов: БД, если база знаний туда переехала, иначе файлы.

    Признак тот же, что у `load_kb_entries`, — есть ли записи в `kb_notes`.
    Держать его в одном месте обязательно: разъехавшись, чтение записей и
    чтение ссылок к ним начнут смотреть в разные хранилища, и подтверждённое
    подчёркивание перестанет находиться.
    """
    if get_store().load_kb_notes():
        return kb.SqliteLinkStore(VAULT), kb.SqliteExclusionStore(VAULT)
    return kb.JsonLinkStore(VAULT), kb.JsonExclusionStore(VAULT)


def migrate_kb_to_db(today=None):
    """Перенести базу знаний из markdown в таблицы. Этап (b) плана.

    Что переносится: `База/*.md` → `kb_notes`, `Ссылки.json` → `kb_links`,
    `Исключения.json` → `kb_exclusions`. Файлы после переноса остаются на диске
    нетронутыми: это данные заказчика, и удалять их за него мы не будем — но
    источником правды они быть перестают (см. `load_kb_entries`).

    Главная работа тут не в переливании, а в **смене идентификатора**. В
    markdown записью правило имя файла, то есть строка «Василий Говнов», и
    ссылки в `Ссылки.json` ссылаются ею. В базе id числовой. Поэтому запись
    сохраняет своё прежнее имя в `legacy_file`, а ссылки перецепляются по нему.
    Ссылка на запись, которой в `База/` уже нет (файл удалили руками, а ссылка
    осталась), переносу не подлежит: в базе внешний ключ, и висячая строка туда
    просто не ляжет. Такие считаются и возвращаются числом, а не выбрасываются
    молча — заказчику стоит знать, что часть подчёркиваний исчезнет.

    Идемпотентно: если в `kb_notes` уже что-то есть, второй прогон ничего не
    делает. Повторный запуск после обрыва не должен заводить вторые копии.
    """
    склад = get_store()
    if склад.load_kb_notes():
        return {"ok": True, "skipped": "база знаний уже в БД",
                "notes": 0, "links": 0, "exclusions": 0, "dropped_links": 0}

    записи = load_kb_entries()   # здесь ещё markdown: таблица пуста
    имя_к_id = {}
    for з in записи:
        тело = ""
        путь = KB_DIR / f"{з['id']}.md"
        if путь.is_file():
            try:
                _, тело = parse_file(путь)
            except Exception:
                тело = ""
        имя_к_id[str(з["id"])] = склад.add_kb_note(
            з["title"], з.get("aliases") or [], тело, legacy_file=str(з["id"]))

    старые_ссылки = kb.JsonLinkStore(VAULT).all() if (VAULT / "Ссылки.json").is_file() else []
    новые, потеряно = [], 0
    for с in старые_ссылки:
        новый_id = имя_к_id.get(str(с.get("kb_entry_id")))
        if новый_id is None:
            потеряно += 1
            continue
        новые.append({**с, "kb_entry_id": новый_id})
    склад.save_kb_links(новые)

    старые_отказы = (kb.JsonExclusionStore(VAULT).keys()
                     if (VAULT / "Исключения.json").is_file() else set())
    отказы = []
    for запись, написание in старые_отказы:
        # None в первом поле — «слово никогда не ссылка, у любой записи»: такой
        # отказ не привязан к записи и переезжает как есть.
        отказы.append({"kb_entry_id": имя_к_id.get(str(запись)) if запись is not None else None,
                       "text": написание})
    склад.save_kb_exclusions([о for о in отказы
                              if о["kb_entry_id"] is not None or о["text"]])

    return {"ok": True, "notes": len(имя_к_id), "links": len(новые),
            "exclusions": len(отказы), "dropped_links": потеряно}


def cmd_migrate_kb(args, today):
    """Команда для этапа (b). Отдельная и запускаемая руками, а не при старте:
    миграция трогает данные, и делать это молча в фоне нельзя."""
    return migrate_kb_to_db(today)


_search_text_of_task = core_persist.search_text_of_task


def reindex_search(store_=None):
    """Собрать поисковый индекс заново по всему стору и починить справочник
    тегов. Требования R24, R25.

    Полная пересборка, а не досборка: она нужна после переезда, после смены
    правил лемматизации и как способ починить индекс, если он разошёлся с
    данными. На целевом объёме ТЗ это секунды, а разошедшийся индекс чинится
    иначе только руками.

    Дальше индекс поддерживается по одной задаче в `save()` — там своя строка
    переписывается, а не пересобирается всё.
    """
    склад = store_ or get_store()
    склад.search_clear()
    задач = 0
    for task in load_tasks():
        склад.search_replace(
            "task", task["path"].stem, task["path"].stem,
            (task.get("body") or "").strip()[:200],
            kb.lemmatize_text(_search_text_of_task(task)))
        # Справочник тегов — сюда же: при обычной работе его пополняет save()
        # на каждой записи, но задача, попавшая в базу мимо движка (миграция),
        # своих тегов в справочник не донесла. Пачкой это делал refresh, пока
        # писал весь стор; теперь чинит reindex, а не пятиминутный будильник.
        _sync_tags_to_catalog(task["meta"].get("tags") or [])
        задач += 1
    записей = 0
    for з in склад.load_kb_notes():
        склад.search_replace(
            "kb_note", з["id"], з["title"], (з.get("body") or "").strip()[:200],
            kb.lemmatize_text(" ".join([з["title"], *(з.get("aliases") or []),
                                        з.get("body") or ""])))
        записей += 1
    return {"ok": True, "tasks": задач, "kb_notes": записей}


def cmd_reindex(args, today):
    return reindex_search()


def search_query(text):
    """Человеческий запрос → выражение FTS5.

    Слова приводятся к тем же леммам, что и текст в индексе: иначе «гранту» в
    поиске не нашло бы «грант» в задаче, ради чего лемматизация и заводилась.
    Слова соединяются через AND — человек, набравший два слова, ищет то, где
    есть оба, а не то, где есть хоть одно.

    Каждое слово берётся в кавычки: в запрос попадают знаки, которые FTS5
    считает синтаксисом (дефис в «финмодель-2026», звёздочка, скобки), и без
    кавычек он на них ругается или понимает их не так, как человек имел в виду.
    """
    слова = [w for w, _, _ in kb.tokenize(text or "")]
    if not слова:
        return ""
    return " AND ".join(f'"{kb.lemma(w)}"' for w in слова)


def cmd_search(args, today):
    """Поиск по истории — R24, главный ответ на «как я это делал в прошлый раз».

    Ищет по задачам, их заметкам, названиям шагов и записям базы знаний.
    """
    запрос = search_query(getattr(args, "text", None))
    if not запрос:
        return {"ok": False, "errors": [{"field": "text", "error": "Пустой запрос"}]}
    виды = None
    if getattr(args, "kind", None):
        виды = [args.kind]
    найдено = get_store().search(запрос, limit=int(getattr(args, "limit", None) or 50),
                                 source_types=виды)
    return {"ok": True, "query": запрос, "count": len(найдено), "results": найдено}


def _kb_settings():
    """Настройки автораспознавания. Тот же приём, что `_work`/`_backup_settings`:
    файл — база, битый файл не роняет сканирование."""
    try:
        сохранённые = cfg.load(cfg.settings_path(VAULT))["kb"]
    except cfg.SettingsError:
        сохранённые = cfg.defaults()["kb"]
    return сохранённые


def cmd_kb_scan(args, today):
    """Гипотезы упоминаний записей базы знаний в тексте — R17, разделы 5.7/5.8 ТЗ.

    Индекс собирается заново на каждый вызов, не кэшируется: заказчик правит
    кэш без инвалидации на эту правку не среагирует.

    `kb.auto_recognition` выключает поиск новых совпадений целиком — заказчик
    решил, что подчёркивания мешают, а не сканирование сломано. Уже
    подтверждённые ссылки при этом продолжают показываться: это не гипотезы,
    а факт, который заказчик когда-то подтвердил сам, и выключенный флаг не
    должен стирать историю. `kb.min_match_length` идёт в `build_index` вместо
    зашитой в `kb.py` константы — раньше это поле лежало в файле настроек,
    а искало ровно четыре буквы, что бы там ни было записано.
    """
    настройки = _kb_settings()
    source_type = getattr(args, "source_type", None)
    source_id = getattr(args, "source_id", None)
    _ссылки_склад, _отказы_склад = _kb_stores()
    подтверждённые = (_ссылки_склад.for_source(source_type, source_id)
                      if source_type and source_id else [])

    text = getattr(args, "text", None) or ""
    entries = load_kb_entries()
    if not text or not entries or not настройки.get("auto_recognition", True):
        return {"hypotheses": [], "confirmed": подтверждённые, "kb_broken": list(KB_BROKEN)}

    исключения = _отказы_склад.keys()
    индекс = kb.build_index(entries, min_match=настройки.get("min_match_length") or kb.MIN_MATCH)
    гипотезы = kb.find_mentions(text, индекс, excluded=исключения)

    if подтверждённые:
        # Уже отвеченное не переспрашиваем: смещения подтверждённых ссылок
        # исключаются из новых гипотез по тому же месту в тексте.
        занято = {(с["offset_start"], с["offset_end"]) for с in подтверждённые}
        гипотезы = [г for г in гипотезы
                    if (г["offset_start"], г["offset_end"]) not in занято]

    return {"hypotheses": гипотезы, "confirmed": подтверждённые,
            "kb_broken": list(KB_BROKEN)}


def _find_task_by_stem(name):
    """Точное название задачи, без нечёткого поиска `find_task`: гипотезы уже
    посчитаны на конкретной, уже созданной задаче — мазать мимо здесь нельзя.

    Точное совпадение уходит в SQL по уникальному индексу названия, а не
    перебором всего стора: этой функцией ходят все команды, которым дали имя
    задачи, включая отметки шагов.
    """
    склад = get_store()
    task_id = склад.find_task_id(name)
    return склад.task_by_id(task_id) if task_id is not None else None


# Срез блока шагов переехал в `domain.steps_plan.strip_steps_block`; имя
# сохранено для тестов и `_mark_links_in_body`.
_strip_steps_block = steps_plan.strip_steps_block


def _mark_links_in_body(task, mentions):
    """Вписать подтверждённые упоминания в тело настоящими вики-ссылками.

    Единственное место, где текст заказчика правит программа, — и делает это
    ровно потому, что человек сам нажал «да» на конкретное упоминание (раздел
    7.1 ТЗ). Пишет `[[Название]]`, а если написано не так, как называется
    запись — piped link `[[Название|как написано]]`.

    Гипотезы обрабатываются по убыванию offset_start: иначе первая же вставка
    сдвинет смещения соседних. Та, под которой текст успел измениться
    (`text[s:e] != matched`), пропускается молча — ссылка в Ссылки.json уже
    записана вызывающим, здесь только подчёркивание в тексте.
    """
    body, вернуть_блок = _strip_steps_block(task["body"])
    for гипотеза in sorted(mentions, key=lambda г: -г["offset_start"]):
        s, e = гипотеза["offset_start"], гипотеза["offset_end"]
        if body[s:e] != гипотеза["matched"]:
            continue
        title, matched = гипотеза["title"], гипотеза["matched"]
        link = f"[[{title}]]" if matched == title else f"[[{title}|{matched}]]"
        body = body[:s] + link + body[e:]
    task["body"] = вернуть_блок(body)


def cmd_kb_confirm(args, today):
    """Подтверждение гипотез автораспознавания — R17, раздел 7.1 ТЗ.

    Каждая гипотеза обрабатывается независимо: одна кривая не блокирует
    соседние. Ошибки собираются с индексом гипотезы в поле (`mentions.0` и
    т. п.), `ok` — False только если упали все.
    """
    source_type = getattr(args, "source_type", None)
    source_id = getattr(args, "source_id", None)
    try:
        mentions = _read_json_arg(args.mentions) or []
    except json.JSONDecodeError as e:
        return {"ok": False, "links": [],
                "errors": [{"field": "mentions", "error": f"битый JSON: {e}"}]}

    склад, _ = _kb_stores()
    успешные, ошибки = [], []
    for i, гипотеза in enumerate(mentions):
        try:
            ссылка = склад.add(гипотеза, source_type=source_type, source_id=source_id)
        except kb.KbError as e:
            for err in e.errors:
                поле = f"mentions.{i}.{err['field']}" if err.get("field") else f"mentions.{i}"
                ошибки.append({"field": поле, "error": err["error"]})
            continue
        успешные.append((гипотеза, ссылка))

    if успешные and source_type == "task":
        задача = _find_task_by_stem(source_id)
        if задача is not None:
            _mark_links_in_body(задача, [г for г, _ in успешные])
            save(задача, today)

    ok = bool(успешные) or not ошибки
    return {"ok": ok, "links": [с for _, с in успешные], "errors": ошибки}


def cmd_kb_reject(args, today):
    """Ответ «нет» на гипотезу — раздел 7.1 ТЗ.

    `mute=True` — слово никогда не считается ссылкой ни у одной записи
    («Грант» у заказчика чаще сумма денег, чем запись базы). `mute=False` —
    отказ только от этой конкретной гипотезы у этой записи БАЗЫ ЗНАНИЙ: ключ
    исключения — пара (запись, написание), источник (задача, из которой пришёл
    ответ) в него не входит, и отказ, поставленный на одной задаче, гасит
    гипотезу и на всех остальных.
    """
    try:
        гипотеза = _read_json_arg(args.mention)
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [{"field": "mention", "error": f"битый JSON: {e}"}]}

    _, склад = _kb_stores()
    if getattr(args, "mute", False):
        склад.mute_word(гипотеза["matched"])
    else:
        склад.reject(гипотеза)
    return {"ok": True}


def cmd_kb_exclusions(args, today):
    """Список отказов автораспознавания — раздел 7.1 ТЗ, отмена «нет»/«заглушить».

    Без списка отказ, поставленный по ошибке, не видно: `--mute` гасит слово у
    всех записей разом (`kb.word_key`), и один случайный клик молча снимает
    слово с распознавания навсегда. Возвращает записи в том же виде, в каком
    их принимает `cmd_kb_forget` (эхо-пара `kb_entry_id`/`text`), плюс
    `scope` («word» для отказа по слову целиком, «entry» для отказа по
    конкретной записи) и `title` записи, если она ещё существует в базе —
    без заголовка список выглядел бы набором нечитаемых написаний.
    """
    _, склад = _kb_stores()
    записи = {str(з["id"]): з for з in load_kb_entries()}
    исключения = []
    for entry_id, написание in склад.keys():
        запись = записи.get(str(entry_id)) if entry_id is not None else None
        исключения.append({
            "kb_entry_id": entry_id,
            "text": написание,
            "scope": "word" if entry_id is None else "entry",
            "title": запись["title"] if запись else None,
        })
    исключения.sort(key=lambda и: (и["scope"], и["title"] or "", и["text"]))
    return {"ok": True, "exclusions": исключения}


def cmd_kb_forget(args, today):
    """Отмена отказа — «передумал», раздел 7.1 ТЗ. Без неё `--mute` необратим.

    `key` — та же пара `kb_entry_id`/`text`, что вернул `cmd_kb_exclusions`
    (или сама отклонённая гипотеза с теми же полями): эхо-приём, как у
    `kb-confirm` с гипотезами `kb-scan`, а не отдельный набор аргументов —
    типы `kb_entry_id` разные у файлового и БД-склада (имя записи и число), и
    гадать его на командной строке ненадёжно.
    """
    try:
        ключ = _read_json_arg(args.key)
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [{"field": "key", "error": f"битый JSON: {e}"}]}

    _, склад = _kb_stores()
    снято = склад.forget((ключ.get("kb_entry_id"), ключ.get("text")))
    return {"ok": True, "removed": снято}


# --- записи базы знаний ----------------------------------------------------
#
# Идентификатор записи здесь числовой: после этапа (b) записи живут в таблице
# `kb_notes`, а не файлами `База/*.md`, где им служило именем имя файла. Оттого
# и никакого slug'а в этих командах нет — переименование записи больше не
# значит «написать новый файл и убрать старый», id при правке названия не
# меняется, и различать «под каким именем лежит» и «во что переименовываем»
# стало нечего.
#
# Markdown после переезда остаётся на диске нетронутым (данные заказчика), но
# источником правды быть перестаёт — см. `load_kb_entries`. Писать правку ещё и
# в файл значило бы завести второй источник, поэтому команды ниже трогают
# только базу. Пока таблица пуста, они честно показывают пусто: перенести
# markdown нужно один раз командой `migrate-kb`.


def _kb_note_id(value):
    """Числовой id записи из чего пришло: из HTTP приходит строкой, из CLI
    строкой, из тестов числом. None — если это вообще не число."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _kb_note_by_id(склад, value):
    note_id = _kb_note_id(value)
    if note_id is None:
        return None
    for з in склад.load_kb_notes():
        if з["id"] == note_id:
            return з
    return None


def _index_kb_note(склад, note):
    """Обновить поисковый индекс по одной записи — тем же выражением, каким
    `reindex_search` собирает сразу все. Причины те же, что у `_index_task`:
    пересобирать весь индекс на каждую правку записи дорого, а сбой
    лемматизации не должен ронять саму запись — индекс чинится командой
    `reindex`, запись ничем."""
    try:
        склад.search_replace(
            "kb_note", note["id"], note["title"],
            (note.get("body") or "").strip()[:200],
            kb.lemmatize_text(" ".join([note["title"], *(note.get("aliases") or []),
                                        note.get("body") or ""])))
    except Exception:
        pass


def _kb_note_clean(data, было=None):
    """Поля записи из присланного JSON, поверх текущих значений.

    Правка частичная: поле, которого в JSON нет, остаётся как было. Отдельного
    способа сказать «поле удалено» у формы нет, поэтому «не прислано» и
    «очистить» не различить, и трактуем как первое — тот же выбор, что был у
    этих команд до переезда на БД.
    """
    было = было or {"title": "", "aliases": [], "body": ""}
    def брать(имя):
        return data.get(имя) if имя in data else было.get(имя)
    return {
        "title": (брать("title") or "").strip(),
        "aliases": [a.strip() for a in (брать("aliases") or []) if a and a.strip()],
        "body": (брать("body") or "").strip(),
    }


def _kb_note_errors(склад, поля, кроме=None):
    """Проверки записи. Дубль названия — ошибка, а не мелочь: автораспознавание
    строит индекс по названиям и синонимам (`kb.build_index`), и две записи с
    одинаковым названием дали бы на одно и то же слово два совпадения, между
    которыми заказчику нечем выбрать. `кроме` — id правимой записи: собственное
    название дублем себе не считается."""
    if not поля["title"]:
        return [{"field": "title", "error": "Название обязательно"}]
    занято = поля["title"].casefold()
    for з in склад.load_kb_notes():
        if з["id"] != кроме and (з["title"] or "").strip().casefold() == занято:
            return [{"field": "title", "error": "Запись с таким названием уже есть"}]
    return []


def cmd_kb_note_list(args, today):
    """Все записи базы знаний целиком — id, название, синонимы, тело.

    Страницы интерфейса читают этим, а не `load_kb_entries`: тому нужен только
    состав для поиска упоминаний, здесь — весь текст для списка и карточки."""
    return {"notes": get_store().load_kb_notes()}


def cmd_kb_note_show(args, today):
    """Одна запись по id — плоским словарём, каким её отдаёт склад."""
    note = _kb_note_by_id(get_store(), getattr(args, "id", None))
    if note is None:
        return {"ok": False,
                "errors": [{"field": "id",
                            "error": f"нет записи «{getattr(args, 'id', None)}»"}]}
    return note


def cmd_kb_note_create(args, today):
    """Завести запись базы знаний из JSON: {"title", "aliases", "body"}."""
    try:
        data = _read_json_arg(args.json)
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [{"field": None, "error": f"битый JSON: {e}"}]}

    склад = get_store()
    поля = _kb_note_clean(data)
    ошибки = _kb_note_errors(склад, поля)
    if ошибки:
        return {"ok": False, "errors": ошибки}

    note_id = склад.add_kb_note(поля["title"], поля["aliases"], поля["body"])
    _index_kb_note(склад, {**поля, "id": note_id})
    return {"ok": True, "note": note_id, "title": поля["title"]}


def cmd_kb_note_update(args, today):
    """Править запись: название, синонимы, тело. Запись ищется по `args.id`,
    и он же остаётся у неё после правки — переименование здесь обычная смена
    поля, а не смена идентификатора."""
    try:
        data = _read_json_arg(args.json)
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [{"field": None, "error": f"битый JSON: {e}"}]}

    склад = get_store()
    было = _kb_note_by_id(склад, getattr(args, "id", None))
    if было is None:
        return {"ok": False,
                "errors": [{"field": "id",
                            "error": f"нет записи «{getattr(args, 'id', None)}»"}]}

    поля = _kb_note_clean(data, было)
    ошибки = _kb_note_errors(склад, поля, кроме=было["id"])
    if ошибки:
        return {"ok": False, "errors": ошибки}

    склад.update_kb_note(было["id"], поля["title"], поля["aliases"], поля["body"])
    _index_kb_note(склад, {**поля, "id": было["id"]})
    return {"ok": True, "note": было["id"], "title": поля["title"]}


def cmd_kb_note_delete(args, today):
    """Удалить запись насовсем. Подтверждение — дело интерфейса, не движка, как
    и у `cmd_delete` для задач.

    Вместе с записью уходят её подтверждённые ссылки и отказы: у обеих таблиц
    ON DELETE CASCADE (см. `store.delete_kb_note`). Текст `[[Название]]`,
    который движок когда-то вписал в тело задачи, при этом остаётся — он часть
    заметки заказчика, и вычищать её за него мы не будем."""
    склад = get_store()
    note = _kb_note_by_id(склад, getattr(args, "id", None))
    if note is None:
        return {"ok": False,
                "errors": [{"field": "id",
                            "error": f"нет записи «{getattr(args, 'id', None)}»"}]}
    склад.delete_kb_note(note["id"])
    try:
        склад.search_forget("kb_note", note["id"])
    except Exception:
        pass
    return {"ok": True, "note": note["id"], "deleted": True}

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
    ("Задача", 34), ("Шаг", 6), ("Название", 40), ("Вид", 10), ("В группе", 30),
    ("Статус", 10), ("Контроль", 12), ("Выполнен", 12), ("Не сделан, раз", 15),
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
    """Весь стор в один .xlsx.

    Заказчику это не аналитика, а страховка «чтобы не проебалось»: файл уезжает ему
    в Telegram и лежит отдельно от стора. Отсюда третий лист — плоский разворот
    `log` шагов. Сводку и статусы можно пересчитать из шагов заново, а переносы,
    причины и старые даты больше взять неоткуда: пропадут вместе с папкой.

    Только чтение: стор после экспорта побайтово тот же.
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
        по_id = {s["id"]: s for s in all_steps}
        листья = [s for s in all_steps if not is_group(s)]
        closed = sum(1 for s in листья if s.get("status") in (DONE, SKIPPED))
        tags = meta.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tasks.append([
            name, meta.get("title"), as_date(meta.get("created")), STATUS_RU[status],
            step.get("title") if step else None,
            as_date(step.get("control_date")) if step else None,
            f"{closed}/{len(листья)}" if листья else None,
            stall_count(step) if step else 0,
            ", ".join(str(t) for t in tags), task["body"].strip(),
        ])

        for s in all_steps:
            step_title = s.get("title")
            родитель = по_id.get(s.get("parent"))
            steps.append([
                name, s.get("id"), step_title,
                ("группа ∥" if s.get("mode") == "par" else
                 "группа →" if s.get("mode") == "seq" else None),
                родитель.get("title") if родитель else None,
                (None if is_group(s) else
                 STEP_STATUS_RU.get(s.get("status", OPEN), s.get("status"))),
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


# --- резервные копии и экспорт (R25, R11 — раздел 9 ТЗ) --------------------
#
# Копия (R25) и экспорт (R11) — разные задачи, backup.py разводит их по разным
# функциям. Здесь только тонкая обвязка: разбор аргументов, дефолтный путь и
# перевод backup.BackupError в структурную ошибку контракта {field, error}.

def default_backup_dir():
    """Куда класть копии по умолчанию — рядом с стором, не внутри него.

    Если пропадёт папка стора целиком (отвалившийся диск, случайное
    удаление), копия обязана остаться цела. Папку заранее не создаём:
    `backup.create` заводит её сама при первом снятии копии.
    """
    return VAULT.parent / f"{VAULT.name} — копии"


def _backup_settings():
    """Папка, частота и число копий из настроек стора.

    Читается тем же приёмом, что рабочие часы в `_work`: файл — база, битый
    файл не роняет операцию. Раньше эти три ключа лежали в `Настройки.json`
    мёртвыми: форма их не показывала, а `cmd_backup` брал папку рядом с стором
    и `backup.KEEP_DEFAULT`, что бы в них ни стояло.
    """
    try:
        сохранённые = cfg.load(cfg.settings_path(VAULT))["backup"]
    except cfg.SettingsError:
        сохранённые = cfg.defaults()["backup"]
    return сохранённые


def cmd_backup(args, today):
    """Снять резервную копию стора. Раздел 9 ТЗ, требование R25."""
    настройки = _backup_settings()
    dest = Path(args.dest) if getattr(args, "dest", None) else (
        Path(настройки["folder"]) if настройки.get("folder") else default_backup_dir())
    keep = getattr(args, "keep", None) or настройки.get("keep_count") or backup.KEEP_DEFAULT
    try:
        итог = backup.backup(VAULT, dest, keep=keep, force=bool(getattr(args, "force", False)))
    except backup.BackupError as e:
        return {"ok": False, "errors": [e.as_json()]}
    except OSError as e:
        # Диск полон, папка недоступна — не наша ошибка с полем, но и молчать
        # нельзя: заказчик должен узнать, что копия не снялась.
        return {"ok": False, "errors": [{"field": None, "error": str(e)}]}
    return {"ok": True, **итог}


def cmd_backup_list(args, today):
    """Список копий для интерфейса восстановления. Пустого списка не боимся —
    первой копии могло ещё не быть, это не ошибка."""
    dest = Path(args.dest) if getattr(args, "dest", None) else default_backup_dir()
    return {"copies": backup.copies(dest), "dest": str(dest)}


def cmd_backup_restore(args, today):
    """Восстановить стор из конкретной копии. Дефолта на «последнюю копию»
    нет намеренно: это деструктивная операция, файл выбирает человек.

    `backup.restore` сама снимает страховочную копию текущего состояния перед
    перезаписью — вызывающему снимать её отдельно не нужно.
    """
    try:
        итог = backup.restore(args.file, VAULT)
    except backup.BackupError as e:
        return {"ok": False, "errors": [e.as_json()]}
    return {"ok": True, **итог}


def cmd_export_json(args, today):
    """Выгрузка всей базы в JSON. Раздел 9 ТЗ, требование R11.

    Не Excel-выгрузка (`cmd_export`): та для «посмотреть глазами и переслать»,
    эта — данные без нашего формата хранения, для будущей версии продукта.

    Путь по умолчанию — рядом с копиями, не в самом сторе: та же логика, что
    у `default_backup_dir` — файл не должен пропасть вместе с папкой стора.
    """
    out = (Path(args.to) if getattr(args, "to", None)
           else default_backup_dir() / f"выгрузка-{today.isoformat()}.json")
    try:
        итог = backup.write_export(VAULT, out)
    except backup.BackupError as e:
        return {"ok": False, "errors": [e.as_json()]}
    return {"ok": True, **итог}


# --- вложения ----------------------------------------------------------

def _attachment_owner(args):
    """Владелец для `core.attachments` из `task`/`--step` или `--template`.
    Адаптер «кусок названия → `TaskOwner`»: задача в CLI ищется подстрокой
    (`find_task_id`), в ядре она уже адресуется `task_id`. Существование
    задачи, шага и шаблона проверяет ядро (`owner_key`), здесь только форма.

    Нечисловой `--step` ядру не передать: `TaskOwner.step_id` — int. Ответ
    прежний, «нет шага X в «Название»», для чего название читается отдельно.
    """
    имя_шаблона = getattr(args, "template", None)
    if имя_шаблона not in (None, ""):
        return core_attachments.TemplateOwner(имя_шаблона)
    if getattr(args, "task", None) in (None, ""):
        sys.exit("нужно название задачи или --template")
    task_id = find_task_id(args.task)
    шаг = getattr(args, "step", None)
    if шаг in (None, ""):
        return core_attachments.TaskOwner(task_id)
    try:
        step_id = int(шаг)
    except (TypeError, ValueError):
        task = get_store().task_by_id(task_id)
        sys.exit(f"нет шага {шаг} в «{task['path'].stem}»")
    return core_attachments.TaskOwner(task_id, step_id)


def cmd_attach(args, today):
    """Прикрепить файл к задаче, шагу (`--step`) или шаблону (`--template`).
    Один путь для CLI и формы: данные приходят либо уже готовыми байтами
    (`args.data`), либо путём к файлу на диске (CLI — `args.file`) — тот же
    приём, что у `cmd_create` с JSON-строкой или `-` для чтения из stdin.

    Форма ответа прежняя: `NotFound` ядра (задача, шаг, шаблон) отдаётся с
    полем `template` или `task` — так пиннит `test_attach_к_несуществующему_шагу`.
    """
    поле = "template" if getattr(args, "template", None) else "task"
    try:
        owner = _attachment_owner(args)
    except SystemExit as e:
        return {"ok": False, "errors": [{"field": поле, "error": str(e)}]}

    filename = (getattr(args, "filename", None) or "").strip()
    if not filename and getattr(args, "file", None):
        filename = Path(args.file).name

    if getattr(args, "data", None) is not None:
        data = args.data
    else:
        try:
            data = Path(args.file).read_bytes()
        except OSError as e:
            return {"ok": False, "errors": [{"field": "file", "error": str(e)}]}

    try:
        info = core_attachments.add(_ctx(), owner, data, filename,
                                    getattr(args, "caption", None), today)
    except ValidationError as e:
        return {"ok": False, "errors": e.errors}
    except CoreError as e:
        return {"ok": False, "errors": [{"field": поле, "error": str(e)}]}
    # `sha256` в ответе был всегда; модель ядра его не несёт (клиенту он не
    # нужен), поэтому для прежней формы он дочитывается из строки.
    row = get_store().get_attachment(info.id)
    return {"ok": True, "id": info.id, "sha256": row["sha256"], "filename": info.filename,
            "mime": info.mime, "bytes": info.bytes}


def cmd_attachments(args, today):
    """Список вложений задачи, одного шага (`--step`) или шаблона.

    Для задачи без `--step` — только её собственные файлы, как и раньше:
    ядро (`list_for`) отдаёт задачу вместе с шагами одним списком (Р6), а
    CLI-форма это разделение держит (`test_attach_к_шагу`).
    """
    owner = _attachment_owner(args)
    try:
        rows = core_attachments.list_for(_ctx(), owner).attachments
    except CoreError as e:
        sys.exit(str(e))
    if isinstance(owner, core_attachments.TaskOwner) and owner.step_id is None:
        rows = [a for a in rows if a.step_id is None]
    return {"attachments": [
        {"id": a.id, "filename": a.filename, "mime": a.mime,
         "bytes": a.bytes, "caption": a.caption, "added": str(a.added)}
        for a in rows]}


def cmd_attachment_delete(args, today):
    """Удалить вложение. Не задачу и не шаг — на связь между ними это никак
    не влияет, только на список вложений."""
    try:
        r = core_attachments.remove(_ctx(), args.id)
    except CoreError as e:
        sys.exit(str(e))
    return {"ok": True, "id": r.id}


def rename_tag_everywhere(old_name, new_name, today=None):
    """Заменить тег во всех задачах стора. Дополняет `settings.rename_tag`
    и `settings.merge_tags`: те трогают только справочник (цвет, закрепление),
    а сами задачи settings.py не видит — про хранилище знает только движок.

    Строки задач эта функция больше не переписывает: `tags`/`task_tags` в БД
    хранят тег через id, а не строкой на самой задаче, так что и переименование,
    и слияние — операции только над справочником `tags`, ни одной задачи не
    касаются. См. `store.Store.rename_tag_everywhere`.
    """
    return get_store().rename_tag_everywhere(old_name, new_name)


def main():
    p = argparse.ArgumentParser(description="Движок шагов Yungdrung")
    p.add_argument("--today", help="подменить сегодняшнюю дату (для проверок)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("next", help="что требует внимания").set_defaults(func=cmd_next)
    sub.add_parser("feed", help="лента «Что сегодня»").set_defaults(func=cmd_feed)
    sub.add_parser("backlog", help="всё просроченное — разбор завала").set_defaults(
        func=cmd_backlog)

    bb = sub.add_parser("backlog-bulk",
                        help="массовые действия из разбора завала, вкладка «Списком» (R20)")
    bb.add_argument("op", choices=["defer", "done", "fail"])
    bb.add_argument("items", help='JSON-список [{"task":"...","step":1}, ...] '
                                  'или "-" для чтения из stdin')
    bb.add_argument("--reason", help="одна причина на всю пачку; обязательна для defer/fail")
    bb.add_argument("--to", help="новая дата для defer, формат 2026-08-24 или "
                                 "2026-08-24 15:00 — уже разобранная, не «+3»")
    bb.set_defaults(func=cmd_backlog_bulk)
    ls = sub.add_parser("list", help="все задачи")
    ls.add_argument("--status", help="только с этим статусом: overdue/due/waiting/"
                                      "no_date/done/cancelled/empty")
    ls.set_defaults(func=cmd_list)
    r = sub.add_parser("refresh", help="пересчитать сводку во всех задачах (перед сборкой)")
    r.add_argument("--force", action="store_true",
                   help="переписать все файлы, даже если сводка не изменилась — "
                        "нужно после смены схемы, чтобы привести стор к новому виду")
    r.set_defaults(func=cmd_refresh)

    t = sub.add_parser("templates", help="список шаблонов с предпросмотром")
    t.add_argument("--start", help="от какой даты считать предпросмотр")
    t.set_defaults(func=cmd_templates)

    sr = sub.add_parser("set-recurrence", help="прикрепить или снять повторение у шаблона")
    sr.add_argument("name")
    sr.add_argument("--rule", help='JSON правила с anchor, например '
                                   '{"anchor":"2026-09-01","freq":"monthly","bymonthday":[5]}')
    sr.add_argument("--clear", action="store_true", help="снять повторение")
    sr.set_defaults(func=cmd_set_recurrence)

    st = sub.add_parser("save-template", help="создать шаблон с нуля или переписать существующий")
    st.add_argument("json", help='JSON шаблона или "-" для чтения из stdin, форма как у cmd_create')
    st.set_defaults(func=cmd_save_template)

    td = sub.add_parser("template-delete", help="удалить шаблон насовсем")
    td.add_argument("name")
    td.set_defaults(func=cmd_template_delete)

    tp = sub.add_parser("template-preview",
                        help="какие даты дадут шаги ещё не сохранённого шаблона")
    tp.add_argument("json", help='JSON шаблона или "-" для чтения из stdin')
    tp.add_argument("--start", help="дата старта для примера, по умолчанию сегодня")
    tp.set_defaults(func=cmd_template_preview)

    rp = sub.add_parser("parse-recurrence", help="разобрать «каждый вторник» в правило")
    rp.add_argument("text")
    rp.set_defaults(func=cmd_recurrence_parse)

    rc = sub.add_parser("recur", help="прогнать шаблоны с правилом повторения")
    rc.add_argument("--name", help="только один шаблон")
    rc.add_argument("--force", action="store_true",
                    help="создать очередной цикл, даже если предыдущий не закрыт "
                         "(только вместе с --name — кнопка про один конкретный цикл)")
    rc.add_argument("--limit", type=int, help="сколько решений максимум за прогон")
    rc.set_defaults(func=cmd_recur)

    ft = sub.add_parser("from-template", help="завести задачу из шаблона")
    ft.add_argument("name")
    ft.add_argument("--start", help="дата старта, по умолчанию сегодня")
    ft.add_argument("--title", help="имя задачи, по умолчанию имя шаблона")
    ft.set_defaults(func=cmd_from_template)

    tt = sub.add_parser("template-from-task", help="сделать шаблон из задачи")
    tt.add_argument("task")
    tt.add_argument("--name", help="имя шаблона, по умолчанию имя задачи")
    tt.set_defaults(func=cmd_template_from_task)

    c = sub.add_parser("create", help="завести задачу из JSON (её же зовёт форма)")
    c.add_argument("json", help='JSON или "-" для stdin')
    c.set_defaults(func=cmd_create)

    u = sub.add_parser("update", help="править задачу из JSON — карточка, не статусы шагов")
    u.add_argument("task")
    u.add_argument("json", help='JSON или "-" для stdin')
    u.add_argument("--force", action="store_true",
                   help="разрешить пропажу шага из данных без явного «снять»")
    u.set_defaults(func=cmd_update)

    cn = sub.add_parser("cancel", help="отменить задачу целиком")
    cn.add_argument("task")
    cn.add_argument("--reason")
    cn.set_defaults(func=cmd_cancel)

    cl = sub.add_parser("close", help="закрыть задачу вручную, не проходя шаги по одному")
    cl.add_argument("task")
    cl.set_defaults(func=cmd_close)

    dl = sub.add_parser("delete", help="удалить задачу насовсем")
    dl.add_argument("task")
    dl.set_defaults(func=cmd_delete)

    at = sub.add_parser("attach", help="прикрепить файл к задаче, шагу или шаблону")
    at.add_argument("task", nargs="?", help="название задачи; либо --template")
    at.add_argument("file", help="путь к файлу на диске")
    at.add_argument("--step", help="id шага, если не вся задача")
    at.add_argument("--template", help="название шаблона вместо задачи")
    at.add_argument("--caption", help="короткая подпись")
    at.set_defaults(func=cmd_attach)

    al = sub.add_parser("attachments", help="список вложений задачи, шага или шаблона")
    al.add_argument("task", nargs="?", help="название задачи; либо --template")
    al.add_argument("--step", help="id шага, если не вся задача")
    al.add_argument("--template", help="название шаблона вместо задачи")
    al.set_defaults(func=cmd_attachments)

    ad = sub.add_parser("attachment-delete", help="удалить вложение по id")
    ad.add_argument("id", type=int)
    ad.set_defaults(func=cmd_attachment_delete)

    ro = sub.add_parser("reopen", help="отменить закрытие шага (сделан → снова открыт)")
    ro.add_argument("task")
    ro.add_argument("step")
    ro.set_defaults(func=cmd_reopen)

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

    un = sub.add_parser("undo", help="отменить последнее сегодняшнее действие над шагом")
    un.add_argument("task")
    un.add_argument("step")
    un.set_defaults(func=cmd_undo)

    k = sub.add_parser("skip", help="снять шаг")
    k.add_argument("task"); k.add_argument("step"); k.add_argument("--reason")
    k.set_defaults(func=cmd_skip)

    ar = sub.add_parser("archive", help="история: закрытые и отменённые задачи")
    ar.add_argument("--tag", help="только с этим тегом")
    ar.add_argument("--since", help="не раньше этой даты")
    ar.add_argument("--until", help="не позже этой даты")
    ar.set_defaults(func=cmd_archive)

    sr = sub.add_parser("search", help="поиск по задачам, заметкам и базе знаний")
    sr.add_argument("text")
    sr.add_argument("--kind", choices=["task", "kb_note"], help="только этот вид")
    sr.add_argument("--limit", type=int, default=50)
    sr.set_defaults(func=cmd_search)

    ri = sub.add_parser("reindex", help="собрать поисковый индекс заново")
    ri.set_defaults(func=cmd_reindex)

    mk = sub.add_parser("migrate-kb",
                        help="перенести базу знаний из База/*.md в таблицы (этап b)")
    mk.set_defaults(func=cmd_migrate_kb)

    ks = sub.add_parser("kb-scan", help="гипотезы упоминаний записей базы знаний в тексте")
    ks.add_argument("--text", default="")
    ks.add_argument("--source-type")
    ks.add_argument("--source-id")
    ks.set_defaults(func=cmd_kb_scan)

    kc = sub.add_parser("kb-confirm", help="подтвердить гипотезы, проставить ссылки в тексте")
    kc.add_argument("--source-type", required=True)
    kc.add_argument("--source-id", required=True)
    kc.add_argument("--mentions", required=True,
                    help='JSON-список гипотез (эхо того, что вернул kb-scan) или "-" для stdin')
    kc.set_defaults(func=cmd_kb_confirm)

    kr = sub.add_parser("kb-reject", help="отклонить гипотезу или заглушить слово целиком")
    kr.add_argument("--mention", required=True, help='JSON гипотезы или "-" для stdin')
    kr.add_argument("--mute", action="store_true", help="слово никогда не ссылка, у любой записи")
    kr.set_defaults(func=cmd_kb_reject)

    ke = sub.add_parser("kb-exclusions",
                        help="список отклонённых и заглушенных совпадений базы знаний")
    ke.set_defaults(func=cmd_kb_exclusions)

    kf = sub.add_parser("kb-forget", help="снять отказ — совпадение снова начнёт предлагаться")
    kf.add_argument("--key", required=True,
                    help='JSON {"kb_entry_id": ..., "text": ...} (эхо kb-exclusions) '
                         'или "-" для stdin')
    kf.set_defaults(func=cmd_kb_forget)

    kbl = sub.add_parser("kb-list", help="все записи базы знаний целиком")
    kbl.set_defaults(func=cmd_kb_note_list)

    kbs = sub.add_parser("kb-show", help="одна запись базы знаний по id")
    kbs.add_argument("id")
    kbs.set_defaults(func=cmd_kb_note_show)

    kbn = sub.add_parser("kb-create", help="завести запись базы знаний")
    kbn.add_argument("json", help='JSON {"title", "aliases", "body"} или "-" для stdin')
    kbn.set_defaults(func=cmd_kb_note_create)

    kbu = sub.add_parser("kb-update", help="править запись базы знаний")
    kbu.add_argument("id")
    kbu.add_argument("json", help='JSON с изменёнными полями или "-" для stdin')
    kbu.set_defaults(func=cmd_kb_note_update)

    kbx = sub.add_parser("kb-delete", help="удалить запись базы знаний насовсем")
    kbx.add_argument("id")
    kbx.set_defaults(func=cmd_kb_note_delete)

    x = sub.add_parser("export", help="выгрузить весь стор в Excel")
    x.add_argument("--to", help="куда писать; по умолчанию — «Выгрузка <дата>.xlsx» "
                                "в корне стора")
    x.set_defaults(func=cmd_export)

    bk = sub.add_parser("backup", help="снять резервную копию стора (R25)")
    bk.add_argument("--dest", help="куда класть копии; по умолчанию рядом с стором")
    bk.add_argument("--keep", type=int, help="сколько копий хранить, по умолчанию 7")
    bk.add_argument("--force", action="store_true",
                    help="снять копию сейчас, не глядя на расписание")
    bk.set_defaults(func=cmd_backup)

    bl = sub.add_parser("backups", help="список сделанных копий")
    bl.add_argument("--dest", help="папка копий; по умолчанию рядом с стором")
    bl.set_defaults(func=cmd_backup_list)

    rs = sub.add_parser("restore", help="восстановить стор из копии — перезаписывает данные")
    rs.add_argument("file", help="путь к архиву копии")
    rs.set_defaults(func=cmd_backup_restore)

    ej = sub.add_parser("export-json", help="выгрузить всю базу в JSON (R11)")
    ej.add_argument("--to", help="куда писать файл выгрузки; по умолчанию в корне стора")
    ej.set_defaults(func=cmd_export_json)

    args = p.parse_args()
    today = date.fromisoformat(args.today) if args.today else date.today()
    print(json.dumps(args.func(args, today), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
