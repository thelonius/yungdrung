#!/usr/bin/env python3
"""Настройки продукта. Требование R28, раздел 6.8 ТЗ.

Раздел 6.8 перечисляет семь групп настроек: уведомления (рабочие часы, выходные,
звук, повтор), автозапуск и сворачивание в трей, бэкап (папка, частота, число
копий), справочник причин переноса, теги (цвет, закрепление, слияние), база
знаний (автораспознавание, минимальная длина совпадения) и горячая клавиша
быстрого ввода.

`autostart` и `minimize_to_tray` из модели убраны сознательно (сверка покрытия
2026-08-24): оба ключа лежали в файле мёртвыми и не могли ожить при нашей
архитектуре. Трей не заводим вовсе — решение записано в `CLAUDE.md`, единственная
работа трея заменена Планировщиком Windows. «Автозапуск» в этой схеме не тумблер
в приложении, а факт установки: задача Планировщика (`remind.py` каждые 5 минут)
переживает перезагрузку сама по себе, потому что так работают задачи Планировщика,
а не потому что кто-то включил галочку в настройках уже запущенной программы —
самой программы, которая могла бы прочитать этот флаг при старте системы, здесь
нет. Переключатель без пути к действию хуже отсутствующего переключателя: значит
человеку, что что-то произойдёт, и оставляет его наедине с тем, что не произошло.

Это конфигурация продукта, а не документ заказчика: в отличие от файлов в
`Задачи/`, эти данные не открывают в Obsidian и не читают глазами. Поэтому
файл лежит в корне вольта, а не внутри `Задачи/`, и в остальном ничем не
отличается от `Шаблоны.json` из templates.py — тот же приём: одна вычисляющая
половина (чистые функции над словарями и списками) и одна хранящая (чтение и
атомарная запись целиком).

`engine` этот модуль намеренно не импортирует, хотя мог бы взять оттуда
`VAULT` и стартовый список причин. Импорт `engine` тянет за собой pyyaml
(engine.py делает sys.exit при его отсутствии) — совершенно ненужную
зависимость для модуля, который не читает ни одного markdown-файла. Путь к
вольту settings.py вычисляет тем же способом независимо (переменная
окружения YUNGDRUNG_VAULT), а `worktime` — единственный модуль проекта,
который сюда действительно можно и нужно импортировать: он уже содержит
правило «начало рабочего дня раньше конца» и дефолты рабочих часов, дублировать
их здесь было бы прямым нарушением задания.

Ошибки — списком по полям, как `validate_new_task` в engine.py: форма должна
подсветить все проблемные места разом, а не гонять человека по кругу за раз
за разом. Предупреждения (пустая или системная горячая клавиша) — отдельным
списком: они не блокируют сохранение.

О чём этот модуль не может знать и не выдумывает:
  - занята ли горячая клавиша другой программой — это видно только ОС;
  - куда физически переехать тег при слиянии на самих задачах — это знает
    только хранилище задач (engine.py), а не этот модуль. См. docstring
    `merge_tags`.
"""
import copy
import json
import os
import re
import tempfile
import time
from datetime import datetime, time as dtime
from pathlib import Path

import worktime

SCHEMA = 1
SETTINGS_FILENAME = "Настройки.json"

# Тот же способ определить вольт, что и в engine.py (переменная окружения,
# иначе — папка рядом со скриптом). Не импортируем его оттуда: engine.py
# требует pyyaml на импорте, а этому модулю yaml не нужен вовсе.
VAULT = Path(os.environ.get("YUNGDRUNG_VAULT", Path(__file__).resolve().parent))

# --- границы полей ----------------------------------------------------------
# Раздел 6.8: «интервал повтора... в разумных пределах (не пять секунд, не
# сутки)». Нижняя граница отсекает то, что по сути спам; верхняя — заметно
# меньше суток (1440 минут), потому что повтор напоминания раз в несколько
# часов уже не работает как повтор.
REPEAT_MINUTES_MIN = 1
REPEAT_MINUTES_MAX = 180

