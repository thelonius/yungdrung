#!/usr/bin/env python3
"""Тесты `domain.steps_plan` напрямую, без `engine`.

Логика `resolve_steps`, `validate_*`, `build_task` переехала файлом и покрыта
через адаптеры в test_card.py и test_engine.py; здесь — то, что появилось при
переезде (срез 2): предпросмотр `plan`/`to_planned`, подсчёт пропавших шагов до
мутации, нормализация листа, ставшего группой (Р14).
"""
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import store  # noqa: E402
from domain import steps_plan  # noqa: E402

TODAY = date(2026, 9, 8)


def _task(steps, start=date(2026, 9, 1)):
    return {"path": store.TaskRef(1, "Задача"),
            "meta": {"title": "Задача", "start_date": start, "tags": [], "steps": steps},
            "body": ""}


def _step(sid, title, **fields):
    шаг = {"id": sid, "title": title, "status": "pending", "start_date": None,
           "control_date": None, "completed_date": None, "note": None,
           "parent": None, "mode": None, "log": []}
    шаг.update(fields)
    return шаг


def test_plan_дефолтные_старты_в_цепочке_и_в_параллельной_группе():
    nodes, errors, warnings = steps_plan.plan([
        {"title": "Первый", "control_date": "10.09"},
        {"title": "Подписи", "mode": "par", "steps": [
            {"title": "Арендодатель", "control_date": "12.09"},
            {"title": "Банк", "control_date": "13.09"}]},
        {"title": "Ключи", "control_date": "20.09"},
    ], TODAY, TODAY)
    assert errors == [] and warnings == []
    планы = steps_plan.to_planned(nodes)
    # Первый лист стартует от старта задачи, следующие — от контроля предыдущего;
    # внутри параллельной группы оба ребёнка стартуют от точки входа группы.
    assert планы[0]["start"] == "2026-09-08" and not планы[0]["explicit_start"]
    assert [c["start"] for c in планы[1]["steps"]] == ["2026-09-10", "2026-09-10"]
    # Группа кончается самым поздним контролем поддерева.
    assert планы[2]["start"] == "2026-09-13"
    assert планы[1]["start"] is None and планы[1]["mode"] == "par"
    assert [c["path"] for c in планы[1]["steps"]] == ["steps.1.steps.0", "steps.1.steps.1"]


def test_plan_с_временем_отдаёт_строку_с_пробелом():
    nodes, _, _ = steps_plan.plan([{"title": "A", "control_date": "10.09 10:00"}],
                                  TODAY, TODAY)
    assert steps_plan.to_planned(nodes)[0]["control"] == "2026-09-10 10:00"


def test_plan_old_подставляет_сохранённые_даты_начала():
    old = {2: _step(2, "Второй", start_date=date(2026, 9, 3))}
    # Даты полным ISO-текстом, не «05.09»: у последнего разбор без года
    # катит на дату раньше `today` в следующий год, а тест сравнивает именно
    # с исходным годом.
    nodes, errors, _ = steps_plan.plan(
        [{"id": 2, "title": "Второй", "control_date": "2026-09-05"},
         {"title": "Новый", "control_date": "2026-09-15"}],
        date(2026, 9, 6), TODAY, old=old)
    # Без `old` контроль 05.09 был бы раньше старта 06.09 — ошибка; с ним лист
    # держит свою сохранённую дату начала, и перестановка проходит.
    assert errors == []
    assert steps_plan.to_planned(nodes)[0]["start"] == "2026-09-03"


def test_plan_не_отдаёт_ошибки_названия_но_отдаёт_остальные():
    _, errors, _ = steps_plan.plan([
        {"title": "", "control_date": "позавчера"},
        {"title": "", "control_date": "10.09"},
    ], TODAY, TODAY)
    assert [e["field"] for e in errors] == ["steps.0.control_date"]
    assert errors[0]["error"].startswith("Дату не понял")


def test_plan_предупреждения_по_явному_старту():
    _, _, warnings = steps_plan.plan(
        [{"title": "A", "start_date": "2026-09-05", "control_date": "2026-09-12"}],
        date(2026, 9, 10), TODAY)
    assert warnings == [{"field": "steps.0.start_date",
                         "warning": "Шаг начинается раньше даты начала задачи"}]


def test_пропавшие_считаются_до_мутации():
    task = _task([_step(1, "A", control_date=date(2026, 9, 10)),
                  _step(2, "B", control_date=date(2026, 9, 12))])
    данные = {"title": "Задача", "steps": [{"id": 1, "title": "A", "control_date": "10.09"}]}
    assert steps_plan.missing_step_ids(task, данные) == [2]
    # Задача не тронута: шагов по-прежнему два.
    assert [s["id"] for s in task["meta"]["steps"]] == [1, 2]
    # А после применения список тот же самый.
    assert steps_plan.apply_task_edit(task, данные, TODAY) == [2]
    assert [s["id"] for s in task["meta"]["steps"]] == [1]


def test_пропавшие_видят_id_внутри_группы():
    task = _task([_step(1, "Группа", mode="par"), _step(2, "Лист", parent=1)])
    данные = {"steps": [{"id": 1, "title": "Группа", "mode": "par",
                         "steps": [{"id": 2, "title": "Лист"}]}]}
    assert steps_plan.missing_step_ids(task, данные) == []


def test_лист_ставший_группой_нормализуется(caplog):
    журнал = [{"date": date(2026, 9, 1), "event": "done"}]
    task = _task([_step(1, "Был листом", status="done",
                        completed_date=date(2026, 9, 1), log=журнал)])
    steps_plan.apply_task_edit(task, {"steps": [
        {"id": 1, "title": "Стал группой", "mode": "seq",
         "steps": [{"title": "Подшаг", "control_date": "12.09"}]}]}, TODAY)
    группа, лист = task["meta"]["steps"]
    assert группа["mode"] == "seq" and группа["status"] == "pending"
    assert группа["completed_date"] is None
    assert группа["log"] == журнал, "журнал остаётся: это история, а не состояние"
    assert лист["parent"] == 1 and лист["id"] == 2


def test_strip_steps_block_режет_блок_и_склейку():
    тело = f"Заметка.\n\n{steps_plan.STEPS_START}\n- [ ] 1. Шаг\n{steps_plan.STEPS_END}\n"
    без, вернуть = steps_plan.strip_steps_block(тело)
    assert без == "Заметка.\n\n"
    assert вернуть(без) == тело


def test_to_planned_даты_без_времени_и_с_временем():
    nodes = [{"поле": "steps.0", "id": None, "mode": None, "start": date(2026, 9, 8),
              "control": datetime(2026, 9, 9, 15, 30), "явный_старт": False, "children": []}]
    assert steps_plan.to_planned(nodes) == [{
        "path": "steps.0", "id": None, "mode": None, "start": "2026-09-08",
        "control": "2026-09-09 15:30", "explicit_start": False, "steps": []}]
