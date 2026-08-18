#!/usr/bin/env python3
"""Тесты ленты «Что сегодня» — `collect_open` / `cmd_feed` / `cmd_backlog`.

Тот же приём изоляции, что в test_engine.py: подменяем VAULT/TASKS_DIR через
monkeypatch, зовём функции движка напрямую в обход argparse.

`TODAY` здесь — понедельник, не суббота, как в test_engine.py: рабочее время
(9:00–21:00, без выходных по умолчанию) двигает показ шага на понедельник, если
день выходной, а лента и завал считаются от show_at, а не от голой даты. На
буднем дне это не путается под ногами.

`now` передаётся явно строкой в каждом вызове: без этого `_now` берёт время со
стенных часов машины, на которой идёт pytest, и попадание шага в «сегодня» vs
«завтра» через is_working_moment переставало бы быть детерминированным.
"""
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402

TODAY = date(2026, 8, 17)       # понедельник
TOMORROW = date(2026, 8, 18)    # вторник
OVERDUE = date(2026, 8, 10)     # понедельник неделей раньше — давно просрочено
NOW = "2026-08-17T10:00:00"     # будний день, рабочее время


class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


@pytest.fixture
def vault(tmp_path, monkeypatch):
    tasks = tmp_path / "Задачи"
    tasks.mkdir()
    monkeypatch.setattr(engine, "VAULT", tmp_path)
    monkeypatch.setattr(engine, "TASKS_DIR", tasks)
    return tmp_path


def step(number, title, *, status="pending", control_date=None,
        completed_date=None, log=None):
    return {"id": number, "title": title, "status": status,
            "control_date": control_date, "completed_date": completed_date,
            "log": log if log is not None else []}


def task(vault, name, steps, *, body="Тело заметки, его пишет заказчик.\n", **fields):
    meta = {"schema": 1, "type": "task", "title": name, "created": date(2026, 8, 1)}
    meta.update(fields)
    meta["steps"] = steps
    fm = yaml.dump(meta, Dumper=NoAliasDumper, allow_unicode=True,
                   sort_keys=False, default_flow_style=False)
    path = vault / "Задачи" / f"{name}.md"
    path.write_text(f"---\n{fm}---\n\n{body}", encoding="utf-8")
    return path


def run(command, today=TODAY, now=NOW, **fields):
    fields.setdefault("force", False)
    fields.setdefault("reason", None)
    fields.setdefault("to", None)
    fields.setdefault("now", now)
    return command(SimpleNamespace(**fields), today)


def three_not_done():
    """Три подряд отметки «не сделан» — порог буксования."""
    return [{"date": date(2026, 8, d), "event": "not_done", "reason": f"причина {d}"}
            for d in (3, 4, 5)]


# --- горизонт: лента / завал / ждут не сломаны ------------------------------

def test_horizon_splits_today_tomorrow_and_overdue(vault):
    task(vault, "Сегодня", [step(1, "Позвонить", control_date=TODAY)])
    task(vault, "Завтра", [step(1, "Написать", control_date=TOMORROW)])
    task(vault, "Просрочено", [step(1, "Отправить", control_date=OVERDUE)])

    feed = run(engine.cmd_feed)

    assert [i["task"] for i in feed["feed"]] == ["Сегодня"]
    assert [i["task"] for i in feed["waiting"]] == ["Завтра"]
    assert feed["overdue_count"] == 1
    assert feed["counts"] == {"overdue": 1, "today": 1, "waiting": 1, "attention": 0}

    backlog = run(engine.cmd_backlog)
    assert [i["task"] for i in backlog["backlog"]] == ["Просрочено"]
    assert backlog["count"] == 1


# --- отменённые задачи -------------------------------------------------------