# Частота бэкапа и число копий — своих чисел в ТЗ нет, только «в часах» и
# «число». Верхние границы нужны не как продуктовое требование, а как щит от
# опечатки (лишний ноль превращает «раз в сутки» в «раз в год» молча).
BACKUP_FREQUENCY_MIN_HOURS = 1
BACKUP_FREQUENCY_MAX_HOURS = 24 * 7   # реже раза в неделю бэкап теряет смысл
BACKUP_KEEP_MIN = 1
BACKUP_KEEP_MAX = 365

# Цвет тега: hex — точный цвет для тех, кому важно совпасть с фирменным; имя —
# для формы с кружочками-образцами, чтобы не заставлять нетехнического
# заказчика вводить #RRGGBB руками. Восемь имён — это уже весь обычный набор
# «цветных кружков», больше усложняет выбор, меньше — не хватает на все теги
# сразу.
COLOR_NAMES = {"red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink", "gray"}
HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Горячая клавиша: базовый формат «модификаторы через + и одна клавиша в
# конце». Win, а не Cmd — продукт для Windows-ноутбука заказчика. Список
# зарезервированных комбинаций даёт только предупреждение, не ошибку: он
# заведомо неполон (что реально занято, знает только ОС), а не пропустить
# человека дальше из-за нашего неполного списка — хуже, чем предупредить и
# оставить решение ему.
HOTKEY_MODIFIERS = ("Ctrl", "Alt", "Shift", "Win")
HOTKEY_FINAL_RE = re.compile(r"^[A-Za-z0-9]$|^F(1[0-2]|[1-9])$")
# Названные клавиши длиннее одного символа — ровно те, что нужны, чтобы
# зарезервированные комбинации ниже (Ctrl+Alt+Delete и подобные) вообще
# доходили до проверки на пересечение, а не отсеивались форматом раньше.
HOTKEY_NAMED_KEYS = {"delete", "del", "esc", "escape", "tab", "space", "enter"}
HOTKEY_RESERVED = {
    "ctrl+alt+delete", "ctrl+alt+del", "ctrl+shift+esc", "alt+f4", "ctrl+esc", "win+l",
}

# Значения по умолчанию. Рабочие часы и выходные — из worktime.DEFAULT, чтобы
# «09:00–21:00» не жило в проекте двумя числами, которые однажды разъедутся.
DEFAULTS = {
    "notifications": {
        "start": worktime.DEFAULT["start"],
        "end": worktime.DEFAULT["end"],
        "weekends": worktime.DEFAULT["weekends"],
        "sound": True,
        "repeat_minutes": 15,
    },
    "backup": {
        "folder": None,
        "frequency_hours": 24,
        "keep_count": 7,
    },
    # Перенесено из engine.REASONS при подключении модуля — тот список был
    # источником истины до сегодняшнего дня, теперь источник один, здесь.
    "reasons": [
        {"name": "жду ответа от другого человека", "archived": False},
        {"name": "не было денег", "archived": False},
        {"name": "не было времени", "archived": False},
        {"name": "передумал, надо переформулировать", "archived": False},
        {"name": "внешние обстоятельства", "archived": False},
        {"name": "не хватило информации", "archived": False},
        {"name": "моя лень", "archived": False},
    ],
    "tags": [],
    "kb": {
        "auto_recognition": True,
        "min_match_length": 4,
    },
    # Комбинация из примера в разделе 6.8 ТЗ — рабочий дефолт, а не заглушка.
    "hotkey": "Ctrl+Alt+Y",
}


class SettingsError(Exception):
    """Ошибка с разбором по полям — как TemplateError в templates.py.

    Несёт список словарей {"field": ..., "error": ...}, тот же список, что
    вернула бы `validate`. Оболочке достаточно отдать `e.errors` в JSON.
    """

    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(e.get("error", "") for e in errors))


# --- путь и (де)сериализация -------------------------------------------------

def settings_path(vault=None):
    """Файл настроек — в корне вольта, рядом с `Задачи/`, а не внутри неё:
    это конфигурация продукта, не документ заказчика (раздел 6.8 ТЗ)."""
    return Path(vault or VAULT) / SETTINGS_FILENAME


