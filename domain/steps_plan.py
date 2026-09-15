"""План шагов задачи: чистые функции над dict-формой стора (§3.2 спецификации
среза 2).

Сюда переехали из `engine.py` файлом, без изменения логики: разбор дат шагов,
дефолт даты начала, проверка новой задачи и правки, сборка meta и применение
правки. `engine.py` импортирует всё обратно под прежними именами: тесты и
`templates.py` зовут `engine.resolve_steps` и `engine.build_task`, и ломать эти
адреса ради переезда файла незачем — они уйдут вместе с `engine.py` в срезе 5.

Новое здесь: `plan` и `to_planned` для живого предпросмотра формы (Р9),
`missing_step_ids` — подсчёт пропавших шагов до мутации задачи (раньше
`apply_task_edit` сначала переписывал шаги, потом сообщал о потере; ядру нужно
решить об отказе до записи, не портя прочитанную задачу), и нормализация
листа, ставшего группой (Р14).

Пути ошибок здесь точечные (`steps.1.steps.0.control_date`); в скобки их
переводит `api/errors.py::bracket_path` (Р1). Правило имени — в
`domain.names.title_error`.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from domain.names import title_error
from domain.ru_dates import as_date, format_control, parse_date_input
from domain.steps import OPEN, steps_of

# Версия формы задачи в `meta["schema"]`. Дублирует `core.persist.TASK_SCHEMA`
# осознанно: `domain` ничего из `core` не импортирует, а `save_task` всё равно
# переписывает поле своим числом перед записью — здесь оно только для того,
# чтобы meta от `build_task` была полной и до записи.
TASK_SCHEMA = 1

MODES = ("par", "seq")

# Текст ошибки даты у шагов намеренно без «· через час»: пресет считается от
# настоящего момента, которого у даты контроля шага в форме нет. Полный
# список — `core.dates.НЕ_ПОНЯЛ`, для окна контроля.
ДАТУ_НЕ_ПОНЯЛ = "Дату не понял. Можно: 18.08 · 15 марта · завтра · +3 · пн · полдесятого"

# Маркеры блока шагов в теле задачи. Блок писал markdown-вывод до `47cdc52`; у
# заказчика такие задачи остались в базе, и карточка обязана срезать его из
# заметки, а подтверждение ссылок базы знаний — считать смещения без него.
STEPS_START = "<!-- шаги: пишет движок, править руками не нужно -->"
STEPS_END = "<!-- /шаги -->"


def _parse_optional_date(raw, today, поле, errors, *, now=None):
    """Разобрать необязательную дату, добавить ошибку в список при провале.

    Общий кусок между валидацией контрольной даты и даты начала: разное
    сообщение об ошибке на одну и ту же дату сбивает с толку. `now` прокидывается
    в разбор ради быстрого ввода («через час»); текст ошибки от него не зависит.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return parse_date_input(raw, today, now=now)
    except (ValueError, TypeError):
        errors.append({"field": поле, "error": ДАТУ_НЕ_ПОНЯЛ})
        return None


def default_step_start(предыдущий_контроль, старт_задачи):
    """Дата начала шага по умолчанию — раздел 6.3.2 ТЗ: контроль предыдущего
    шага, а для первого шага дата начала задачи."""
    return предыдущий_контроль or старт_задачи


