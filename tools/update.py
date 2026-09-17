#!/usr/bin/env python3
"""Обновление на машине заказчика: `git pull`, зависимости, сборка клиента.

    python3 tools/update.py            обновить
    python3 tools/update.py --dry-run  только сказать, что было бы сделано

Правило R6 из ТЗ: упавшее обновление блокирует обновление, а не работу.
Поэтому клиент собирается во временный каталог и подменяет `apps/web/dist`
одним переименованием только при успехе; прошлый `dist` остаётся рядом как
`dist.prev` — откат руками, если новая сборка чем-то не понравилась. Упала
сборка — на экране причина, в `dist` лежит то, что работало.

Сборка запускается только если после `git pull` менялись `apps/web/` или
lock-файл, или `dist` ещё нет: `npm ci` на ноутбуке — минута с сетью, и
платить её за правку README незачем. `pip install -r requirements.txt` —
по тому же признаку.

Ничего из этого не требует Node в PATH у самого трекера: Node нужен только
здесь, во время обновления. Решение и обоснование — REFACTOR.md, таблица.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "web"
DIST = WEB / "dist"
PREV = WEB / "dist.prev"

NPM = "npm.cmd" if os.name == "nt" else "npm"


class UpdateError(Exception):
    pass


def run(cmd, cwd=ROOT, *, capture=True):
    """Один вызов внешней команды. Вынесен, чтобы тест мог подменить: сборку
    проверяют с `npm`, который падает, а не с настоящим."""
    r = subprocess.run(cmd, cwd=str(cwd), text=True, encoding="utf-8",
                       capture_output=capture)
    if r.returncode != 0:
        вывод = (r.stderr or r.stdout or "").strip()
        raise UpdateError(f"{' '.join(cmd)} → код {r.returncode}\n{вывод}")
    return r.stdout or ""


def changed_files(before, after, runner=run):
    if not before or before == after:
        return []
    return runner(["git", "diff", "--name-only", before, after]).split()


def needs_build(changed, dist=DIST):
    if not dist.is_dir():
        return True
    return any(f.startswith("apps/web/") for f in changed)


def build(runner=run, web=WEB, dist=DIST, prev=PREV):
    """Сборка во временный каталог рядом с `dist` (тот же диск — переименование
    атомарно), затем подмена. Порядок: старый `dist` → `dist.prev`, временный →
    `dist`. Между двумя переименованиями `dist` отсутствует доли секунды; сервер
    в это время не перезапускается, а уже отданные файлы у браузера в кэше."""
    tmp = Path(tempfile.mkdtemp(prefix="dist-", dir=str(web)))
    try:
        runner([NPM, "ci", "--no-audit", "--no-fund"], cwd=web)
        runner([NPM, "run", "build", "--", "--outDir", str(tmp), "--emptyOutDir"], cwd=web)
        if not (tmp / "index.html").is_file():
            raise UpdateError("сборка отработала, но index.html в результате нет")
    except UpdateError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if prev.exists():
        shutil.rmtree(prev)
    if dist.exists():
        os.replace(dist, prev)
    os.replace(tmp, dist)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-pull", action="store_true", help="не дёргать git, только собрать")
    a = ap.parse_args(argv)

    try:
        before = run(["git", "rev-parse", "HEAD"]).strip()
        if not a.no_pull:
            print("git pull…")
            run(["git", "pull", "--ff-only"], capture=False)
        after = run(["git", "rev-parse", "HEAD"]).strip()
        changed = changed_files(before, after)
        print(f"{before[:7]} → {after[:7]}, изменено файлов: {len(changed)}")

        if "requirements.txt" in changed:
            print("requirements.txt менялся → pip install")
            if not a.dry_run:
                run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                    capture=False)

        if needs_build(changed):
            print("сборка клиента (apps/web менялся или dist ещё нет)…")
            if not a.dry_run:
                build()
                print(f"готово: {DIST.relative_to(ROOT)}; прошлая сборка — {PREV.name}")
        else:
            print("клиент не менялся, сборка не нужна")
    except UpdateError as e:
        print(f"обновление не завершено: {e}", file=sys.stderr)
        print("работающая версия не тронута", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
