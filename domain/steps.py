"""Дерево шагов: закрытие, активные листья, статус задачи, буксование.

Перенесено из `engine.py` файлом (REFACTOR.md, срез 1a) — логика не менялась.
Шаг и задача здесь по-прежнему словари той формы, что отдаёт `store`:
`{"path": TaskRef, "meta": {..., "steps": [...]}, "body": str}`. Типизированные
модели появляются на выходе `core`, а не здесь: переписывать вычисления над
словарями ради аннотаций значило бы трогать самый проверенный код без причины.
"""
from domain.ru_dates import as_date

OPEN = "pending"
DONE = "done"
SKIPPED = "skipped"
FAILED = "failed"

# Статусы, означающие «шаг закрыт». Единственное место, где это знание
# записано: `is_closed` ниже и SQL-фильтры в `store.py` берут его отсюда.
# Незнакомый статус в этот кортеж не входит и потому считается открытым —
# см. докстринг `is_closed`.
CLOSED_STATUSES = (DONE, SKIPPED, FAILED)


# В стор статус пишется по-русски: эти файлы читает заказчик, а не только движок.
# В JSON наружу уходят английские ключи — там интерфейс для бота.
STATUS_RU = {
    "overdue": "просрочена",
    "due": "сегодня",
    "waiting": "ждёт",
    "no_date": "без даты",
    "done": "закрыта",
    "empty": "нет шагов",
    "cancelled": "отменена",
}


def steps_of(task):
    return task["meta"].get("steps") or []


def is_closed(step):
    """Шаг закрыт, только если статус — один из двух известных нам.

    Всё незнакомое считаем открытым, а не закрытым. Стор правится руками, и
    заказчик вполне может напечатать `status: сделан` вместо `done`.
    Раньше это роняло `list`/`next`/`refresh`/`export` целиком с traceback:
    «закрыт» и «открыт» проверялись двумя разными правилами, и на незнакомом
    статусе они расходились — задача не считалась закрытой, но и текущего шага
    в ней не находилось.
    """
    return step.get("status", OPEN) in CLOSED_STATUSES


def is_group(step):
    """Группа подшагов. У группы есть режим — "par" (порядок не важен) или
    "seq" (подшаги по очереди), — а дат, статуса и журнала нет: закрытие
    вычисляется из детей. Обычный шаг режима не имеет."""
    return bool(step.get("mode"))


def _children_map(steps):
    """id родителя → его дети в порядке хранения. Верхний уровень — под ключом
    None. Плоский список из БД идёт в глубину-первом порядке, поэтому дети
    каждого родителя здесь оказываются в своём относительном порядке."""
    m: dict = {}
    for s in steps:
        m.setdefault(s.get("parent"), []).append(s)
    return m


def _closure(steps):
    """(закрыт?, карта детей) для дерева шагов. Лист закрыт по статусу, группа —
    когда закрыты все дети. Группа без детей считается закрытой: валидация
    такие не пропускает, а на битых данных «закрыта» безопаснее вечно
    открытой — не всплывает в ленте."""
    m = _children_map(steps)

    def закрыт(s):
        if is_group(s):
            return all(закрыт(c) for c in m.get(s["id"], []))
        return is_closed(s)

    return закрыт, m


def leaves_of(task):
    return [s for s in steps_of(task) if not is_group(s)]


def current_steps(task):
    """Активные листья — то, по чему сейчас идёт работа. В последовательной
    цепочке это листья первого незакрытого элемента; параллельная группа
    отдаёт активные листья всех своих незакрытых детей разом."""
    steps = steps_of(task)
    закрыт, m = _closure(steps)

    def раскрыть(s):
        if not is_group(s):
            return [s]
        дети = [c for c in m.get(s["id"], []) if not закрыт(c)]
        if s["mode"] == "par":
            return [лист for c in дети for лист in раскрыть(c)]
        return раскрыть(дети[0]) if дети else []

    for s in m.get(None, []):
        if not закрыт(s):
            return раскрыть(s)
    return []


def current_step(task):
    """Первый активный лист — для сводки и мест, где нужен один шаг."""
    активные = current_steps(task)
    return активные[0] if активные else None


def task_status(task, today):
    # Отмена — состояние задачи целиком, не выводится из шагов и стоит впереди
    # любого другого правила: отменённая задача не должна всплывать просроченной
    # только потому, что в ней остался незакрытый шаг.
    if task["meta"].get("cancelled"):
        return "cancelled"
    steps = steps_of(task)
    if not steps:
        return "empty"
    активные = current_steps(task)
    if not активные:
        return "done"
    # Активных листьев может быть несколько (параллельная группа) — задача
    # получает худшее из их состояний: просрочка перекрывает «сегодня», та —
    # отсутствие даты, та — ожидание. Один лист даёт прежнее поведение.
    состояния = set()
    for step in активные:
        due = as_date(step.get("control_date"))
        if due is None:
            состояния.add("no_date")
        elif due < today:
            состояния.add("overdue")
        elif due == today:
            состояния.add("due")
        else:
            состояния.add("waiting")
    for худшее in ("overdue", "due", "no_date"):
        if худшее in состояния:
            return худшее
    return "waiting"


def stall_count(step):
    """Сколько раз шаг не сделали. Отличает «ещё не дошли руки» от «буксует».

    Массовый перенос (`mass_defer`) считается наравне с «не сделан» — R20 ТЗ
    прямо требует, чтобы он увеличивал счётчик. Обычный одиночный `defer` сюда
    не идёт: там дату для конкретного шага выбирают осознанно, это не то же
    самое, что «опять не собрались» (см. test_defer_does_not_count_as_stalling).
    """
    return sum(1 for e in step.get("log") or [] if e.get("event") in ("not_done", "mass_defer"))


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


def task_summary(task, today):
    """status/current_step/control_date/stalled/progress — сводка верхнего
    уровня, которую `save()` мержит в `task["meta"]` перед возвратом.

    Отдельная функция, а не кусок внутри save(): раньше сводку было видно
    только прочитав только что написанный файл, теперь — сразу после чтения
    из БД её там нет (колонок под неё нет, source of truth в шагах). Тестам и
    любому будущему коду, которому нужен «файл как он раньше выглядел бы»,
    нужен ровно этот пересчёт, а не второе его написание.
    """
    листья = leaves_of(task)
    активные = current_steps(task)
    step = активные[0] if активные else None
    closed = sum(1 for s in листья if is_closed(s))
    # Прогресс считается по листьям: группа — скобка вокруг подшагов, а не
    # отдельная единица работы. Контроль сводки — ближайший из активных.
    контроли = sorted((as_date(s["control_date"]) for s in активные
                       if s.get("control_date")))
    return {
        "status": STATUS_RU[task_status(task, today)],
        "current_step": step.get("title") if step else None,
        "control_date": контроли[0] if контроли else None,
        "stalled": max((stall_count(s) for s in активные), default=0),
        "progress": f"{closed}/{len(листья)}" if листья else None,
    }
