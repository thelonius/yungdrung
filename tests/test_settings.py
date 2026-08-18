#!/usr/bin/env python3
"""Тесты settings.py — хранение и валидация настроек, требование R28.

Ожидаемые значения посчитаны руками, не получены из самого модуля — иначе тест
проверял бы, что код согласен с собой (тот же принцип, что в test_worktime.py).

Все вызовы load/save и производных функций получают явный `path` внутрь
`tmp_path`: у settings.py есть дефолтный путь рядом с настоящим вольтом
проекта, и тест не должен случайно писать туда.
"""
import json
import sys
from datetime import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import settings  # noqa: E402
import worktime  # noqa: E402


@pytest.fixture
def path(tmp_path):
    return tmp_path / "Настройки.json"


# --- дефолты -----------------------------------------------------------------

def test_рабочие_часы_по_умолчанию_взяты_из_worktime_а_не_задублированы():
    """Требование задания: 09:00–21:00 не должны жить в settings.py вторым
    числом рядом с worktime.DEFAULT."""
    d = settings.defaults()
    assert d["notifications"]["start"] == worktime.DEFAULT["start"]
    assert d["notifications"]["end"] == worktime.DEFAULT["end"]
    assert d["notifications"]["weekends"] == worktime.DEFAULT["weekends"]


def test_дефолты_валидны_сами_по_себе():
    result = settings.validate(settings.defaults())
    assert result == {"errors": [], "warnings": []}


def test_defaults_возвращает_независимые_копии():
    """Правка того, что вернул defaults(), не должна протухать в модуле —
    иначе один тест испортит все последующие в этом же процессе."""
    d1 = settings.defaults()
    d1["reasons"].append({"name": "подсунутое", "archived": False})
    d1["notifications"]["repeat_minutes"] = 999
    d2 = settings.defaults()
    assert d2["reasons"] == []
    assert d2["notifications"]["repeat_minutes"] == 15


def test_нет_файла_работаем_на_дефолтах(path):
    assert settings.load(path) == settings.defaults()


# --- валидация: рабочие часы (переиспользование правила из worktime) --------

def test_перевёрнутый_интервал_ловится_через_worktime_settings():
    data = settings.defaults()
    data["notifications"]["start"] = "21:00"
    data["notifications"]["end"] = "09:00"
    result = settings.validate(data)
    поля = {e["field"] for e in result["errors"]}
    assert "notifications.end" in поля
    # то же сообщение, что кидает worktime.settings — не отдельная копия правила
    assert any("раньше конца" in e["error"] for e in result["errors"])


def test_время_строкой_и_объектом_time_равнозначны():
    data = settings.defaults()
    data["notifications"]["start"] = "10:00"
    assert settings.validate(data) == {"errors": [], "warnings": []}
    assert settings.parse_time_of_day("10:00") == time(10, 0)
    assert settings.parse_time_of_day(time(10, 0)) == time(10, 0)


def test_битый_формат_времени_даёт_ошибку_по_полю():
    data = settings.defaults()
    data["notifications"]["start"] = "10 утра"
    result = settings.validate(data)
    assert result["errors"] == [
        {"field": "notifications.start", "error": "неверный формат времени: '10 утра', ожидается ЧЧ:ММ"}]


# --- валидация: числовые границы ---------------------------------------------

@pytest.mark.parametrize("значение, ок", [
    (settings.REPEAT_MINUTES_MIN, True),
    (settings.REPEAT_MINUTES_MAX, True),
    (settings.REPEAT_MINUTES_MIN - 1, False),
    (settings.REPEAT_MINUTES_MAX + 1, False),
    (0, False),
    (True, False),  # bool — подкласс int, но не число минут
])
def test_границы_повтора_уведомления(значение, ок):
    data = settings.defaults()
    data["notifications"]["repeat_minutes"] = значение
    result = settings.validate(data)
    assert (result["errors"] == []) is ок


@pytest.mark.parametrize("значение, ок", [
    (settings.BACKUP_FREQUENCY_MIN_HOURS, True),
    (settings.BACKUP_FREQUENCY_MAX_HOURS, True),
    (settings.BACKUP_FREQUENCY_MIN_HOURS - 1, False),
    (settings.BACKUP_FREQUENCY_MAX_HOURS + 1, False),
])
def test_границы_частоты_бэкапа(значение, ок):
    data = settings.defaults()
    data["backup"]["frequency_hours"] = значение
    result = settings.validate(data)
    assert (result["errors"] == []) is ок


