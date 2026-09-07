#!/usr/bin/env python3
"""Базовые замеры движка на синтетическом сторе.

Зачем скриптом, а не руками: замер от 2026-08-24 («лента 161 мс на 5120
задачах», PLAN.md, строка про R29) был разовым — повторить его и сравнить с ним
нечем, а весь довод про полное чтение стора держится на этих цифрах. После
рефакторинга нужно сравнивать с той же нагрузкой, а не с записью в документе.

Стор — всегда временный и синтетический. Настоящий `стор.db` не читается и не
пишется: во-первых, там дела заказчика, во-вторых, `done` и `refresh` — операции
записи, и мерить их на живых данных нельзя.

Детерминированность: дата отсчёта зафиксирована (`BASE`), генератор с seed,
поэтому два прогона в разные дни дают одинаковую нагрузку.

    python3 tools/bench.py                    три профиля из PLAN.md
    python3 tools/bench.py --closed 5000      один профиль
    python3 tools/bench.py --repeat 9         больше повторов, меньше шума
    python3 tools/bench.py --json             машиночитаемый вывод
"""
import argparse
import json
import os
import random
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Стор подменяется до импорта движка: `VAULT` читается на уровне модуля, и
# переменная окружения после импорта уже ничего не решает.
_TMP = tempfile.mkdtemp(prefix="yungdrung-bench-")
os.environ["YUNGDRUNG_VAULT"] = _TMP

import engine  # noqa: E402
import store  # noqa: E402

# Дата отсчёта, от которой раскладываются сроки шагов. Фиксированная нарочно:
# иначе «сколько сейчас просрочено» зависело бы от дня прогона, а вместе с ним
# и время ленты.
BASE = date(2026, 9, 1)

# Профили из PLAN.md: 150 активных задач и растущий хвост закрытых. Именно на
# росте хвоста видно линейность — лента показывает те же 18 строк.
PROFILES = [1250, 5000, 10000]


def _args(**поля):
    поля.setdefault("force", False)
    поля.setdefault("reason", None)
    поля.setdefault("to", None)
    поля.setdefault("status", None)
    return SimpleNamespace(**поля)


# --- генерация нагрузки ----------------------------------------------------

def сгенерировать(путь, *, active, closed, seed=20260901):
    """Синтетический стор: `active` открытых задач по три шага и `closed`
    закрытых по два, с журналом. Журнал существенен: `_assemble_step` тянет его
    для каждого шага, и именно это делает полное чтение дорогим.
    """
    rnd = random.Random(seed)
    conn = sqlite3.connect(str(путь / "стор.db"))
    store.migrate_schema(conn)
    conn.execute("PRAGMA synchronous=OFF")

    задачи, шаги, журнал = [], [], []

    for i in range(active):
        задачи.append((f"Активная задача {i:05d}", 1, store._iso(BASE - timedelta(days=30)),
                       store._iso(BASE - timedelta(days=30)), 0, None,
                       "Тело задачи, его пишет заказчик.\n"))
    for i in range(closed):
        задачи.append((f"Закрытая задача {i:05d}", 1, store._iso(BASE - timedelta(days=200)),
                       store._iso(BASE - timedelta(days=200)), 0, None,
                       "Тело задачи, его пишет заказчик.\n"))

    conn.executemany(
        "INSERT INTO tasks (title, schema, created, start_date, cancelled, "
        "cancelled_reason, body) VALUES (?, ?, ?, ?, ?, ?, ?)", задачи)

    ids = [r[0] for r in conn.execute("SELECT id FROM tasks ORDER BY id")]
    активные_ids = ids[:active]

    for n, tid in enumerate(ids):
        закрытая = n >= active
        if закрытая:
            for k in range(2):
                готово = BASE - timedelta(days=rnd.randint(20, 190))
                шаги.append((tid, k + 1, k, f"Шаг {k + 1}", "done", None, None,
                             store._iso(готово), None, None, None))
                журнал.append((tid, k + 1, store._iso(готово), "done", None, None, None))
        else:
            # Сроки раскладываются от −10 до +14 дней от BASE: часть в просрочке,
            # часть в горизонте ленты, часть ждёт. Так лента, завал и «ждут»
            # наполняются все три, как в жизни.
            срок = BASE + timedelta(days=rnd.randint(-10, 14))
            шаги.append((tid, 1, 0, "Первый шаг", "pending", None, store._iso(срок),
                         None, None, None, None))
            # У части активных шагов есть история переносов — тоже нагрузка на
            # сборку, и заодно наполняет счётчик буксования.
            for j in range(rnd.choice([0, 0, 1, 2])):
                журнал.append((tid, 1, store._iso(срок - timedelta(days=j + 1)),
                               "not_done", "не было времени", None, None))
            for k in (2, 3):
                шаги.append((tid, k, k - 1, f"Шаг {k}", "pending", None,
                             store._iso(срок + timedelta(days=7 * (k - 1))),
                             None, None, None, None))

    conn.executemany(
        "INSERT INTO steps (task_id, step_id, position, title, status, start_date, "
        "control_date, completed_date, note, parent_id, mode) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", шаги)
    conn.executemany(
        "INSERT INTO step_log (task_id, step_id, date, event, reason, was, to_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", журнал)
    conn.commit()
    conn.close()
    return {"tasks": len(задачи), "steps": len(шаги), "log": len(журнал),
            "active_ids": активные_ids}


