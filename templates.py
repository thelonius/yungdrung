#!/usr/bin/env python3
"""Шаблоны задач — требование R22, раздел 5.6 ТЗ.

Шаблон — заготовка задачи: название, теги и шаги со сдвигом в днях от даты старта
и временем контроля. Из шаблона и даты старта получаются данные для
`engine.build_task`, то есть обычная задача. Своего пути записи задач здесь нет:
пишет по-прежнему движок, иначе появится второй писатель вольта.

Граница «как считается» и «как хранится» проходит по классу `Store`. Считающая
половина — `validate_template`, `normalize_template`, `control_moment`, `expand`,
`preview`, `template_from_task` и функции над списком `find`, `put`, `drop` —
принимает словари и списки, возвращает словари и списки, файлов не открывает и
путей не знает. Хранящая половина — два метода: `_load` отдаёт список шаблонов,
`_commit` записывает его целиком. Публичные `all`, `get`, `save` и `delete`
стоят на границе: они складывают чистые функции с этими двумя вызовами, своей
арифметики над шаблонами у них нет. Переезд вольта на SQLite означает третьего
наследника рядом с `MemoryStore` и `JsonStore`, а в расчётах ни одной правки.

Выходные, рабочие часы и момент показа считает `worktime`; правила имени задачи
и разбор времени приходят из `engine`. Своего календаря выходных и своего списка
запрещённых символов здесь нет намеренно: по контракту считает ядро, и считает в
одном месте. Разъехавшись, две копии дают предпросмотр, который метит шаг
субботним и тут же назначает показ на ту же субботу.

Даты наружу уходят строкой «2026-08-18 14:00», а не ISO с «T». Так надо: разбор
даты в движке (`engine.parse_date_input`) видит в «2026-08-18T14:00» двоеточие,
считает всю строку временем и падает с «не время». Пробел он разбирает верно, и
именно в этом виде дата доезжает до задачи.
"""
import copy
import json
import os
import re
import tempfile
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

# Движок импортируется рядом с `worktime`, а не внутри функций. Отложенный импорт
# был нужен прошлому заходу, чтобы пережить отсутствие pyyaml, но pyyaml стоит и
# записан в requirements.txt: движок читает им весь вольт. Если его всё-таки нет,
# `engine` зовёт sys.exit прямо при импорте — тогда упасть на старте честнее, чем
# подсунуть расчёту запасную копию правил имени и разойтись с движком.
import engine
import worktime

SCHEMA = 1

# Сдвиг больше десяти лет — это почти всегда дата, набранная в поле сдвига
# (20260818 вместо 3). Пропустить такое означает выдать задачу с контролем в
# 57000 году: в ленте её никогда не будет, и заказчик решит, что шаблон не работает.
MAX_OFFSET_DAYS = 3650

# Порядковый номер дня недели → сокращение для предпросмотра. Заказчик смотрит на
# список дат и должен видеть, что шаг попал на субботу, не сверяясь с календарём.
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


class TemplateError(Exception):
    """Ошибка с разбором по полям, а не строкой.

    Контракт требует структурных ошибок: форма подсвечивает поля, бот собирает их
    в фразу. Исключение несёт тот же список, что возвращает `validate_template`,
    поэтому оболочке достаточно отдать `e.errors` в JSON.
    """

    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(e.get("error", "") for e in errors))


# --- разбор значений -------------------------------------------------------

def parse_offset(value):
    """Сдвиг в днях. Пустое значение — ноль, то есть шаг в день старта.

    Ноль по умолчанию, потому что шаблон из одного шага («позвонить в день X»)
    иначе требует писать `offset_days: 0` — поле, которое ничего не добавляет.
    Строку принимаем: и форма, и SQLite умеют отдать «3» текстом.

    Движковый разбор `parse_date_input` здесь не подходит, хотя «+3» понимает и
    он: движок отсчитывает от сегодняшнего дня и отдаёт готовую дату, а шаблон
    хранит сам сдвиг, ещё ни к какому старту не привязанный. Числовое поле и
    остаётся числовым, до даты его доводит `control_moment`.
    """
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        # True прошёл бы как int(1) и дал бы шаг «через день» из галочки.
        raise ValueError("сдвиг задаётся числом дней")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != int(value):
            raise ValueError("сдвиг задаётся целым числом дней")
        return int(value)
    s = str(value).strip()
    if not re.fullmatch(r"[+-]?\d+", s):
        raise ValueError(f"не сдвиг в днях: {value!r}")
    return int(s)