@pytest.mark.parametrize("значение, ок", [
    (settings.BACKUP_KEEP_MIN, True),
    (settings.BACKUP_KEEP_MAX, True),
    (0, False),
    (settings.BACKUP_KEEP_MAX + 1, False),
])
def test_границы_числа_копий_бэкапа(значение, ок):
    data = settings.defaults()
    data["backup"]["keep_count"] = значение
    result = settings.validate(data)
    assert (result["errors"] == []) is ок


@pytest.mark.parametrize("значение, ок", [(1, True), (4, True), (0, False), (-1, False)])
def test_минимальная_длина_совпадения_положительное_целое(значение, ок):
    data = settings.defaults()
    data["kb"]["min_match_length"] = значение
    result = settings.validate(data)
    assert (result["errors"] == []) is ок


def test_ошибки_подсвечивают_несколько_полей_разом():
    """Форма должна суметь показать все проблемы сразу — как validate_new_task
    в engine.py, а не гонять человека по кругу."""
    data = settings.defaults()
    data["notifications"]["repeat_minutes"] = 0
    data["backup"]["keep_count"] = -1
    data["kb"]["min_match_length"] = 0
    result = settings.validate(data)
    поля = {e["field"] for e in result["errors"]}
    assert поля == {"notifications.repeat_minutes", "backup.keep_count", "kb.min_match_length"}


# --- цвет тега ----------------------------------------------------------------

@pytest.mark.parametrize("цвет, ок", [
    ("#4a90d9", True), ("#fff", True), ("#FFFFFF", True),
    ("red", True), ("RED", True), ("gray", True),
    ("greenish", False), ("#12", False), ("#gggggg", False), (None, False), (123, False),
])
def test_is_valid_color(цвет, ок):
    assert settings.is_valid_color(цвет) is ок


# --- атомарная запись и перечитывание -----------------------------------------

def test_настройки_переживают_запись_и_чтение(path):
    data = settings.defaults()
    data["notifications"]["start"] = "10:30"
    data["hotkey"] = "Ctrl+Alt+Q"
    settings.save(data, path)
    заново = settings.load(path)
    assert заново["notifications"]["start"] == time(10, 30)
    assert заново["hotkey"] == "Ctrl+Alt+Q"


def test_файл_настроек_читается_человеком(path):
    """UTF-8 без экранирования — как у Шаблоны.json: русские названия причин
    и тегов не должны превращаться в \\u041f."""
    settings.add_reason("не дозвонился", path=path)
    сырое = path.read_text(encoding="utf-8")
    assert "не дозвонился" in сырое
    assert json.loads(сырое)["schema"] == settings.SCHEMA


def test_время_на_диске_лежит_строкой_чч_мм(path):
    settings.save(settings.defaults(), path)
    сырое = json.loads(path.read_text(encoding="utf-8"))
    assert сырое["notifications"]["start"] == "09:00"
    assert сырое["notifications"]["end"] == "21:00"


def test_невалидные_настройки_не_сохраняются(path):
    data = settings.defaults()
    data["notifications"]["repeat_minutes"] = 0
    with pytest.raises(settings.SettingsError):
        settings.save(data, path)
    assert not path.exists()


def test_битый_файл_настроек_не_затирается_молча(path):
    path.write_text("{это не json", encoding="utf-8")
    with pytest.raises(settings.SettingsError):
        settings.load(path)
    with pytest.raises(settings.SettingsError):
        settings.add_reason("что угодно", path=path)
    assert path.read_text(encoding="utf-8") == "{это не json"


def test_схема_новее_чем_понимает_модуль_отвергается(path):
    path.write_text(json.dumps({"schema": 99}), encoding="utf-8")
    with pytest.raises(settings.SettingsError) as exc:
        settings.load(path)
    assert exc.value.errors[0]["field"] == "schema"


def test_неизвестное_поле_в_файле_тихо_отбрасывается(path):
    сырое = {"schema": 1, "notifications": {}, "unknown_future_field": "мусор"}
    path.write_text(json.dumps(сырое, ensure_ascii=False), encoding="utf-8")
    data = settings.load(path)
    assert "unknown_future_field" not in data


# --- справочник причин --------------------------------------------------------