def test_cancelled_task_excluded_from_every_bucket(vault):
    """Докстринг cmd_cancel: отменённая задача перестаёт быть просроченной или
    ждущей. Проверяем, что она не всплывает вообще ни в одной из корзин —
    включая новую «внимание», а не только в ленте и завале."""
    task(vault, "Отменена", [step(1, "Шаг", control_date=TODAY)], cancelled=True)

    feed = run(engine.cmd_feed)
    backlog = run(engine.cmd_backlog)

    assert feed["feed"] == []
    assert feed["waiting"] == []
    assert feed["attention"] == []
    assert feed["stalled"] == []
    assert feed["next_ahead"] is None
    assert feed["counts"] == {"overdue": 0, "today": 0, "waiting": 0, "attention": 0}
    assert backlog["backlog"] == []


def test_cancelled_task_with_overdue_step_not_in_backlog(vault):
    task(vault, "Отменена", [step(1, "Шаг", control_date=OVERDUE)], cancelled=True)
    backlog = run(engine.cmd_backlog)
    assert backlog["backlog"] == []
    assert backlog["count"] == 0


# --- шаг без даты контроля ----------------------------------------------------

def test_step_without_control_date_goes_to_attention_only(vault):
    task(vault, "Без даты", [step(1, "Написать письмо", control_date=None)])
    task(vault, "Ждёт", [step(1, "Дождаться ответа", control_date=TOMORROW)])

    feed = run(engine.cmd_feed)

    assert feed["attention"] == [{
        "task": "Без даты", "step": 1, "title": "Написать письмо",
        "reason": "нет даты контроля", "tags": [],
    }]
    assert feed["attention_count"] == 1
    assert feed["counts"]["attention"] == 1
    assert all(i["task"] != "Без даты" for i in feed["feed"])
    assert all(i["task"] != "Без даты" for i in feed["waiting"])
    # ближайшее впереди — «Ждёт» с настоящей датой, а не бездатный шаг
    assert feed["next_ahead"]["task"] == "Ждёт"
    assert feed["next_ahead"]["control_at"] is not None


def test_next_ahead_is_none_not_dateless_when_only_dateless_step_exists(vault):
    """Раньше бездатный шаг проваливался в «ждут» и мог стать next_ahead с
    show_at=null, state="no_date" — то есть выглядел бы как «ближайшее
    впереди» без даты, чего не бывает. Теперь next_ahead либо есть с датой,
    либо его нет вовсе."""
    task(vault, "Без даты", [step(1, "Написать письмо", control_date=None)])

    feed = run(engine.cmd_feed)

    assert feed["waiting"] == []
    assert feed["next_ahead"] is None


# --- задача вовсе без шагов ---------------------------------------------------

def test_task_without_any_steps_goes_to_attention(vault):
    task(vault, "Пустая", [])

    feed = run(engine.cmd_feed)

    assert feed["attention"] == [{
        "task": "Пустая", "step": None, "title": None,
        "reason": "нет шагов", "tags": [],
    }]
    assert all(i["task"] != "Пустая" for i in feed["feed"] + feed["waiting"])


def test_attention_sorted_by_task_name(vault):
    task(vault, "Юг", [step(1, "Шаг", control_date=None)])
    task(vault, "Альфа", [step(1, "Шаг", control_date=None)])
    task(vault, "Бета", [])

    feed = run(engine.cmd_feed)

    assert [i["task"] for i in feed["attention"]] == ["Альфа", "Бета", "Юг"]


# --- буксование --------------------------------------------------------------

def test_stalled_step_in_waiting_appears_in_stalled_list(vault):
    """Буксующий шаг ждёт (control_date завтра), но три «не сделан» подряд
    всё равно обязаны попасть в общий список stalled, а не только те, что
    лежат в ленте или завале."""
    task(vault, "Буксует", [step(1, "Дожать клиента", control_date=TOMORROW,
                                  log=three_not_done())])

    feed = run(engine.cmd_feed)

    assert any(i["task"] == "Буксует" for i in feed["waiting"])
    assert [i["task"] for i in feed["stalled"]] == ["Буксует"]
    assert feed["stalled_count"] == 1


def test_not_stalled_step_absent_from_stalled_list(vault):
    task(vault, "Свежий", [step(1, "Шаг", control_date=TOMORROW)])
    feed = run(engine.cmd_feed)
    assert feed["stalled"] == []
    assert feed["stalled_count"] == 0