def resolve_steps(steps_data, старт_задачи, today, старые=None, prefix="steps",
                  предыдущий=None, mode="seq"):
    """Разобрать шаги и подставить дефолт даты начала — один проход, которым
    пользуются и проверка, и запись, и создание, и правка.

    Раньше дефолт вычислялся заново в `build_task`, отдельно от `validate_new_task`:
    проверка смотрела только на то, что пришло в запросе, и дефолт мог обогнать
    control_date уже после проверки. Здесь дефолт и проверка смотрят на одни
    и те же значения.

    Шаг с непустым списком `steps` — группа: у неё режим ("par" по умолчанию,
    "seq" для подцепочки), дат нет, дети разбираются рекурсивно. Дефолт даты
    начала листа — по последовательной цепочке: контроль предыдущего элемента,
    у группы это максимум контролей её поддерева. Внутри параллельной группы
    цепочки нет: каждый ребёнок стартует от точки входа группы.

    `старые` — режим правки: для листа с известным `id` без явной даты начала
    берётся сохранённое, а не дефолт по новому порядку. Без этого чистая
    перестановка шагов в карточке упиралась бы в «контроль раньше начала».

    Возвращает дерево словарей: title, start, control, note, id, mode,
    children, errors, явный_старт, поле, предыдущий (для мягких предупреждений).
    """
    resolved = []
    for i, step in enumerate(steps_data or []):
        поле = f"{prefix}.{i}"
        errors: list[dict] = []
        if not (step.get("title") or "").strip():
            errors.append({"field": f"{поле}.title", "error": "Название шага обязательно"})
        дети_данные = step.get("steps") or []
        node: dict[str, Any] = {
            "title": step.get("title"), "note": step.get("note"),
            "id": step.get("id"), "errors": errors, "children": [],
            "mode": None, "start": None, "control": None,
            "явный_старт": False, "поле": поле, "предыдущий": предыдущий}
        if дети_данные or step.get("mode"):
            режим = step.get("mode") or "par"
            if режим not in MODES:
                errors.append({"field": f"{поле}.mode",
                               "error": "Режим группы — par или seq"})
                режим = "par"
            node["mode"] = режим
            if not дети_данные:
                errors.append({"field": f"{поле}.steps",
                               "error": "В группе нужен хотя бы один подшаг"})
            for k in ("control_date", "start_date"):
                if step.get(k):
                    errors.append({"field": f"{поле}.{k}",
                                   "error": "Даты ставятся подшагам, не группе"})
            node["children"] = resolve_steps(
                дети_данные, старт_задачи, today, старые,
                prefix=f"{поле}.steps", предыдущий=предыдущий, mode=режим)
            финиш = _финиш_узла(node)
        else:
            control = _parse_optional_date(step.get("control_date"), today,
                                           f"{поле}.control_date", errors)
            явный_старт = _parse_optional_date(step.get("start_date"), today,
                                               f"{поле}.start_date", errors)
            сохранённый = None
            if старые is not None and step.get("id") in старые:
                сохранённый = as_date(старые[step["id"]].get("start_date"))
            start = явный_старт or сохранённый or default_step_start(предыдущий,
                                                                     старт_задачи)
            # Жёсткая проверка из раздела 6.3.3 ТЗ: контроль раньше, чем шаг можно
            # начать, бессмысленен как дата — блокирует сохранение. Сравниваем с
            # итоговым start (явным или дефолтным), а не только с введённым.
            if start and control and as_date(control) < as_date(start):
                errors.append({"field": f"{поле}.control_date",
                               "error": "Контроль раньше даты начала шага"})
            node.update(start=start, control=control,
                        явный_старт=bool(явный_старт))
            финиш = control
        resolved.append(node)
        if mode != "par" and финиш:
            предыдущий = финиш
    return resolved


def _финиш_узла(node):
    """Когда элемент цепочки «кончается» для дефолта следующего: у листа это
    его контроль, у группы — самый поздний контроль поддерева. None, если дат
    в поддереве нет вовсе."""
    даты = [node["control"]] if node["control"] else []
    даты += [f for f in (_финиш_узла(c) for c in node["children"]) if f]
    return max(даты, key=as_date) if даты else None


def walk_resolved(nodes):
    """Дерево resolve_steps плоским потоком, глубина-первым порядком."""
    for n in nodes:
        yield n
        yield from walk_resolved(n["children"])


