#!/usr/bin/env python3
"""Тесты `core.tasks` напрямую, минуя `engine.py` — как `test_core_mark.py`.

Форма ответов CLI-адаптеров (`cmd_create` и т. д.) покрыта golden-снимком
`tests/test_golden_tasks.py`; здесь — поведение самого прикладного слоя,
которое станет опорой для `/api/v1` (создание, карточка, правка, перенос
владения вложениями/базы знаний при переименовании, закрытие, переоткрытие).
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import tasks as core_tasks  # noqa: E402
from core.context import Context  # noqa: E402
from core.errors import Conflict, NotFound, ValidationError  # noqa: E402
from core.models_tasks import PlanIn, StepEditIn, TaskEditIn  # noqa: E402

TODAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 8, 11, 0)


@pytest.fixture
def ctx(tmp_path):
    return Context(tmp_path)


def _create(ctx, title="Заявка на грант", steps=None, **fields):
    data = {"title": title, "steps": steps or [{"title": "Собрать документы",
                                                 "control_date": "10.09"}]}
    data.update(fields)
    return core_tasks.create_task(ctx, data, TODAY)


def test_create_несёт_мягкие_предупреждения(ctx):
    """`core.tasks.create` (не `create_task`) — путь `POST /tasks`: карточка
    плюс `soft_warnings`, посчитанные здесь же, а не в HTTP-слое (находка
    ревью среза 2, api/v1/tasks.py:274 — там раньше жил единственный прямой
    импорт `domain` среди `api/v1/*.py`)."""
    result = core_tasks.create(ctx, {
        "title": "Заявка", "start_date": "2026-09-10",
        "steps": [{"title": "A", "start_date": "2026-09-05", "control_date": "2026-09-12"}],
    }, TODAY, NOW, ctx.work())
    assert result.created
    assert [w.model_dump() for w in result.warnings] == [
        {"field": "steps.0.start_date",
         "warning": "Шаг начинается раньше даты начала задачи"}]


def test_create_task_возвращает_задачу_с_id(ctx):
    task = _create(ctx)
    assert task["path"].id and task["path"].stem == "Заявка на грант"
    assert task["meta"]["steps"][0]["title"] == "Собрать документы"


def test_create_task_дубль_имени_422(ctx):
    _create(ctx)
    with pytest.raises(ValidationError) as e:
        _create(ctx)
    assert e.value.errors[0]["field"] == "title"


def test_quick_create_разбирает_дату_и_режет_хвост(ctx):
    result = core_tasks.quick_create(
        ctx, "позвонить Василию завтра в полдесятого", TODAY, NOW, ctx.work())
    assert result.created and result.task == "позвонить Василию"
    шаг = result.card.steps[0]
    assert шаг.title == "позвонить Василию"
    assert шаг.control_date == "2026-09-09 09:30"


def test_quick_create_без_даты_контроль_сегодня(ctx):
    result = core_tasks.quick_create(ctx, "разобрать почту", TODAY, NOW, ctx.work())
    assert result.card.steps[0].control_date == "2026-09-08"


def test_quick_create_пустой_текст_422(ctx):
    with pytest.raises(ValidationError) as e:
        core_tasks.quick_create(ctx, "   ", TODAY, NOW, ctx.work())
    assert e.value.errors[0]["field"] == "text"


def test_update_task_переименование_переносит_вложения_и_ссылки_базы_знаний(ctx):
    task = _create(ctx)
    склад = ctx.store
    склад.add_attachment("task", "Заявка на грант", "h1", "a.png", "image/png",
                         10, None, TODAY)
    склад.add_attachment("step", "Заявка на грант:1", "h2", "b.png", "image/png",
                         10, None, TODAY)
    note_id = склад.add_kb_note("Фонд")
    склад.save_kb_links([{"kb_entry_id": note_id, "source_type": "task",
                          "source_id": "Заявка на грант", "matched": "х",
                          "offset_start": 0, "offset_end": 1, "confirmed_at": TODAY}])

    edit = TaskEditIn(title="Заявка на грант ФПГ", force=True, steps=[
        StepEditIn(id=1, title="Собрать документы", control_date="10.09")])
    result = core_tasks.update_task(ctx, task["path"].id, edit, TODAY, NOW, ctx.work())

    assert result.renamed_from == "Заявка на грант"
    assert result.task == "Заявка на грант ФПГ"
    assert склад.list_attachments("task", "Заявка на грант") == []
    assert len(склад.list_attachments("task", "Заявка на грант ФПГ")) == 1
    assert len(склад.list_attachments("step", "Заявка на грант ФПГ:1")) == 1
    ссылки = склад.load_kb_links()
    assert ссылки[0]["source_id"] == "Заявка на грант ФПГ"


def test_update_task_пропавший_шаг_без_force_422(ctx):
    task = _create(ctx, steps=[
        {"title": "Раз", "control_date": "10.09"},
        {"title": "Два", "control_date": "12.09"}])
    edit = TaskEditIn(title="Заявка на грант", steps=[
        StepEditIn(id=1, title="Раз", control_date="10.09")])
    with pytest.raises(ValidationError) as e:
        core_tasks.update_task(ctx, task["path"].id, edit, TODAY, NOW, ctx.work())
    assert e.value.errors[0]["field"] == "steps"


def test_delete_удаляет_строки_владения(ctx):
    task = _create(ctx)
    ctx.store.add_attachment("task", "Заявка на грант", "h1", "a.png",
                             "image/png", 10, None, TODAY)
    result = core_tasks.delete(ctx, task["path"].id)
    assert result.deleted and result.task == "Заявка на грант"
    assert ctx.store.list_attachments("task", "Заявка на грант") == []
    with pytest.raises(NotFound):
        core_tasks.show(ctx, task["path"].id, TODAY, NOW, ctx.work())


def test_card_row_у_открытого_листа_и_none_у_группы_и_закрытого(ctx):
    task = _create(ctx, steps=[
        {"title": "Открытый", "control_date": "10.09"},
        {"title": "Группа", "mode": "par", "steps": [
            {"title": "Подшаг", "control_date": "12.09"}]},
    ])
    card = core_tasks.card(ctx, task, TODAY, NOW, ctx.work())
    открытый, группа = card.steps
    assert открытый.row is not None and открытый.row.step == 1
    assert группа.row is None and группа.mode == "par"
    assert группа.steps[0].row is not None

    закрыто = core_tasks.close(ctx, task["path"].id, TODAY, NOW, ctx.work())
    закрытый_шаг = закрыто.card.steps[0]
    assert закрытый_шаг.closed and закрытый_шаг.row is None
    assert закрытый_шаг.actions == ["reopen", "undo"]


def test_card_history_по_убыванию_даты(ctx):
    task = _create(ctx, steps=[
        {"title": "Раз", "control_date": "10.09"},
        {"title": "Два", "control_date": "12.09"}])
    core_tasks.close(ctx, task["path"].id, date(2026, 9, 1), NOW, ctx.work())
    task2 = core_tasks.show(ctx, task["path"].id, TODAY, NOW, ctx.work())
    даты = [e.date for e in task2.history]
    assert даты == sorted(даты, reverse=True)


def test_close_идемпотентен(ctx):
    task = _create(ctx)
    первый = core_tasks.close(ctx, task["path"].id, TODAY, NOW, ctx.work())
    assert первый.closed_steps == 1
    второй = core_tasks.close(ctx, task["path"].id, TODAY, NOW, ctx.work())
    assert второй.closed_steps == 0
    assert второй.card.task_status == "done"


def test_reopen_не_done_конфликт(ctx):
    task = _create(ctx)
    with pytest.raises(Conflict):
        core_tasks.reopen(ctx, task["path"].id, 1, TODAY, NOW, ctx.work())


def test_reopen_возвращает_шаг_в_работу(ctx):
    task = _create(ctx)
    core_tasks.close(ctx, task["path"].id, TODAY, NOW, ctx.work())
    card = core_tasks.reopen(ctx, task["path"].id, 1, TODAY, NOW, ctx.work())
    assert card.steps[0].status == "pending" and card.steps[0].completed_date is None


def test_cancel_дважды_конфликт(ctx):
    task = _create(ctx)
    core_tasks.cancel(ctx, task["path"].id, "передумали", TODAY, NOW, ctx.work())
    with pytest.raises(Conflict):
        core_tasks.cancel(ctx, task["path"].id, None, TODAY, NOW, ctx.work())


def test_resolve_title_точное_совпадение(ctx):
    task = _create(ctx)
    ref = core_tasks.resolve_title(ctx, "Заявка на грант")
    assert ref.task_id == task["path"].id
    with pytest.raises(NotFound):
        core_tasks.resolve_title(ctx, "грант")  # не точное — не находится


def test_resolve_title_пустой_title_422(ctx):
    """Ветка 422 §1.1 SLICE2_SPEC.md, не задетая ни одним из двух других
    тестов на `resolve_title` (находка ревью среза 2, core/tasks.py:203)."""
    with pytest.raises(ValidationError) as excinfo:
        core_tasks.resolve_title(ctx, "   ")
    assert excinfo.value.errors[0]["field"] == "title"


# --- plan: режим правки (task_id) --------------------------------------------
# Находка ревью среза 2 (core/tasks.py:225): ни один тест не вызывал
# `core.tasks.plan` с заданным `task_id` — путь, который карточка задачи
# держит стабильным при перетаскивании шагов (§5.5.10 SLICE2_SPEC.md). Тест
# на `domain.steps_plan.plan(..., old=...)` (`test_steps_plan.py`) проверяет
# только чистую функцию с готовым `old`, а не его сборку из загруженной
# задачи и не дефолт `start` от `task['meta']['start_date']` — обе эти
# склейки здесь.

def test_plan_режима_правки_берёт_старт_задачи_и_старые_даты_начала(ctx):
    task = _create(ctx, start_date="2026-09-01", steps=[
        {"title": "Первый", "control_date": "2026-09-05"},
        {"title": "Второй", "control_date": "2026-09-10"},
    ])
    sid1, sid2 = (s["id"] for s in task["meta"]["steps"])
    assert task["meta"]["steps"][1]["start_date"] == date(2026, 9, 5), (
        "предпосылка теста: старт второго шага изначально — контроль первого")

    # `start_date` в запросе не задан: дефолт обязан взяться из сохранённого
    # старта задачи (2026-09-01), а не из `today` вызова (module-level TODAY,
    # 2026-09-08) — иначе форма правки молча подвинула бы начало задачи.
    итог = core_tasks.plan(ctx, PlanIn(task_id=task["path"].id, steps=[
        StepEditIn(id=sid1, title="Первый", control_date="2026-09-20"),
        StepEditIn(id=sid2, title="Второй", control_date="2026-09-25"),
    ]), TODAY, NOW)

    assert итог.ok, итог.errors
    первый, второй = итог.steps
    assert первый.start == "2026-09-01"
    # Второй лист держит СВОЙ сохранённый старт (2026-09-05), а не дефолт по
    # новому порядку (который сдвинулся бы на новый контроль первого,
    # 2026-09-20) — ровно то, ради чего `plan` собирает `old` из задачи.
    assert второй.start == "2026-09-05"


def test_plan_режима_правки_чужой_задачи_даёт_NotFound(ctx):
    with pytest.raises(NotFound):
        core_tasks.plan(ctx, PlanIn(task_id=999999, steps=[]), TODAY, NOW)