def parse_time_of_day(value):
    """Время контроля: «14:00», «9.30», «18-45» → time. Пусто → None.

    Разбирает `engine.parse_time_part`, а не своя регулярка: заказчик набирает
    время одинаково в быстром вводе и в поле шага, и понимать их должно одно
    место. Расходится только трактовка непонятного значения: в «позвонить утром»
    движок времени не находит и ошибкой это не считает, а возвращает None и
    разбирает остаток строки. Здесь разбирается ровно поле времени, поэтому None
    из движка означает опечатку в нём.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, dtime):
        return value
    s = str(value).strip()
    if not s:
        return None
    время = engine.parse_time_part(s)
    if время is None:
        raise ValueError(f"не время: {value!r}")
    return время


def format_time_of_day(value):
    """Время в хранимый вид. None остаётся None: шаг без времени назначен на весь
    день, и рабочие часы для него считаются от начала суток."""
    return None if value is None else value.strftime("%H:%M")


def as_start_date(value):
    """Дата старта. Время в ней игнорируется: сдвиг считается по календарным
    дням, и «старт 18 августа в 17:00» означает тот же день, что «18 августа».

    Само значение разбирает `worktime.as_datetime`: он уже принимает date,
    datetime и строку, и второй разборщик рядом с ним рано или поздно разойдётся
    на краевом случае. Пустое значение отвергаем: сдвигать нечего.
    """
    момент = worktime.as_datetime(value)
    if момент is None:
        raise ValueError("дата старта обязательна")
    return момент.date()


def split_control_value(value):
    """Значение контроля → (дата, время). Времени может не быть: дата без часа
    означает весь день.

    Строку разбираем наравне с date и datetime, потому что приходит она
    по-разному: из вольта дату достаёт yaml уже разобранной, а из JSON и из
    будущей таблицы SQLite она приезжает текстом. Пока строку разбирала только
    ветка даты, «2026-08-17 10:00» превращалось в шаг без времени контроля.
    """
    if value is None:
        return None, None
    if isinstance(value, datetime):
        return value.date(), value.time()
    if isinstance(value, date):
        return value, None
    s = str(value).strip()
    try:
        return date.fromisoformat(s), None
    except ValueError:
        момент = worktime.as_datetime(s)
        return момент.date(), момент.time()


# --- расчёт ----------------------------------------------------------------

def control_moment(start_date, offset_days, time_of_day=None):
    """Дата старта + сдвиг в днях = момент контроля.

    Сдвиг календарный, поэтому несуществующих дат тут не бывает: от 31 января
    двадцать девять дней приводят к 1 марта, а «31 февраля» получиться неоткуда.
    Сдвиг по месяцам R22 не требует, и хорошо: он сразу ставит вопрос, во что
    превращается «31-е» в феврале.

    Сдвиг за пределы календаря (в поле сдвига набрана дата — 20260818) даёт
    ValueError, а не OverflowError: предпросмотр в форме считает шаблон до
    сохранения, то есть до `validate_template`, и должен показать ошибку поля,
    а не уронить запрос.
    """
    try:
        день = as_start_date(start_date) + timedelta(days=offset_days)
    except OverflowError:
        raise ValueError(f"сдвиг уводит дату за пределы календаря: {offset_days}")
    время = parse_time_of_day(time_of_day)
    return день if время is None else datetime.combine(день, время)


def control_text(moment):
    """Момент контроля в том виде, в котором его понимает движок. Пробел между
    датой и временем обязателен — на «T» разбор ввода спотыкается."""
    if isinstance(moment, datetime):
        return moment.strftime("%Y-%m-%d %H:%M")
    return moment.strftime("%Y-%m-%d")


# --- проверка --------------------------------------------------------------

def validate_template(data, existing_names=()):
    """Проверка шаблона до сохранения. Возвращает список ошибок по полям.

    Список, а не первое попавшееся исключение: форме надо подсветить все
    проблемные места разом, а не гонять человека по кругу. Имена полей совпадают
    с путями во входных данных (`steps.1.offset_days`), чтобы форма нашла нужный
    ввод, ничего не переводя.

    Предел длины и список запрещённых символов берутся у движка константами:
    название шаблона становится названием задачи, то есть именем файла в вольте.
    Второй такой список разошёлся бы с первым, и шаблон начал бы выдавать задачи,
    которые движок отказывается записать.
    """
    errors = []

    name = str(data.get("name") or "").strip()
    if not name:
        errors.append({"field": "name", "error": "Название шаблона обязательно"})
    elif len(name) > engine.MAX_TITLE:
        errors.append({"field": "name",
                       "error": f"Название длиннее {engine.MAX_TITLE} символов"})
    elif set(name) & engine.FORBIDDEN_IN_NAME:
        плохие = "".join(sorted(set(name) & engine.FORBIDDEN_IN_NAME))
        errors.append({"field": "name",
                       "error": f"В названии нельзя символы {плохие} — "
                                f"из шаблона получится имя файла"})
    elif any(name.lower() == str(n).strip().lower() for n in existing_names):
        errors.append({"field": "name", "error": "Шаблон с таким названием уже есть"})
    elif name != name.strip(". "):
        # Правило переписано с engine.validate_new_task: константы для него
        # движок не отдаёт, а сам метод требует уже развёрнутую задачу с датами.
        # Совпадение сторожит тест «имя с точкой отвергается как и в движке».
        # Без правила шаблон «Отчёт за квартал.» сохраняется, а созданная из него
        # задача падает у движка — через два экрана после опечатки.
        errors.append({"field": "name",
                       "error": "Название не должно кончаться точкой или пробелом"})

    tags = data.get("tags") or []
    if not isinstance(tags, (list, tuple)):
        errors.append({"field": "tags", "error": "Теги задаются списком строк"})
        tags = []
    for i, tag in enumerate(tags):
        if not isinstance(tag, str) or not tag.strip():
            errors.append({"field": f"tags.{i}", "error": "Пустой тег"})

    steps = data.get("steps") or []
    if not isinstance(steps, (list, tuple)):
        # Проверка стоит на границе с JSON, куда шаблон приходит из формы и из
        # бота. Форма, приславшая шаги объектом вместо списка, должна получить
        # ошибку по полю, а не AttributeError из середины расчёта.
        errors.append({"field": "steps", "error": "Шаги задаются списком"})
        steps = []
    if not steps:
        errors.append({"field": "steps", "error": "В шаблоне нужен хотя бы один шаг"})

    занятые_позиции = {}
    предыдущий = None      # (сдвиг, время) предыдущего шага — для проверки порядка
    for i, шаг in enumerate(steps):
        поле = f"steps.{i}"
        if not isinstance(шаг, dict):
            errors.append({"field": поле, "error": "Шаг задаётся полями, а не строкой"})
            continue
        название = str(шаг.get("title") or "").strip()
        if not название:
            errors.append({"field": f"{поле}.title", "error": "Название шага обязательно"})

        сдвиг = None
        try:
            сдвиг = parse_offset(шаг.get("offset_days"))
        except (ValueError, TypeError):
            errors.append({"field": f"{поле}.offset_days",
                           "error": "Сдвиг задаётся целым числом дней"})
        else:
            if сдвиг < 0:
                errors.append({"field": f"{поле}.offset_days",
                               "error": "Сдвиг не может быть отрицательным — "
                                        "шаг оказался бы раньше даты старта"})
                сдвиг = None
            elif сдвиг > MAX_OFFSET_DAYS:
                errors.append({"field": f"{поле}.offset_days",
                               "error": f"Сдвиг больше {MAX_OFFSET_DAYS} дней — "
                                        f"похоже, в поле сдвига набрана дата"})
                сдвиг = None

        время = None
        try:
            время = parse_time_of_day(шаг.get("time_of_day"))
        except (ValueError, TypeError):
            errors.append({"field": f"{поле}.time_of_day",
                           "error": "Время контроля пишется как 14:00"})

        # Шаги идут по очереди: следующий становится активным, когда закрыт
        # предыдущий. Сдвиг назад означает контроль по шагу, до которого очередь
        # ещё не дошла, — заказчик увидит его дату в прошлом с первого дня.
        # У шага без времени контроль на весь день, и «раньше или позже» внутри
        # одного дня для него не определено — тогда сравниваем только дни.
        if сдвиг is not None:
            if предыдущий is not None:
                прошлый_сдвиг, прошлое_время = предыдущий
                назад = сдвиг < прошлый_сдвиг or (
                    сдвиг == прошлый_сдвиг and время is not None
                    and прошлое_время is not None and время < прошлое_время)
                if назад:
                    errors.append({"field": f"{поле}.offset_days",
                                   "error": "Шаг назначен раньше предыдущего, "
                                            "а шаги идут по очереди"})
            предыдущий = (сдвиг, время)

        позиция = шаг.get("position")
        if позиция is not None:
            try:
                позиция = int(позиция)
            except (ValueError, TypeError):
                errors.append({"field": f"{поле}.position",
                               "error": "Позиция шага — целое число"})
                continue
            if позиция < 1:
                errors.append({"field": f"{поле}.position",
                               "error": "Позиция шага считается с единицы"})
            elif позиция in занятые_позиции:
                # В базе шаги лягут строками с номером позиции и читаться будут
                # с сортировкой по ней. Два шага с одной позицией — это порядок
                # шагов на усмотрение движка базы, то есть каждый раз новый.
                errors.append({"field": f"{поле}.position",
                               "error": f"Позиция {позиция} уже занята шагом "
                                        f"«{занятые_позиции[позиция]}»"})
            else:
                занятые_позиции[позиция] = название
    return errors


def normalize_template(data):
    """Шаблон к каноническому виду: строки очищены, сдвиги числа, позиции плотные.

    Хранится и считается только такой вид, поэтому разбор пользовательского ввода
    живёт в одном месте, а не в каждой функции по копии. Вызывать после
    `validate_template`: то, что она забраковала бы, здесь бросит ValueError.

    Номера позиций проставляются заново по порядку списка, а присланные из формы
    не сохраняются. `expand` и `preview` идут по списку, а из базы шаги будут
    читаться с сортировкой по позиции: оставь шаблон с позициями 3 и 1 в таком
    порядке — и предпросмотр покажет один порядок шагов, а созданная задача
    получит другой.
    """
    # Строка итерируется по символам, поэтому tags="отчёт" молча превращались в
    # пять однобуквенных тегов. То же со steps: строка вместо списка шагов роняла
    # предпросмотр AttributeError вместо внятной ошибки поля. Докстрока обещает
    # ValueError на том, что забраковала бы валидация, — здесь это и происходит.
    сырые_теги = data.get("tags")
    if isinstance(сырые_теги, str):
        raise ValueError("теги задаются списком строк, а не строкой")
    сырые_шаги = data.get("steps")
    if isinstance(сырые_шаги, str):
        raise ValueError("шаги задаются списком объектов, а не строкой")

    steps = []
    for i, шаг in enumerate(сырые_шаги or [], start=1):
        if not isinstance(шаг, dict):
            raise ValueError(f"шаг {i}: ожидался объект, пришло {type(шаг).__name__}")
        steps.append({
            "position": i,
            "title": str(шаг.get("title") or "").strip(),
            "offset_days": parse_offset(шаг.get("offset_days")),
            "time_of_day": format_time_of_day(parse_time_of_day(шаг.get("time_of_day"))),
        })
    tags = [t.strip() for t in (сырые_теги or []) if isinstance(t, str) and t.strip()]
    return {
        "schema": SCHEMA,
        "name": str(data.get("name") or "").strip(),
        "tags": tags,
        "steps": steps,
        "body": str(data.get("body") or "").strip(),
    }


# --- развёртывание ---------------------------------------------------------

def expand(template, start_date, title=None):
    """Шаблон + дата старта → данные для `engine.build_task`.

    Идентификаторы шагов здесь не раздаются: их раздаёт движок, и второй
    нумератор рано или поздно разойдётся с первым. Название задачи по умолчанию
    берётся от шаблона, но его можно перебить: по разделу 5.6 между предпросмотром
    и созданием заказчик правит поля, и «Отчёт за август» из шаблона «Квартальный
    отчёт» — обычный случай.

    Списки копируются, а не отдаются наружу как есть: движок дальше правит шаги
    задачи, и общий список испортил бы сам шаблон.
    """
    шаблон = normalize_template(template)
    старт = as_start_date(start_date)
    steps = [{
        "title": шаг["title"],
        "control_date": control_text(
            control_moment(старт, шаг["offset_days"], шаг["time_of_day"])),
    } for шаг in шаблон["steps"]]
    return {
        "title": str(title).strip() if title else шаблон["name"],
        "tags": list(шаблон["tags"]),
        "steps": steps,
        "body": шаблон["body"],
    }


def preview(template, start_date, work=None):
    """Какие даты получатся, если стартовать в этот день. До создания задачи.

    Раздел 5.6 требует показать расчёт до создания, потому что ошибку в сдвиге
    видно только по датам: «через 10 дней» в шаблоне выглядит правдоподобно, а на
    календаре попадает на новогодние праздники.

    День недели отдаётся строкой, а не вычисляется в форме: по контракту оболочка
    не считает ничего, даже такое. Рядом идёт `show_at` — когда уведомление
    реально всплывёт. Дата контроля от этого не меняется, сдвигается только показ.

    Выходной день определяет `worktime.is_workday` по настройкам рабочего
    времени, а не номер дня недели. Считать здесь самим значило бы, что при
    включённой работе по выходным строка предпросмотра сама себе противоречит:
    `on_weekend: True` и `show_at` в ту же субботу. Настройки приходят
    параметром, потому что заказчик их меняет (R19, R28); без них берутся те же
    значения по умолчанию, что у движка и у повторений.
    """
    work = work or worktime.settings()
    шаблон = normalize_template(template)
    старт = as_start_date(start_date)
    строки = []
    for шаг in шаблон["steps"]:
        момент = control_moment(старт, шаг["offset_days"], шаг["time_of_day"])
        день = момент.date() if isinstance(момент, datetime) else момент
        строки.append({
            "position": шаг["position"],
            "title": шаг["title"],
            "offset_days": шаг["offset_days"],
            "control_date": control_text(момент),
            "weekday": WEEKDAYS_SHORT[день.weekday()],
            "on_weekend": not worktime.is_workday(день, work),
            "show_at": worktime.show_at(момент, work).isoformat(timespec="minutes"),
        })
    return строки


def template_from_task(meta, name=None):
    """«Сохранить как шаблон» из задачи (R22): даты контроля обратно в сдвиги.

    За ноль берётся самая ранняя дата контроля среди шагов, а не дата создания
    задачи и не дата первого шага. От даты создания сдвиги уехали бы на столько,
    на сколько задачу завели заранее. От первого шага — ушли бы в минус, если
    заказчик успел перенести первый шаг за второй, и шаблон не прошёл бы
    собственную проверку. Шаг без даты контроля наследует сдвиг предыдущего:
    в задаче он идёт следом, а взять дату ему всё равно неоткуда.

    Порядок шагов не чинится: если в задаче шаги перепутаны по датам,
    `validate_template` ткнёт в конкретный шаг, и заказчик увидит, где именно.
    """
    steps = meta.get("steps") or []
    разобранные = [split_control_value(шаг.get("control_date")) for шаг in steps]
    начало = min((d for d, _ in разобранные if d is not None), default=None)

    результат = []
    сдвиг = 0
    for i, шаг in enumerate(steps, start=1):
        дата, время = разобранные[i - 1]
        if дата is not None:
            сдвиг = (дата - начало).days
        результат.append({
            "position": i,
            "title": str(шаг.get("title") or "").strip(),
            "offset_days": сдвиг,
            "time_of_day": format_time_of_day(время),
        })
    return {
        "schema": SCHEMA,
        "name": str(name or meta.get("title") or "").strip(),
        "tags": list(meta.get("tags") or []),
        "steps": результат,
        "body": "",
    }


# --- хранение --------------------------------------------------------------

def same_name(one, other):
    """Имена сравниваем без регистра: «Квартальный отчёт» и «квартальный отчёт» —
    один шаблон, и второй перезаписывает первый, а не встаёт рядом. Задачи в
    вольте живут по тому же правилу, и различаться они не должны."""
    return str(one).strip().lower() == str(other).strip().lower()


def find(templates, name):
    for шаблон in templates:
        if same_name(шаблон.get("name"), name):
            return шаблон
    return None


def put(templates, template):
    """Сохранение по имени: повторное сохранение того же шаблона заменяет запись,
    а не добавляет вторую. Контракт требует идемпотентности — оболочка вправе
    повторить запрос после обрыва. Позиция в списке сохраняется, иначе правка
    одного шага гоняла бы шаблон в конец перечня."""
    результат = list(templates)
    for i, существующий in enumerate(результат):
        if same_name(существующий.get("name"), template.get("name")):
            результат[i] = template
            return результат
    результат.append(template)
    return результат


def drop(templates, name):
    return [t for t in templates if not same_name(t.get("name"), name)]


class Store:
    """Склад шаблонов: четыре операции и ничего про то, где они лежат.

    Здесь и проходит граница хранения и расчёта. Наследник добавляет два метода:
    `_load` отдаёт список шаблонов, `_commit` записывает его целиком. Проверку,
    приведение к каноническому виду, поиск по имени и замену записи делают чистые
    функции модуля над обычными списками, а `save` и `delete` их складывают.
    Своей арифметики над шаблонами у четвёрки нет, поэтому переезд на SQLite
    её не трогает.

    Расчёты, наоборот, про склад не знают: `expand` и `preview` принимают шаблон
    словарём, откуда он взялся — из файла, из базы или из формы — им безразлично.
    """

    def _load(self):
        raise NotImplementedError

    def _commit(self, templates):
        raise NotImplementedError

    def all(self):
        return self._load()

    def get(self, name):
        return find(self._load(), name)

    def save(self, data):
        """Сохранить шаблон. Проверка здесь, а не у вызывающего: путь записи один,
        и мимо него испорченный шаблон в хранилище не попадёт."""
        текущие = self._load()
        имя = str(data.get("name") or "").strip()
        чужие = [t.get("name") for t in текущие if not same_name(t.get("name"), имя)]
        errors = validate_template(data, чужие)
        if errors:
            raise TemplateError(errors)
        шаблон = normalize_template(data)
        self._commit(put(текущие, шаблон))
        return шаблон

    def delete(self, name):
        """Удаление идемпотентно: удалять нечего — не ошибка, а False. Повтор
        после обрыва связи не должен ломаться на втором заходе."""
        текущие = self._load()
        осталось = drop(текущие, name)
        if len(осталось) == len(текущие):
            return False
        self._commit(осталось)
        return True


class MemoryStore(Store):
    """Хранение в памяти: для тестов и для предпросмотра, когда шаблон ещё не
    сохранён. Копии наружу отдаются намеренно — иначе вызывающий поправит шаг в
    выданном словаре и незаметно изменит хранилище."""

    def __init__(self, templates=()):
        self._templates = [normalize_template(t) for t in templates]

    def _load(self):
        return copy.deepcopy(self._templates)

    def _commit(self, templates):
        self._templates = copy.deepcopy(templates)


def templates_path(vault):
    """Файл шаблонов лежит в вольте на виду, а не скрытым служебным: это данные
    заказчика, они уходят в бэкап и в выгрузку вместе с задачами."""
    return Path(vault) / "Шаблоны.json"


class JsonStore(Store):
    """Хранение в одном JSON-файле.

    JSON, а не markdown с frontmatter, по двум причинам. Шаблон целиком пишет
    программа: свободного текста, ради которого заказчик открыл бы файл в
    Obsidian, в нём нет. И запись «имя, теги, список шагов со сдвигом» ложится в
    таблицу SQLite один в один, поэтому переезд не потребует переразбора текста.
    Разобрать frontmatter было бы чем — движок читает им весь вольт, — но
    заголовок заметки без самой заметки только добавляет слой над теми же полями.
    """

    def __init__(self, vault):
        self.path = templates_path(vault)

    def _load(self):
        if not self.path.is_file():
            return []
        try:
            сырое = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            # В отличие от файла доставки, битый файл шаблонов не заменяется
            # пустым молча: пустой список означал бы, что первое же сохранение
            # затрёт файл и шаблоны исчезнут насовсем. Лучше отказаться работать.
            raise TemplateError([{"field": None,
                                  "error": f"файл шаблонов не читается: {e}"}])
        if not isinstance(сырое, dict) or not isinstance(сырое.get("templates"), list):
            raise TemplateError([{"field": None,
                                  "error": "файл шаблонов не в том формате"}])
        return сырое["templates"]

    def _commit(self, templates):
        """Атомарно: файл лежит в вольте, а вольт синхронизируется OneDrive и
        открыт в Obsidian. Дописывание на месте даёт обрезанный файл, то есть
        потерю всех шаблонов разом, а не одного.

        Повторы на PermissionError — из-за Windows: там os.replace ненадолго
        падает, если файл в этот момент держит антивирус или индексатор.
        """
        содержимое = {"schema": SCHEMA, "templates": templates}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                # ensure_ascii=False — иначе русские названия лягут в файл
                # экранированными последовательностями вида \\u041a, и починить
                # шаблон в блокноте станет нельзя. Кодировка задана явно: на
                # Windows локаль не UTF-8.
                json.dump(содержимое, f, ensure_ascii=False, indent=1)
            for попытка in range(5):
                try:
                    os.replace(tmp, self.path)
                    break
                except PermissionError:
                    if попытка == 4:
                        raise
                    time.sleep(0.2 * (попытка + 1))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