def validate_new_task(data, existing_names, today):
    """Проверка задачи до записи. Возвращает список ошибок по полям — тех,
    что блокируют сохранение. Мягкие предупреждения — отдельно, в `soft_warnings`.

    Отдельно от формы намеренно: правила должны быть в одном месте, иначе форма
    и CLI разойдутся, и в стор попадёт то, что движок потом не прочитает.
    Ошибки возвращаются списком, а не первым попавшимся исключением, — форме надо
    подсветить все проблемные поля разом, а не гонять человека по кругу.
    """
    errors: list[dict] = []

    ошибка_имени = title_error(data.get("title") or "", existing_names)
    if ошибка_имени:
        errors.append({"field": "title", "error": ошибка_имени})

    старт_задачи = _parse_optional_date(data.get("start_date"), today,
                                        "start_date", errors) or today

    steps = data.get("steps") or []
    if not steps:
        errors.append({"field": "steps", "error": "Нужен хотя бы один шаг"})
    for r in walk_resolved(resolve_steps(steps, старт_задачи, today)):
        errors += r["errors"]
    return errors


def soft_warnings(data, today):
    """Мягкие предупреждения из раздела 6.3.3 ТЗ: сохранить можно, но человек
    должен увидеть, что даты выглядят подозрительно.

    Не блокируют запись, поэтому отдельная функция, а не часть `validate_new_task`:
    смешивать в одном списке то, что останавливает сохранение, с тем, что просто
    предупреждает, заставило бы форму гадать, какая ошибка какая.

    Сравнение идёт по явно введённой дате начала, не по дефолтной: дефолт равен
    как раз тому, с чем его сравнивают (концу задачи или предыдущему шагу), и
    строгое «меньше» на них никогда не сработает — предупреждать не о чем.
    """
    старт_задачи = _parse_optional_date(data.get("start_date"), today, None, []) or today
    return _warnings_of(resolve_steps(data.get("steps") or [], старт_задачи, today),
                        старт_задачи)


def _warnings_of(nodes, старт_задачи):
    warnings = []
    for r in walk_resolved(nodes):
        if not r["явный_старт"]:
            continue
        if as_date(r["start"]) < as_date(старт_задачи):
            warnings.append({"field": f'{r["поле"]}.start_date',
                             "warning": "Шаг начинается раньше даты начала задачи"})
        if r["предыдущий"] and as_date(r["start"]) < as_date(r["предыдущий"]):
            warnings.append({"field": f'{r["поле"]}.start_date',
                             "warning": "Шаг начинается раньше, чем закончится "
                                       "предыдущий"})
    return warnings


def build_task(data, today):
    """Данные формы → meta задачи. Без записи.

    Идентификаторы шагов раздаёт движок, а не форма: они должны быть плотными и
    по порядку, иначе `done <задача> 3` будет попадать не туда.
    """
    старт_задачи = _parse_optional_date(data.get("start_date"), today, None, []) or today
    steps: list[dict] = []

    def добавить(nodes, parent):
        for r in nodes:
            sid = len(steps) + 1
            steps.append({
                "id": sid,
                "title": r["title"].strip(),
                # У группы статус смысла не несёт (закрытие вычисляется из
                # детей), но форма записи шага одна на всех — колонка NOT NULL.
                "status": OPEN,
                "start_date": r["start"],
                "control_date": r["control"],
                "completed_date": None,
                "note": (r["note"] or "").strip() or None,
                "parent": parent,
                "mode": r["mode"],
                "log": [],
            })
            добавить(r["children"], sid)

    добавить(resolve_steps(data.get("steps") or [], старт_задачи, today), None)
    tags = [t.strip() for t in (data.get("tags") or []) if t and t.strip()]
    meta = {
        "schema": TASK_SCHEMA,
        "type": "task",
        "title": data["title"].strip(),
        "created": today,
        "start_date": старт_задачи,
        "tags": tags,
        "steps": steps,
    }
    return meta


