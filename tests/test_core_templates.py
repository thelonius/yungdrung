#!/usr/bin/env python3
"""Тесты `core.templates` и `core.recur` напрямую, минуя `engine.py`-адаптер.

Сам расчёт (проверка полей, арифметика сдвигов, `due_cycles`) покрыт
`test_templates.py` и `test_recurrence.py`; здесь — то, что даёт прикладной
слой сверх него: контекст стора и `today` вызывающего вместо `engine.VAULT` и
системных часов, исключения ядра вместо `TemplateError`, настройки заказчика
в предпросмотре (риск 2 карты шаблонов), только листья в «сохранить как
шаблон» (риск 7).

`instantiate`/`run` зовут `core.tasks.create_task` и
`core.attachments.copy_template_to_task` (сигнатуры B1/B3, §3.3, §3.5
спецификации среза 2) напрямую — после слияния веток обе функции настоящие.
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402
import settings as cfg  # noqa: E402
from core import persist as core_persist  # noqa: E402
from core import recur as core_recur  # noqa: E402
from core import templates as core_templates  # noqa: E402
from core.context import Context  # noqa: E402
from core.errors import NotFound, ValidationError  # noqa: E402
from core.models_templates import TemplateIn  # noqa: E402


@pytest.fixture
def ctx(tmp_path):
    return Context(tmp_path)


def _template(name="Отчёт", **extra):
    return {"name": name, "steps": [{"title": "Собрать", "offset_days": 0}], **extra}


# --- риск 1: якорь повторения разбирается от today вызывающего -------------

def test_якорь_завтра_считается_от_today_вызывающего(ctx):
    """`today` вызывающего лежит в прошлом относительно реального календаря —
    «завтра» обязано разобраться от него, а не от системных часов (риск 1
    карты шаблонов, `map-templates.md`)."""
    вчера_по_календарю = date(2026, 1, 10)
    карточка = core_templates.save(ctx, {
        **_template(), "recurrence": {"anchor": "завтра", "freq": "daily"},
    }, вчера_по_календарю)
    assert карточка.recurrence.anchor == date(2026, 1, 11)


def test_нет_шаблона_даёт_NotFound(ctx):
    with pytest.raises(NotFound):
        core_templates.get(ctx, "Нет такого")


# --- риск 2: предпросмотр берёт выходные из настроек заказчика -------------

def test_preview_без_настроек_метит_субботу_выходным(ctx):
    старт = date(2026, 8, 3)  # понедельник; +5 = суббота 2026-08-08
    result = core_templates.preview(
        ctx, _template(**{"steps": [{"title": "Шаг", "offset_days": 5}]}), None, старт)
    assert result.ok
    assert result.steps[0].on_weekend is True


def test_preview_с_включённой_работой_по_выходным_не_метит_субботу(ctx):
    """Заказчик включил работу по выходным (`Настройки.json`) — суббота
    перестаёт быть выходным и в предпросмотре, иначе строка сама себе
    противоречила бы: `on_weekend: True` и показ в ту же субботу."""
    cfg.save({"notifications": {"weekends": True}}, ctx.settings_path())
    старт = date(2026, 8, 3)
    result = core_templates.preview(
        ctx, _template(**{"steps": [{"title": "Шаг", "offset_days": 5}]}), None, старт)
    assert result.ok
    assert result.steps[0].on_weekend is False


def test_preview_плохая_дата_даёт_ok_false(ctx):
    result = core_templates.preview(ctx, _template(), "чепуха", date(2026, 8, 3))
    assert not result.ok
    assert result.errors[0].field == "start"


# --- PUT: переименование через тело отвергается -----------------------------

def test_save_с_другим_именем_чем_путь_отвергается(ctx):
    core_templates.save(ctx, _template("Исходное"), date(2026, 1, 1))
    with pytest.raises(ValidationError) as excinfo:
        core_templates.save(ctx, _template("Другое"), date(2026, 1, 1),
                            expect_name="Исходное")
    assert excinfo.value.errors[0]["field"] == "name"


def test_save_по_несуществующему_имени_в_put_даёт_NotFound(ctx):
    with pytest.raises(NotFound):
        core_templates.save(ctx, _template("Новое"), date(2026, 1, 1),
                            expect_name="Нет такого")


# --- PUT без ключа recurrence не стирает сохранённое правило ----------------
# Находка ревью среза 2 (core/templates.py:66): `TemplateIn.model_dump(
# exclude_unset=True)` роняет отсутствующий в JSON ключ "recurrence" так же,
# как явный "recurrence": null — `normalize_template` дальше эти случаи не
# различает и стирал правило. Модель нужна настоящая (не dict), иначе
# `model_fields_set` неоткуда взять.

def test_put_без_ключа_recurrence_сохраняет_прежнее_правило(ctx):
    core_templates.save(ctx, TemplateIn(**_template(recurrence={
        "anchor": "2026-01-05", "freq": "weekly", "byweekday": [1],
    })), date(2026, 1, 1))

    # Форма правки шагов/тегов не несёт виджета повторения — ключа
    # "recurrence" в теле нет вовсе (`exclude_unset`, а не `None`).
    правка = TemplateIn.model_validate({"name": "Отчёт",
                                        "steps": [{"title": "Собрать", "offset_days": 0}]})
    assert "recurrence" not in правка.model_fields_set
    карточка = core_templates.save(ctx, правка, date(2026, 1, 1), expect_name="Отчёт")

    assert карточка.recurrence is not None
    assert карточка.recurrence.freq == "weekly"
    assert карточка.recurrence.byweekday == [1]


def test_put_с_recurrence_null_снимает_правило(ctx):
    """Отличие от предыдущего теста: явный `null` — это просьба снять цикл,
    а не забытое поле, и она обязана сработать по-прежнему."""
    core_templates.save(ctx, TemplateIn(**_template(recurrence={
        "anchor": "2026-01-05", "freq": "weekly", "byweekday": [1],
    })), date(2026, 1, 1))

    правка = TemplateIn.model_validate({"name": "Отчёт",
                                        "steps": [{"title": "Собрать", "offset_days": 0}],
                                        "recurrence": None})
    assert "recurrence" in правка.model_fields_set
    карточка = core_templates.save(ctx, правка, date(2026, 1, 1), expect_name="Отчёт")

    assert карточка.recurrence is None


# --- риск 7: «сохранить как шаблон» берёт только листья ---------------------

def test_from_task_с_par_группой_берёт_только_листья(ctx):
    """Групповой узел без собственной даты не должен становиться шагом-
    пустышкой шаблона — иначе каждая заведённая из него задача получала бы
    лишний шаг (риск 7 `map-templates.md`)."""
    data = {
        "title": "Задача с группой",
        "steps": [
            {"title": "Группа", "mode": "par", "steps": [
                {"title": "А", "control_date": "2026-08-05"},
                {"title": "Б", "control_date": "2026-08-07"},
            ]},
        ],
    }
    meta = engine.build_task(data, date(2026, 8, 1))
    task = {"path": None, "meta": meta, "body": ""}
    core_persist.save_task(ctx, task, date(2026, 8, 1))
    task_id = task["path"].id

    карточка = core_templates.from_task(ctx, task_id, "Из группы", date(2026, 8, 1))
    assert [s.title for s in карточка.steps] == ["А", "Б"]
    assert [s.offset_days for s in карточка.steps] == [0, 2]


def test_from_task_несуществующей_задачи_даёт_NotFound(ctx):
    with pytest.raises(NotFound):
        core_templates.from_task(ctx, 999, "Имя", date(2026, 8, 1))


# --- instantiate: файлы шаблона и журнал повторений -------------------------

def test_instantiate_копирует_вложения_и_пишет_журнал(ctx):
    сегодня = date(2026, 8, 1)
    core_templates.save(ctx, {
        **_template(), "recurrence": {"anchor": "2026-08-01", "freq": "daily"},
    }, сегодня)
    ctx.store.add_attachment("template", "Отчёт", "a" * 64, "схема.png",
                             "image/png", 9, None, сегодня)

    результат = core_templates.instantiate(
        ctx, "Отчёт", None, None, сегодня, datetime(2026, 8, 1, 9, 0), ctx.work())

    assert результат.attachments == 1
    файлы = ctx.store.list_attachments("task", результат.task)
    assert [f["filename"] for f in файлы] == ["схема.png"]

    # issue #2: ручная задача закрывает собой тот же цикл, что иначе создал бы
    # `recur` — без записи в журнал следующий прогон завёл бы дубль.
    состояние = core_recur.load_state(ctx)
    assert состояние["Отчёт"]["previous"]["date"] == "2026-08-01"
    assert состояние["Отчёт"]["previous"]["task"] == результат.task


def test_instantiate_несуществующего_шаблона_даёт_NotFound(ctx):
    with pytest.raises(NotFound):
        core_templates.instantiate(ctx, "Нет такого", None, None,
                                   date(2026, 8, 1), datetime(2026, 8, 1), ctx.work())


def test_instantiate_плохой_даты_старта_даёт_ValidationError(ctx):
    core_templates.save(ctx, _template(), date(2026, 8, 1))
    with pytest.raises(ValidationError) as excinfo:
        core_templates.instantiate(ctx, "Отчёт", "чепуха", None,
                                   date(2026, 8, 1), datetime(2026, 8, 1), ctx.work())
    assert excinfo.value.errors[0]["field"] == "start"


# --- core.recur.run: цикл создаётся один раз --------------------------------

def test_run_создаёт_цикл_и_второй_раз_не_дублирует(ctx):
    core_templates.save(ctx, {
        **_template("Полив"), "recurrence": {"anchor": "2026-08-01", "freq": "daily"},
    }, date(2026, 8, 1))

    первый = core_recur.run(ctx, date(2026, 8, 1))
    assert первый["created"] == 1

    второй = core_recur.run(ctx, date(2026, 8, 1))
    assert второй["created"] == 0