def test_добавление_причины(path):
    причины, warnings = settings.add_reason("не дозвонился", path=path)
    assert причины == [{"name": "не дозвонился", "archived": False}]
    assert warnings == []


def test_дубликат_активной_причины_отвергается(path):
    settings.add_reason("не было денег", path=path)
    with pytest.raises(settings.SettingsError) as exc:
        settings.add_reason("не было денег", path=path)
    assert exc.value.errors[0]["field"] == "name"


def test_дубликат_причины_нечувствителен_к_регистру(path):
    settings.add_reason("Моя лень", path=path)
    with pytest.raises(settings.SettingsError):
        settings.add_reason("моя лень", path=path)


def test_архивная_причина_не_мешает_новой_с_тем_же_именем(path):
    settings.add_reason("временно", path=path)
    settings.archive_reason("временно", path=path)
    причины, _ = settings.add_reason("временно", path=path)
    активные = [r for r in причины if not r["archived"]]
    assert len(активные) == 1


def test_архивирование_причины_идемпотентно(path):
    """Операции идемпотентны — правило из КОНТРАКТ.md: повторный вызов после
    обрыва связи не должен считаться ошибкой и не должен ничего ломать."""
    settings.add_reason("жду ответа", path=path)
    settings.archive_reason("жду ответа", path=path)
    причины, warnings = settings.archive_reason("жду ответа", path=path)
    assert причины == [{"name": "жду ответа", "archived": True}]
    assert warnings == []


def test_архивная_причина_не_предлагается_при_выборе_новой(path):
    settings.add_reason("активная", path=path)
    settings.add_reason("устаревшая", path=path)
    settings.archive_reason("устаревшая", path=path)
    assert settings.active_reason_names(path=path) == ["активная"]
    assert {r["name"] for r in settings.list_reasons(path=path)} == {"активная", "устаревшая"}


def test_архивирование_несуществующей_причины_ошибка(path):
    with pytest.raises(settings.SettingsError):
        settings.archive_reason("такой не было", path=path)


def test_переименование_причины(path):
    settings.add_reason("не было времени", path=path)
    причины, _ = settings.rename_reason("не было времени", "не хватило времени", path=path)
    assert причины == [{"name": "не хватило времени", "archived": False}]


def test_переименование_в_занятое_активное_имя_отвергается(path):
    settings.add_reason("а", path=path)
    settings.add_reason("б", path=path)
    with pytest.raises(settings.SettingsError):
        settings.rename_reason("а", "б", path=path)


def test_переименование_несуществующей_причины_ошибка(path):
    with pytest.raises(settings.SettingsError):
        settings.rename_reason("такой не было", "новое", path=path)


def test_пустое_новое_имя_причины_отвергается(path):
    settings.add_reason("причина", path=path)
    with pytest.raises(settings.SettingsError):
        settings.rename_reason("причина", "   ", path=path)


# --- теги -----------------------------------------------------------------

def test_добавление_тега(path):
    теги, warnings = settings.add_tag("срочное", "#ff0000", pinned=True, path=path)
    assert теги == [{"name": "срочное", "color": "#ff0000", "pinned": True}]
    assert warnings == []


def test_добавление_тега_с_именованным_цветом(path):
    теги, _ = settings.add_tag("быт", "blue", path=path)
    assert теги[0]["color"] == "blue"
    assert теги[0]["pinned"] is False


def test_добавление_тега_с_плохим_цветом_отвергается(path):
    with pytest.raises(settings.SettingsError) as exc:
        settings.add_tag("тег", "непонятный-цвет", path=path)
    assert exc.value.errors[0]["field"] == "color"


def test_дубликат_тега_отвергается(path):
    settings.add_tag("быт", "blue", path=path)
    with pytest.raises(settings.SettingsError):
        settings.add_tag("Быт", "red", path=path)


def test_переключение_закрепления_тега(path):
    settings.add_tag("быт", "blue", pinned=False, path=path)
    теги, _ = settings.toggle_tag_pinned("быт", path=path)
    assert теги[0]["pinned"] is True
    теги, _ = settings.toggle_tag_pinned("быт", path=path)
    assert теги[0]["pinned"] is False


def test_переименование_тега(path):
    settings.add_tag("быт", "blue", path=path)
    теги, _ = settings.rename_tag("быт", "бытовое", path=path)
    assert теги == [{"name": "бытовое", "color": "blue", "pinned": False}]