# --- правка существующей задачи --------------------------------------------
#
# Отдельно от создания. При правке шаги приходят с уже известными `id`, и эти
# id обязаны пережить редактирование: на них ссылаются `done`/`notdone`/
# `defer`/`fail`/`skip`, и переезд с 1..N при каждом сохранении раскидал бы
# отметки не по тем шагам.
#
# Статус, дата закрытия и журнал шага правкой не трогаются никогда — это поле
# зоны четырёх команд перехода, а не карточки. Карточка меняет только то, что
# заказчик видит как метаданные: заголовок, даты, заметку, порядок, состав.

def _edit_start(task, data, today, errors):
    """Старт задачи в режиме правки: из данных, иначе сохранённый, иначе сегодня."""
    return (_parse_optional_date(data.get("start_date"), today, "start_date", errors)
            or as_date(task["meta"].get("start_date")) or today)


def validate_task_edit(task, data, existing_names, today):
    """Проверка правки — со своими правилами дат, а не `validate_new_task`: та
    не знает про сохранённые даты существующих шагов и на чистой перестановке
    без единой правки дат ошибалась бы сама.
    """
    errors: list[dict] = []
    # Дубль считается среди чужих названий: своё, вернувшееся из карточки
    # без изменений, дублем не является.
    свои = {n for n in existing_names if n.lower() != task["path"].stem.lower()}
    ошибка_имени = title_error(data.get("title") or "", свои)
    if ошибка_имени:
        errors.append({"field": "title", "error": ошибка_имени})

    старт_задачи = _edit_start(task, data, today, errors)
    старые = {s["id"]: s for s in steps_of(task)}

    steps = data.get("steps") or []
    if not steps:
        errors.append({"field": "steps", "error": "Нужен хотя бы один шаг"})
    for r in walk_resolved(resolve_steps(steps, старт_задачи, today, старые=старые)):
        errors += r["errors"]
    return errors


def _ids_in(steps_data):
    for step in steps_data or []:
        if step.get("id") is not None:
            yield step["id"]
        yield from _ids_in(step.get("steps") or [])


def missing_step_ids(task, data) -> list[int]:
    """Сохранённые шаги, которых нет в присланных данных. Считается до любой
    мутации: ядро решает об отказе (без `force`) ещё до того, как тронет
    прочитанную задачу, и отказ ничего не портит."""
    увиденные = set(_ids_in(data.get("steps") or []))
    return [s["id"] for s in steps_of(task) if s["id"] not in увиденные]


def apply_task_edit(task, data, today):
    """Переписать метаданные задачи по данным карточки. Возвращает список
    id шагов, которые пропали из данных без явного «снять» — тот же список,
    что `missing_step_ids` до вызова; вызывающему, который проверил его
    заранее, повторно смотреть не нужно.
    """
    meta = task["meta"]
    старые = {s["id"]: s for s in steps_of(task)}
    старт_задачи = _edit_start(task, data, today, [])
    пропали = missing_step_ids(task, data)

    следующий_id = max([s["id"] for s in старые.values()], default=0) + 1
    новые: list[dict] = []

    def добавить(nodes, parent):
        nonlocal следующий_id
        for r in nodes:
            если_старый = r["id"] is not None and r["id"] in старые
            if если_старый:
                шаг = dict(старые[r["id"]])  # статус/completed_date/log копируются как есть
            else:
                # Тот же порядок полей, что у build_task, — иначе новый шаг
                # выглядит написанным другой рукой, хотя человеку разницы нет.
                шаг = {"id": следующий_id, "title": None, "status": OPEN,
                       "start_date": None, "control_date": None,
                       "completed_date": None, "note": None, "parent": None,
                       "mode": None, "log": []}
                следующий_id += 1
            шаг["title"] = r["title"].strip()
            шаг["start_date"] = r["start"]
            шаг["control_date"] = r["control"]
            шаг["note"] = (r["note"] or "").strip() or None
            # Родитель и режим переписываются и у старых шагов: карточка могла
            # перетащить шаг в группу или обратно, это правка структуры, а не
            # статуса. Статус и журнал при этом не трогаются.
            шаг["parent"] = parent
            шаг["mode"] = r["mode"]
            if r["mode"]:
                # Лист, ставший группой (Р14): статус у группы смысла не несёт,
                # закрытие вычисляется из детей, и оставшийся от листа `done`
                # в БД читался бы как закрытая группа с открытыми детьми.
                # Журнал остаётся: это история, а не состояние.
                шаг["status"] = OPEN
                шаг["completed_date"] = None
            новые.append(шаг)
            добавить(r["children"], шаг["id"])

    добавить(resolve_steps(data.get("steps") or [], старт_задачи, today, старые=старые),
             None)

    meta["title"] = data.get("title", meta["title"]).strip()
    meta["start_date"] = старт_задачи
    if "tags" in data:
        meta["tags"] = [t.strip() for t in (data.get("tags") or []) if t and t.strip()]
    meta["steps"] = новые
    if "body" in data:
        task["body"] = (data.get("body") or "").strip() + "\n"
    return пропали


