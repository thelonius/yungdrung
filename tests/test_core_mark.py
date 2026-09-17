#!/usr/bin/env python3
"""Тесты `core.mark` и `core.feed` напрямую, минуя адаптер `engine.py`.

Поведение отметок целиком покрыто в test_engine.py через CLI-форму; здесь
проверяется то, что появилось вместе с ядром и на что будет опираться HTTP
(REFACTOR.md, срез 1b): типизированные исключения вместо `sys.exit`, `task_id`
в строках, новое состояние в ответе на запись.
"""
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import store  # noqa: E402
from core import errors, feed as core_feed, mark as core_mark  # noqa: E402
from core.context import Context  # noqa: E402

TODAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 8, 11, 0)


@pytest.fixture
def env(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "стор.db"))
    store.migrate_schema(conn)
    cur = conn.execute(
        "INSERT INTO tasks (title, schema, created, start_date, cancelled, body) "
        "VALUES ('Заявка на грант', 1, '2026-09-01', '2026-09-01', 0, '')")
    tid = cur.lastrowid
    conn.executemany(
        "INSERT INTO steps (task_id, step_id, position, title, status, control_date) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        [(tid, 1, 0, "Собрать документы", "2026-09-08"),
         (tid, 2, 1, "Отправить", None)])
    conn.commit()
    conn.close()
    return Context(tmp_path), tid


def run(env, op, step=1, **extra):
    ctx, tid = env
    return core_mark.mark(ctx, op, tid, step, today=TODAY, now=NOW, work=ctx.work(), **extra)


def test_строка_ленты_несёт_task_id(env):
    ctx, tid = env
    лента = core_feed.feed(ctx, NOW, ctx.work())
    assert [r.task_id for r in лента.feed] == [tid]
    assert лента.feed[0].step == 1


def test_ответ_на_отметку_несёт_новое_состояние(env):
    ctx, tid = env
    до = core_feed.feed(ctx, NOW, ctx.work())
    assert до.counts.today == 1

    r = run(env, "done")
    assert r.ok and r.task_id == tid and r.status == "done"
    # Следующий шаг получил дату «сегодня» и занял место в ленте: строка в
    # ответе — про отмеченный шаг, которого в ленте больше нет.
    assert r.dates_assigned == [2] and r.next_step_id == 2
    assert r.row is None
    assert r.counts.today == 1
    assert r.counts == core_feed.feed(ctx, NOW, ctx.work()).counts


def test_перенос_возвращает_строку_в_новом_состоянии(env):
    ctx, _ = env
    r = run(env, "defer", to=date(2026, 9, 12), reason=ctx.reasons()[0])
    assert r.row is not None
    assert r.row.state == "waiting" and r.row.control_at == "2026-09-12"
    assert r.counts.waiting == 1 and r.counts.today == 0


def test_чужой_id_и_чужой_шаг_это_NotFound(env):
    ctx, tid = env
    with pytest.raises(errors.NotFound):
        run(env, "done", step=99)
    with pytest.raises(errors.NotFound):
        core_mark.mark(ctx, "done", tid + 100, 1, today=TODAY, now=NOW, work=ctx.work())


def test_повторная_отметка_это_Conflict_с_фактическим_статусом(env):
    run(env, "done")
    with pytest.raises(errors.Conflict) as e:
        run(env, "done")
    assert e.value.actual_status == "done"


@pytest.mark.parametrize("op, extra, поле", [
    ("fail", {}, "reason"),
    ("defer", {"reason": None}, "to"),
    ("notdone", {"reason": "такой нет"}, "reason"),
    ("взлететь", {}, "op"),
])
def test_ошибки_ввода_структурные_с_полем(env, op, extra, поле):
    with pytest.raises(errors.ValidationError) as e:
        run(env, op, **extra)
    assert [x["field"] for x in e.value.errors] == [поле]


def test_причина_у_не_сделано_необязательна(env):
    """R15: комментарий к «не сделано» необязателен — иначе отметка одним
    нажатием (`n` в ленте) была бы невозможна."""
    r = run(env, "notdone")
    assert r.status == "pending" and r.next_check == "2026-09-09" and r.stalled == 1


def test_undo_снимает_сегодняшнее_и_отказывает_по_пустому_журналу(env):
    ctx, tid = env
    run(env, "done")
    r = core_mark.undo(ctx, tid, 1, today=TODAY, now=NOW, work=ctx.work())
    assert r.undone == "done" and r.status == "pending"
    assert r.row is not None and r.row.state == "due"
    with pytest.raises(errors.ValidationError):
        core_mark.undo(ctx, tid, 2, today=TODAY, now=NOW, work=ctx.work())
