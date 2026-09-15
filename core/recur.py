"""Журнал повторений и заведение очередных циклов по правилам шаблонов.

Переезд из `engine.py` (§3.4 спецификации среза 2): `recurrence_state_path`
→ `state_path`, `load_recurrence_state` → `load_state`, `save_recurrence_state`
→ `save_state`, `_cycle_closed` → `cycle_closed`, `_recompute_previous` →
`recompute_previous`, `_record_manual_cycle` → `record_manual_cycle`,
`cmd_recur` → `run`. Логика та же; изменилось только, откуда берётся путь к
стору (из `Context`, не из `engine.VAULT`) и через что создаются задачи
(`core.tasks.create_task`, а не `_create_task_from_data`).

Форма ответа `run` остаётся словарём `cmd_recur` без Pydantic: наружу её
отдают только CLI и легаси `/api/recur`, которые зовёт cron.
"""
import json
import os
import tempfile
from pathlib import Path

import templates as tpl
from core import attachments as core_attachments
from core import tasks as core_tasks
from core.context import Context
from core.errors import ValidationError
from domain import recurrence as rec
from domain.ru_dates import as_date
from domain.steps import task_status


def state_path(ctx: Context) -> Path:
    """Журнал повторений лежит рядом со стором, но не в нём: это отметки «какой
    цикл был последним», а не данные заказчика. Тот же приём, что у файла
    доставки в notify.py — потеря файла означает лишний повтор, а не потерю
    задачи."""
    return Path(ctx.vault) / ".повторения.json"


def load_state(ctx: Context) -> dict:
    path = state_path(ctx)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        # Битый журнал — не повод падать: хуже пропустить проверку блокировки
        # один раз, чем перестать заводить задачи по всем правилам разом.
        return {}


def save_state(ctx: Context, state: dict) -> None:
    path = state_path(ctx)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def recurring_title(name, cycle_date) -> str:
    """Имя автосозданной задачи. Голое имя шаблона совпало бы с прошлым циклом —
    ровно та коллизия, что при ручном разворачивании ловит понятную ошибку
    в форме, а здесь заведение идёт без человека и споткнуться не о что."""
    return f"{name} — {cycle_date:%d.%m.%Y}"


def cycle_closed(ctx: Context, task_title, today) -> bool:
    """Закрыт ли цикл — по статусу задачи, которую он породил.

    Задача могла исчезнуть: заказчик вправе удалить её руками. Отсутствие
    считаем закрытием, а не блокировкой навсегда — иначе удалённая вручную
    задача остановила бы правило насовсем, и это тише любой ошибки.

    Точное совпадение названия, как и раньше (`stem == task_name`); только
    ищется оно одним запросом по колонке, а не проходом по всем задачам со
    сборкой шагов.
    """
    task_id = ctx.store.find_task_id(task_title)
    if task_id is None:
        return True
    task = ctx.store.task_by_id(task_id)
    if task is None:
        return True
    return task_status(task, today) == "done"


def recompute_previous(ctx: Context, record: dict, today):
    """`previous` из журнала повторений с пересчитанным на сегодня `closed`.

    Статус закрытия не хранится, а вычисляется заново из фактического состояния
    задачи при каждом обращении (см. `cycle_closed`) — и `run`, и запись цикла
    из `instantiate` должны считать его одинаково, иначе один сочтёт цикл
    открытым, а другой закрытым, и решения разойдутся.
    """
    if not record.get("previous"):
        return None
    предыдущий = dict(record["previous"])
    задача_цикла = предыдущий.pop("task", None)
    if задача_цикла:
        предыдущий["closed"] = cycle_closed(ctx, задача_цикла, today)
    return предыдущий


def _rule_without_anchor(правило: dict) -> dict:
    return {k: v for k, v in правило.items() if k != "anchor"}


def record_manual_cycle(ctx: Context, template: dict, task_title: str, today) -> None:
    """Задача, заведённая вручную через «Завести задачу» по шаблону с активным
    повторением, закрывает собой тот же цикл, который иначе следующим прогоном
    создал бы `run` — issue #2. Без этой записи `run` не видит ручную задачу
    (её имя не совпадает с `recurring_title`) и заводит для того же цикла
    второй экземпляр.

    Журнал правится так, будто цикл создал сам `run`: тот же расчёт через
    `due_cycles`, и только если он в самом деле нашёл цикл к созданию — цикл,
    заведённый заранее (раньше своего дня по `lead_days`) или заблокированный
    незакрытым предыдущим, ручная задача не трогает.
    """
    правило = template.get("recurrence")
    if not правило:
        return
    имя = template["name"]
    state = load_state(ctx)
    запись = state.get(имя) or {}
    предыдущий = recompute_previous(ctx, запись, today)
    якорь = as_date(правило["anchor"])
    try:
        решения = rec.due_cycles(
            _rule_without_anchor(правило), якорь, today,
            previous=предыдущий, work=ctx.work(), force=False, limit=1)
    except rec.RuleError:
        return
    создан = next((р for р in решения if р["action"] == "create"), None)
    if создан is None:
        return
    запись["previous"] = {"date": создан["date"].isoformat(), "closed": False,
                          "task": task_title}
    state[имя] = запись
    save_state(ctx, state)


def run(ctx: Context, today, *, name=None, force=False, limit=12) -> dict:
    """Прогнать шаблоны с правилом повторения: создать очередной цикл или
    записать пропуск. Раздел 5.12 ТЗ.

    Идемпотентно в границах контракта: незакрытый цикл при повторном вызове в
    тот же день снова даёт пропуск, а не вторую задачу — `due_cycles` сам не
    продвигает журнал, пока предыдущий цикл не закрыт.

    `name` — точное имя шаблона (с регистром, как в CLI `--name`); `force`
    действует только на названный шаблон. Календарь выходных берётся из
    настроек заказчика (`ctx.work()`), тем же, что у предпросмотра правила:
    иначе форма показала бы перенос с субботы, а cron завёл бы цикл без него.
    """
    склад = tpl.JsonStore(ctx.vault)
    state = load_state(ctx)
    work = ctx.work()
    # Список названий читается один раз: движок повторений создаёт несколько
    # задач подряд, и перечитывать стор перед каждой — лишний проход.
    задачи_кэш = [title for _, title in ctx.store.titles()]

    отчёт = []
    for шаблон in склад.all():
        правило = шаблон.get("recurrence")
        if not правило:
            continue
        имя = шаблон["name"]
        if name and имя != name:
            continue
        запись = state.get(имя) or {}
        предыдущий = recompute_previous(ctx, запись, today)

        якорь = as_date(правило["anchor"])
        сила = bool(force) and name == имя
        try:
            решения = rec.due_cycles(
                _rule_without_anchor(правило), якорь, today,
                previous=предыдущий, work=work, force=сила, limit=limit or 12)
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
            try:
                задача = core_tasks.create_task(
                    ctx, данные, today, existing=задачи_кэш,
                    template_name=имя, cycle_key=решение["key"])
            except ValidationError as e:
                # Название занято чем-то посторонним — не тем же циклом: имя
                # несёт дату, и наше собственное совпадение уже поймала бы
                # проверка выше по этому же циклу. Останавливаем это правило,
                # остальные шаблоны идут дальше своим чередом.
                сбой = e.errors
                break
            задачи_кэш.append(задача["path"].stem)
            core_attachments.copy_template_to_task(
                ctx, имя, задача["path"].stem, today)
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

    save_state(ctx, state)
    return {"today": today.isoformat(), "templates": отчёт,
            "created": sum(len(t["created"]) for t in отчёт)}