# --- предпросмотр плана (Р9) -------------------------------------------------

def plan(steps_data, start, today, *, old=None):
    """Живой предпросмотр формы: дерево узлов `resolve_steps`, ошибки и мягкие
    предупреждения одним проходом.

    Ошибки `*.title` сюда не попадают: предпросмотр зовётся на каждое нажатие,
    и пустое название шага в момент набора — не ошибка, название проверяется
    при записи. `old` — сохранённые шаги задачи в режиме правки (как `старые` у
    `resolve_steps`), чтобы лист с известным id держал свою дату начала.
    """
    nodes = resolve_steps(steps_data or [], start, today, старые=old)
    errors = [e for r in walk_resolved(nodes) for e in r["errors"]
              if not e["field"].endswith(".title")]
    return nodes, errors, _warnings_of(nodes, start)


def _text(moment) -> str | None:
    return format_control(moment) if isinstance(moment, (date, datetime)) else None


def to_planned(nodes) -> list[dict]:
    """Дерево узлов → дерево словарей формы `PlannedStep` (§2.1): путь с точками,
    даты строкой через `format_control`, чтобы клиент мог вернуть их в поле как
    есть (Р2). Модель Pydantic из них собирает `core`, `domain` про неё не знает."""
    return [{
        "path": n["поле"],
        "id": n["id"],
        "mode": n["mode"],
        "start": _text(n["start"]),
        "control": _text(n["control"]),
        "explicit_start": bool(n["явный_старт"]),
        "steps": to_planned(n["children"]),
    } for n in nodes]


# --- заметка без блока шагов -------------------------------------------------

def strip_steps_block(body):
    """Тело без блока шагов плюс способ вернуть блок на прежнее место.

    Смещения гипотез базы знаний посчитаны против текста БЕЗ этого блока,
    поэтому сплайс ссылок должен идти по той же системе координат. Блока может
    не быть вовсе — тогда возвращается тело как есть.

    Первая вставка блока (markdown-вывод до `47cdc52`) склеивала его с текстом
    заказчика через «\\n\\n» — до одного перевода строки, если текста не было.
    Эта склейка не текст заказчика, а механика рендера: не срезать её здесь —
    значит увести смещения на два символа для задачи, которую подтверждают
    сразу после первого сохранения. Дальше склейка не менялась, поэтому срез
    безопасен и на повторных сохранениях — режется всегда один и тот же кусок.
    """
    start = body.find(STEPS_START)
    end = body.find(STEPS_END)
    if start == -1 or end == -1 or end <= start:
        return body, lambda stripped: stripped
    block_end = end + len(STEPS_END)
    block = body[start:block_end]
    tail = body[block_end:]
    склейка = tail[:2] if tail[:2] == "\n\n" else (tail[:1] if tail[:1] == "\n" else "")
    return (body[:start] + tail[len(склейка):],
            lambda stripped: stripped[:start] + block + склейка + stripped[start:])