def parse_time_of_day(value):
    """Время суток из «ЧЧ:ММ» / «ЧЧ:ММ:СС» или уже готового datetime.time.

    Секунды на входе тоже принимаются: `datetime.time.isoformat()` отдаёт их,
    и не хочется падать, если что-то записало полный ISO вместо короткого
    формата поля ввода времени.
    """
    if isinstance(value, dtime):
        return value
    if isinstance(value, str):
        raw = value.strip()
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt).time()
            except ValueError:
                continue
    raise ValueError(f"неверный формат времени: {value!r}, ожидается ЧЧ:ММ")


def _merge(base, overrides):
    """Оверрайды поверх дефолтов, рекурсивно, только по известным ключам.

    Ключ, которого нет в дефолтах — опечатка в правленном руками файле или
    поле из будущей версии продукта, — тихо отбрасывается. Файл настроек от
    новой версии продукта не должен ронять старую копию модуля отказом или
    протаскивать в выдачу мусорное поле.
    """
    if not isinstance(overrides, dict):
        return copy.deepcopy(base)
    out = copy.deepcopy(base)
    for key, value in overrides.items():
        if key not in out:
            continue
        if isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _deserialize(raw):
    """JSON → внутреннее представление: строки времени становятся `time`.

    Если строка не разбирается — оставляем её как есть. Тихая подмена на
    дефолт здесь была бы потерей: `validate` увидит ту же строку и вернёт
    структурную ошибку по полю, а не проглотит поломку молча (тот же принцип,
    что у BROKEN в engine.py: молчаливая потеря опаснее заметного отказа).
    """
    out = copy.deepcopy(raw)
    notif = out.get("notifications")
    if isinstance(notif, dict):
        for field in ("start", "end"):
            if field in notif and isinstance(notif[field], str):
                try:
                    notif[field] = parse_time_of_day(notif[field])
                except ValueError:
                    pass
    return out


def _serialize(data):
    """Внутреннее представление → JSON: `time` становится строкой «ЧЧ:ММ»."""
    out = copy.deepcopy(data)
    notif = out.get("notifications")
    if isinstance(notif, dict):
        for field in ("start", "end"):
            v = notif.get(field)
            if isinstance(v, dtime):
                notif[field] = v.strftime("%H:%M")
    return out


# --- валидация ----------------------------------------------------------------

def _validate_reasons(reasons):
    errors = []
    if not isinstance(reasons, list):
        return [{"field": "reasons", "error": "Справочник причин — список"}]
    активные = {}
    for i, r in enumerate(reasons):
        поле = f"reasons.{i}"
        if not isinstance(r, dict):
            errors.append({"field": поле, "error": "Причина — объект с полями name и archived"})
            continue
        name = r.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if not name:
            errors.append({"field": f"{поле}.name", "error": "Название причины обязательно"})
            continue
        archived = r.get("archived", False)
        if not isinstance(archived, bool):
            errors.append({"field": f"{поле}.archived", "error": "Признак архива — да или нет"})
            archived = False
        if not archived:
            key = name.lower()
            if key in активные:
                errors.append({"field": f"{поле}.name",
                                "error": f"Причина «{name}» уже есть среди активных"})
            else:
                активные[key] = i
    return errors