# --- измерение -------------------------------------------------------------

def замер(вызов, repeat):
    """Медиана и минимум по `repeat` прогонам. Медиана, а не среднее: один
    подвисший прогон не должен решать за все остальные.
    """
    времена = []
    for _ in range(repeat):
        t = time.perf_counter()
        вызов()
        времена.append((time.perf_counter() - t) * 1000)
    return {"median_ms": round(statistics.median(времена), 1),
            "min_ms": round(min(времена), 1),
            "max_ms": round(max(времена), 1)}


def профиль(active, closed, repeat, seed, no_refresh=False):
    каталог = Path(tempfile.mkdtemp(prefix="yungdrung-bench-", dir=_TMP))
    engine.VAULT = каталог
    engine.KB_DIR = каталог / "База"
    итог = сгенерировать(каталог, active=active, closed=closed, seed=seed)

    # Название задачи для `show`: движок ищет куском названия, поэтому берём
    # заведомо уникальный кусок.
    имя_задачи = "Активная задача 00000"

    операции = {
        "feed": lambda: engine.cmd_feed(_args(), BASE),
        "backlog": lambda: engine.cmd_backlog(_args(), BASE),
        "list": lambda: engine.cmd_list(_args(), BASE),
        "show": lambda: engine.cmd_show(_args(task=имя_задачи), BASE),
    }
    результат = {op: замер(fn, repeat) for op, fn in операции.items()}

    # `refresh` меряется одним прогоном, а не пятью: он перезаписывает каждую
    # задачу целиком, и на десяти тысячах один вызов идёт минуты. Разброс у него
    # при этом мал — мерить нечего, кроме масштаба.
    if not no_refresh:
        результат["refresh"] = замер(lambda: engine.cmd_refresh(_args(), BASE), 1)

    # `done` — запись, и каждый вызов закрывает свой шаг, поэтому меряется по
    # отдельным задачам, а не повтором над одной.
    времена = []
    for i in range(repeat):
        цель = f"Активная задача {i:05d}"
        t = time.perf_counter()
        engine.cmd_done(_args(task=цель, step="1"), BASE)
        времена.append((time.perf_counter() - t) * 1000)
    результат["done (запись)"] = {"median_ms": round(statistics.median(времена), 1),
                                  "min_ms": round(min(времена), 1),
                                  "max_ms": round(max(времена), 1)}

    размер = (каталог / "стор.db").stat().st_size
    return {"active": active, "closed": closed, "tasks": итог["tasks"],
            "steps": итог["steps"], "log_rows": итог["log"],
            "db_bytes": размер, "ops": результат}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--active", type=int, default=150, help="открытых задач (по умолчанию 150)")
    p.add_argument("--closed", type=int, action="append",
                   help="закрытых задач; можно повторять. По умолчанию 1250, 5000, 10000")
    p.add_argument("--repeat", type=int, default=5, help="повторов на операцию (по умолчанию 5)")
    p.add_argument("--seed", type=int, default=20260901, help="seed генератора")
    p.add_argument("--no-refresh", action="store_true",
                   help="без refresh: на больших профилях он идёт минуты")
    p.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    a = p.parse_args()

    профили = a.closed or PROFILES
    отчёт = {"base_date": BASE.isoformat(), "python": sys.version.split()[0],
             "repeat": a.repeat, "seed": a.seed, "profiles": []}
    for closed in профили:
        отчёт["profiles"].append(профиль(a.active, closed, a.repeat, a.seed,
                                     no_refresh=a.no_refresh))

    if a.json:
        print(json.dumps(отчёт, ensure_ascii=False, indent=2))
        return

    print(f"Дата отсчёта {BASE}, python {отчёт['python']}, повторов {a.repeat}, seed {a.seed}")
    print("Медиана в миллисекундах.\n")
    операции = [o for o in ["feed", "backlog", "list", "show", "refresh",
                            "done (запись)"]
                if all(o in pr["ops"] for pr in отчёт["profiles"])]
    ширина = max(len(o) for o in операции)
    шапка = "  ".join(f"{c:>9}" for c in [f"{p['closed']}з" for p in отчёт["profiles"]])
    print(f"{'операция':<{ширина}}  {шапка}")
    print("-" * (ширина + 2 + len(шапка)))
    for op in операции:
        числа = "  ".join(f"{pr['ops'][op]['median_ms']:>9}" for pr in отчёт["profiles"])
        print(f"{op:<{ширина}}  {числа}")
    print()
    for pr in отчёт["profiles"]:
        print(f"  {pr['closed']}з: задач {pr['tasks']}, шагов {pr['steps']}, "
              f"строк журнала {pr['log_rows']}, база {pr['db_bytes'] // 1024} КБ")


if __name__ == "__main__":
    main()