def test_слияние_тегов_убирает_источник_и_сохраняет_цель(path):
    """Раздел 6.8: слияние A в B — A исчезает из списка. Метаданные A (цвет,
    закрепление) при этом не переносятся: остаются ровно те, что были у B."""
    settings.add_tag("срочно", "red", pinned=True, path=path)
    settings.add_tag("важно", "blue", pinned=False, path=path)
    теги, warnings = settings.merge_tags("срочно", "важно", path=path)
    assert теги == [{"name": "важно", "color": "blue", "pinned": False}]
    assert warnings == []


def test_слияние_тега_самого_с_собой_отвергается(path):
    settings.add_tag("быт", "blue", path=path)
    with pytest.raises(settings.SettingsError) as exc:
        settings.merge_tags("быт", "Быт", path=path)
    assert exc.value.errors[0]["field"] == "target"


def test_слияние_с_несуществующим_тегом_отвергается(path):
    settings.add_tag("быт", "blue", path=path)
    with pytest.raises(settings.SettingsError) as exc:
        settings.merge_tags("быт", "нет такого", path=path)
    assert exc.value.errors[0]["field"] == "target"
    with pytest.raises(settings.SettingsError) as exc:
        settings.merge_tags("нет такого", "быт", path=path)
    assert exc.value.errors[0]["field"] == "source"


def test_merge_tags_не_знает_про_задачи_только_про_список():
    """Реальная миграция тега на самих задачах — вне этого модуля (docstring
    merge_tags и модульный docstring settings.py оговаривают это явно): он
    ничего не знает о хранилище задач и не должен."""
    assert "миграция" in settings.merge_tags.__doc__


# --- горячая клавиша -----------------------------------------------------------

@pytest.mark.parametrize("комбинация", ["Ctrl+Alt+Y", "Ctrl+Y", "Shift+Alt+Win+9", "Alt+F4"])
def test_валидные_комбинации_без_ошибок(комбинация):
    data = settings.defaults()
    data["hotkey"] = комбинация
    result = settings.validate(data)
    assert result["errors"] == []


@pytest.mark.parametrize("комбинация", ["Y", "Ctrl+", "Ctrl+Y+Z", "Ctrl+Ctrl+Y", "打+Y"])
def test_невалидный_формат_комбинации_ошибка(комбинация):
    data = settings.defaults()
    data["hotkey"] = комбинация
    result = settings.validate(data)
    поля = {e["field"] for e in result["errors"]}
    assert "hotkey" in поля


@pytest.mark.parametrize("комбинация", [None, "", "   "])
def test_пустая_комбинация_предупреждение_а_не_ошибка(комбинация):
    data = settings.defaults()
    data["hotkey"] = комбинация
    result = settings.validate(data)
    assert result["errors"] == []
    assert any(w["field"] == "hotkey" for w in result["warnings"])


@pytest.mark.parametrize("комбинация", [
    "Ctrl+Alt+Delete", "Ctrl+Alt+Del", "Ctrl+Shift+Esc", "Alt+F4", "Ctrl+Esc", "Win+L",
])
def test_зарезервированная_комбинация_предупреждение_а_не_ошибка(комбинация):
    data = settings.defaults()
    data["hotkey"] = комбинация
    result = settings.validate(data)
    assert result["errors"] == []
    assert any(w["field"] == "hotkey" for w in result["warnings"])


def test_предупреждения_доходят_до_вызывающего_через_save(path):
    data = settings.defaults()
    data["hotkey"] = ""
    warnings = settings.save(data, path)
    assert any(w["field"] == "hotkey" for w in warnings)
    # предупреждение не блокирует запись
    assert path.exists()


# --- путь по умолчанию не совпадает с настоящим вольтом проекта -------------

def test_дефолтный_путь_не_настоящий_вольт_проекта():
    """Явная защита от типичной ошибки теста: если кто-то забудет передать
    `path`, дефолт не должен незаметно указывать на файл настоящего проекта
    (совпасть могло бы, если бы кто-то по ошибке передал YUNGDRUNG_VAULT в
    тестовое окружение) — на момент запуска теста файла там не должно
    появиться от вызовов из этого модуля."""
    p = settings.settings_path()
    assert p.name == "Настройки.json"
    assert p == settings.VAULT / "Настройки.json"