def is_valid_color(value):
    """Hex (#4a90d9, #4a9) или одно из ограниченного набора имён — см. COLOR_NAMES
    и комментарий рядом с ним про то, почему ровно этот набор."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    return bool(HEX_COLOR_RE.match(v)) or v.lower() in COLOR_NAMES


def _validate_tags(tags):
    errors = []
    if not isinstance(tags, list):
        return [{"field": "tags", "error": "Список тегов — список"}]
    имена = {}
    for i, t in enumerate(tags):
        поле = f"tags.{i}"
        if not isinstance(t, dict):
            errors.append({"field": поле, "error": "Тег — объект с полями name, color, pinned"})
            continue
        name = t.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if not name:
            errors.append({"field": f"{поле}.name", "error": "Название тега обязательно"})
        else:
            key = name.lower()
            if key in имена:
                errors.append({"field": f"{поле}.name", "error": f"Тег «{name}» уже есть"})
            else:
                имена[key] = i
        if not is_valid_color(t.get("color")):
            errors.append({"field": f"{поле}.color",
                            "error": "Цвет — hex (#4a90d9) или одно из: "
                                     + ", ".join(sorted(COLOR_NAMES))})
        if "pinned" in t and not isinstance(t.get("pinned"), bool):
            errors.append({"field": f"{поле}.pinned", "error": "Закреплён — да или нет"})
    return errors


def _validate_hotkey(value):
    """Формат строки комбинации — то единственное, что можно проверить локально.

    Настоящую занятость комбинации другой программой видит только ОС; список
    HOTKEY_RESERVED — заведомо неполный минимум («Ctrl+Alt+Delete» и подобные),
    поэтому попадание в него — предупреждение, а не отказ. Пустая комбинация —
    тоже предупреждение: горячая клавиша необязательна, но пользователь должен
    об этом узнать, а не решить, что она просто ещё не сработала.
    """
    errors, warnings = [], []
    if value is None or (isinstance(value, str) and not value.strip()):
        warnings.append({"field": "hotkey",
                          "warning": "Комбинация не задана — быстрый ввод недоступен"})
        return errors, warnings
    if not isinstance(value, str):
        errors.append({"field": "hotkey", "error": "Комбинация — строка"})
        return errors, warnings

    части = [p.strip() for p in value.strip().split("+")]
    модификаторы, финал = части[:-1], части[-1]
    финал_ок = bool(HOTKEY_FINAL_RE.match(финал)) or финал.lower() in HOTKEY_NAMED_KEYS
    формат_ок = (
        len(части) >= 2
        and len(модификаторы) == len(set(модификаторы))
        and all(m in HOTKEY_MODIFIERS for m in модификаторы)
        and финал_ок
    )
    if not формат_ок:
        errors.append({"field": "hotkey",
                        "error": "Формат — модификаторы через + и клавиша, "
                                 "например Ctrl+Alt+Y"})
        return errors, warnings

    canonical = "+".join(модификаторы + [финал]).lower()
    if canonical in HOTKEY_RESERVED:
        warnings.append({"field": "hotkey", "warning": f"«{value}» обычно занята системой"})
    return errors, warnings


def validate(data):
    """Проверка настроек целиком. Возвращает {"errors": [...], "warnings": [...]}.

    Ошибки блокируют сохранение (см. `save`), предупреждения — нет. Оба списка —
    словари {"field": ..., "error"/"warning": ...}, как у `validate_new_task` в
    engine.py: форма подсвечивает все проблемные места разом.
    """
    errors, warnings = [], []

    # --- уведомления ---------------------------------------------------
    notif = data.get("notifications") or {}
    start_raw = notif.get("start", DEFAULTS["notifications"]["start"])
    end_raw = notif.get("end", DEFAULTS["notifications"]["end"])
    начало = конец = None
    try:
        начало = parse_time_of_day(start_raw)
    except ValueError as e:
        errors.append({"field": "notifications.start", "error": str(e)})
    try:
        конец = parse_time_of_day(end_raw)
    except ValueError as e:
        errors.append({"field": "notifications.end", "error": str(e)})
    if начало is not None and конец is not None:
        # Правило «начало раньше конца» уже живёт в worktime.settings —
        # переиспользуем его вместо своей копии того же условия.
        try:
            worktime.settings(start=начало, end=конец)
        except ValueError as e:
            errors.append({"field": "notifications.end", "error": str(e)})

    if "weekends" in notif and not isinstance(notif.get("weekends"), bool):
        errors.append({"field": "notifications.weekends", "error": "Выходные — да или нет"})
    if "sound" in notif and not isinstance(notif.get("sound"), bool):
        errors.append({"field": "notifications.sound", "error": "Звук — да или нет"})

    повтор = notif.get("repeat_minutes", DEFAULTS["notifications"]["repeat_minutes"])
    if (not isinstance(повтор, int) or isinstance(повтор, bool)
            or not (REPEAT_MINUTES_MIN <= повтор <= REPEAT_MINUTES_MAX)):
        errors.append({"field": "notifications.repeat_minutes",
                        "error": f"Повтор — целое число минут от {REPEAT_MINUTES_MIN} "
                                 f"до {REPEAT_MINUTES_MAX}"})

    # --- бэкап -------------------------------------------------------------
    backup = data.get("backup") or {}
    folder = backup.get("folder")
    if folder is not None and (not isinstance(folder, str) or not folder.strip()):
        errors.append({"field": "backup.folder", "error": "Папка — непустая строка пути"})

    частота = backup.get("frequency_hours", DEFAULTS["backup"]["frequency_hours"])
    if (not isinstance(частота, int) or isinstance(частота, bool)
            or not (BACKUP_FREQUENCY_MIN_HOURS <= частота <= BACKUP_FREQUENCY_MAX_HOURS)):
        errors.append({"field": "backup.frequency_hours",
                        "error": f"Частота бэкапа — целое число часов от "
                                 f"{BACKUP_FREQUENCY_MIN_HOURS} до {BACKUP_FREQUENCY_MAX_HOURS}"})

    копий = backup.get("keep_count", DEFAULTS["backup"]["keep_count"])
    if (not isinstance(копий, int) or isinstance(копий, bool)
            or not (BACKUP_KEEP_MIN <= копий <= BACKUP_KEEP_MAX)):
        errors.append({"field": "backup.keep_count",
                        "error": f"Число копий — целое от {BACKUP_KEEP_MIN} до {BACKUP_KEEP_MAX}"})

    # --- база знаний -------------------------------------------------------
    kb = data.get("kb") or {}
    if "auto_recognition" in kb and not isinstance(kb.get("auto_recognition"), bool):
        errors.append({"field": "kb.auto_recognition", "error": "Флаг — да или нет"})
    длина = kb.get("min_match_length", DEFAULTS["kb"]["min_match_length"])
    if not isinstance(длина, int) or isinstance(длина, bool) or длина <= 0:
        errors.append({"field": "kb.min_match_length",
                        "error": "Минимальная длина совпадения — положительное целое"})

    # --- справочники -------------------------------------------------------
    errors.extend(_validate_reasons(data.get("reasons") or []))
    errors.extend(_validate_tags(data.get("tags") or []))

    hk_errors, hk_warnings = _validate_hotkey(data.get("hotkey"))
    errors.extend(hk_errors)
    warnings.extend(hk_warnings)

    return {"errors": errors, "warnings": warnings}


# --- хранение -----------------------------------------------------------------

def defaults():
    """Глубокая копия настроек по умолчанию — чтобы вызывающий не мог
    испортить DEFAULTS, поправив вложенный словарь в возвращённой копии."""
    return copy.deepcopy(DEFAULTS)


def load(path=None):
    """Настройки с диска, дополненные дефолтами. Нет файла — работаем на
    дефолтах, ничего не падает (п. 2 задания).

    Без кэша: читаем файл при каждом обращении. Причины две. Во-первых, файл
    может подменить не этот модуль, а восстановление бэкапа (переезд файла
    целиком в обход settings.py) — кэш в этот момент начал бы молча врать.
    Во-вторых, объём в разделе 6.29 ТЗ маленький (один JSON на десяток полей),
    а частота обращений — по действию пользователя, не в цикле; экономить
    здесь миллисекунды нечего, а вот поймать баг с устаревшим кэшем на
    ноутбуке нетехнического заказчика — дороже любой экономии.

    Файл есть, но не читается или не в том формате — не подменяем его молча
    дефолтами (потерять правки пользователя тише, чем упасть, но тише не
    значит безопаснее) и поднимаем `SettingsError`, как `templates.JsonStore`
    делает для битого файла шаблонов.
    """
    p = Path(path) if path else settings_path()
    if not p.is_file():
        return defaults()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise SettingsError([{"field": None, "error": f"файл настроек не читается: {e}"}]) from e
    if not isinstance(raw, dict):
        raise SettingsError([{"field": None, "error": "файл настроек не в том формате"}])

    schema = raw.get("schema", SCHEMA)
    if not isinstance(schema, int) or schema > SCHEMA:
        raise SettingsError([{"field": "schema",
                               "error": f"файл настроек версии {schema!r} новее, чем "
                                        "понимает этот модуль"}])

    data = _merge(DEFAULTS, _deserialize(raw))
    result = validate(data)
    if result["errors"]:
        raise SettingsError(result["errors"])
    return data


def save(data, path=None):
    """Валидация + атомарная запись. Ошибки — исключением `SettingsError` (запись
    не происходит), предупреждения — возвращаются вызывающему списком.

    Атомарность — тот же приём, что `engine.write_file`: пишем во временный
    файл в той же папке и подменяем `os.replace`. Повторы на PermissionError —
    по той же причине: на Windows файл может на миг придержать антивирус,
    индексатор OneDrive или сам факт открытой формы настроек.
    """
    merged = _merge(DEFAULTS, data)
    result = validate(merged)
    if result["errors"]:
        raise SettingsError(result["errors"])

    p = Path(path) if path else settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": SCHEMA, **_serialize(merged)}
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # ensure_ascii=False — иначе названия причин и тегов по-русски лягут
            # в файл экранированными \uXXXX, и починить его в блокноте станет нельзя.
            json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=False)
        for attempt in range(5):
            try:
                os.replace(tmp, p)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2 * (attempt + 1))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return result["warnings"]


# --- справочник причин: add / rename / archive --------------------------------

def _find_reason(reasons, name, *, active_only=False):
    key = name.strip().lower()
    for r in reasons:
        if r["name"].strip().lower() == key and (not active_only or not r.get("archived")):
            return r
    return None


def _reasons_add(reasons, name):
    name = (name or "").strip()
    if not name:
        raise SettingsError([{"field": "name", "error": "Название причины обязательно"}])
    if _find_reason(reasons, name, active_only=True):
        raise SettingsError([{"field": "name",
                               "error": f"Причина «{name}» уже есть среди активных"}])
    return reasons + [{"name": name, "archived": False}]


def _reasons_rename(reasons, old_name, new_name):
    """Переименование меняет то, что предложат при следующем выборе. Уже
    записанные в журнал шага причины хранятся там буквальной строкой на момент
    события — этот модуль файлов задач не трогает, поэтому история не может
    «переехать» вслед за переименованием, даже если бы кто-то захотел."""
    new_name = (new_name or "").strip()
    if not new_name:
        raise SettingsError([{"field": "new_name", "error": "Новое название обязательно"}])
    target = _find_reason(reasons, old_name)
    if target is None:
        raise SettingsError([{"field": "name", "error": f"Причина «{old_name}» не найдена"}])
    занято = _find_reason(reasons, new_name, active_only=True)
    if занято is not None and занято is not target:
        raise SettingsError([{"field": "new_name",
                               "error": f"Причина «{new_name}» уже есть среди активных"}])
    return [{**r, "name": new_name} if r is target else r for r in reasons]


def _reasons_archive(reasons, name):
    """Архивная причина не удаляется и не предлагается при выборе новой
    (см. `active_reason_names`), но остаётся в списке — история её видит.
    Архивирование уже архивной причины не ошибка: операции идемпотентны
    (КОНТРАКТ.md), повтор после обрыва связи не должен пугать пользователя."""
    target = _find_reason(reasons, name)
    if target is None:
        raise SettingsError([{"field": "name", "error": f"Причина «{name}» не найдена"}])
    return [{**r, "archived": True} if r is target else r for r in reasons]


def add_reason(name, path=None):
    data = load(path)
    data["reasons"] = _reasons_add(data.get("reasons", []), name)
    return data["reasons"], save(data, path)


def rename_reason(old_name, new_name, path=None):
    data = load(path)
    data["reasons"] = _reasons_rename(data.get("reasons", []), old_name, new_name)
    return data["reasons"], save(data, path)


def archive_reason(name, path=None):
    data = load(path)
    data["reasons"] = _reasons_archive(data.get("reasons", []), name)
    return data["reasons"], save(data, path)


def list_reasons(path=None):
    """Все причины, включая архивные — для карточки истории и для самих
    настроек. Для выпадающего списка при выборе новой причины — см.
    `active_reason_names`."""
    return copy.deepcopy(load(path).get("reasons", []))


def active_reason_names(path=None):
    """Имена причин, которые можно предложить при выборе новой: архивные
    сюда не попадают (раздел 6.8 ТЗ)."""
    return [r["name"] for r in load(path).get("reasons", []) if not r.get("archived")]


# --- теги: add / toggle pinned / rename / merge --------------------------------

def _find_tag(tags, name):
    key = name.strip().lower()
    for t in tags:
        if t["name"].strip().lower() == key:
            return t
    return None


def _tags_add(tags, name, color, pinned=False):
    name = (name or "").strip()
    if not name:
        raise SettingsError([{"field": "name", "error": "Название тега обязательно"}])
    if _find_tag(tags, name):
        raise SettingsError([{"field": "name", "error": f"Тег «{name}» уже есть"}])
    if not is_valid_color(color):
        raise SettingsError([{"field": "color",
                               "error": "Цвет — hex (#4a90d9) или одно из: "
                                        + ", ".join(sorted(COLOR_NAMES))}])
    return tags + [{"name": name, "color": color, "pinned": bool(pinned)}]


def _tags_rename(tags, old_name, new_name):
    new_name = (new_name or "").strip()
    if not new_name:
        raise SettingsError([{"field": "new_name", "error": "Новое название обязательно"}])
    target = _find_tag(tags, old_name)
    if target is None:
        raise SettingsError([{"field": "name", "error": f"Тег «{old_name}» не найден"}])
    занято = _find_tag(tags, new_name)
    if занято is not None and занято is not target:
        raise SettingsError([{"field": "new_name", "error": f"Тег «{new_name}» уже есть"}])
    return [{**t, "name": new_name} if t is target else t for t in tags]


def _tags_toggle_pinned(tags, name):
    target = _find_tag(tags, name)
    if target is None:
        raise SettingsError([{"field": "name", "error": f"Тег «{name}» не найден"}])
    return [{**t, "pinned": not t.get("pinned", False)} if t is target else t for t in tags]


def _tags_merge(tags, source_name, target_name):
    if source_name.strip().lower() == target_name.strip().lower():
        raise SettingsError([{"field": "target", "error": "Нельзя слить тег сам с собой"}])
    source = _find_tag(tags, source_name)
    if source is None:
        raise SettingsError([{"field": "source", "error": f"Тег «{source_name}» не найден"}])
    target = _find_tag(tags, target_name)
    if target is None:
        raise SettingsError([{"field": "target", "error": f"Тег «{target_name}» не найден"}])
    return [t for t in tags if t is not source]


def add_tag(name, color, pinned=False, path=None):
    data = load(path)
    data["tags"] = _tags_add(data.get("tags", []), name, color, pinned)
    return data["tags"], save(data, path)


def rename_tag(old_name, new_name, path=None):
    data = load(path)
    data["tags"] = _tags_rename(data.get("tags", []), old_name, new_name)
    return data["tags"], save(data, path)


def toggle_tag_pinned(name, path=None):
    data = load(path)
    data["tags"] = _tags_toggle_pinned(data.get("tags", []), name)
    return data["tags"], save(data, path)


def merge_tags(source_name, target_name, path=None):
    """Слияние тега source в target на уровне справочника: source пропадает
    из списка тегов, target остаётся со своим цветом и закреплением — метаданные
    source при слиянии не переносятся и не усредняются, побеждает identity target.

    Модуль не знает, на каких задачах стоял source: список тегов и хранилище
    задач — разные вещи, а этот модуль ничего не знает о том, где и как задачи
    хранятся (см. модульный docstring). Тот, кто подключает settings.py к
    engine.py, обязан отдельным шагом пройтись по шагам/задачам и заменить
    source на target на самих записях — здесь эта миграция не выполняется.
    """
    data = load(path)
    data["tags"] = _tags_merge(data.get("tags", []), source_name, target_name)
    return data["tags"], save(data, path)


def list_tags(path=None):
    return copy.deepcopy(load(path).get("tags", []))
