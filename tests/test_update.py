#!/usr/bin/env python3
"""Тесты скрипта обновления: сборка подменяет dist атомарно и только при успехе.

Внешние команды подменены: `npm` здесь — функция, которая пишет файлы или
падает. Настоящий npm в тестах не нужен и на машине разработчика может
отсутствовать; проверяется логика подмены, а не Vite.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import update  # noqa: E402


@pytest.fixture
def web(tmp_path):
    w = tmp_path / "web"
    w.mkdir()
    dist = w / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("старая", encoding="utf-8")
    return w


def fake_npm(*, ok=True, writes_index=True):
    calls = []

    def runner(cmd, cwd=None, capture=True):
        calls.append(cmd)
        if cmd[1] == "run":
            out = Path(cmd[cmd.index("--outDir") + 1])
            out.mkdir(exist_ok=True)
            if not ok:
                raise update.UpdateError("vite: ошибка сборки")
            if writes_index:
                (out / "index.html").write_text("новая", encoding="utf-8")
        return ""

    runner.calls = calls
    return runner


def test_успешная_сборка_подменяет_dist_и_оставляет_прошлый(web):
    dist, prev = web / "dist", web / "dist.prev"
    update.build(runner=fake_npm(), web=web, dist=dist, prev=prev)
    assert (dist / "index.html").read_text(encoding="utf-8") == "новая"
    assert (prev / "index.html").read_text(encoding="utf-8") == "старая"
    assert not list(web.glob("dist-*")), "временный каталог остался"


def test_упавшая_сборка_не_трогает_dist(web):
    dist, prev = web / "dist", web / "dist.prev"
    with pytest.raises(update.UpdateError):
        update.build(runner=fake_npm(ok=False), web=web, dist=dist, prev=prev)
    assert (dist / "index.html").read_text(encoding="utf-8") == "старая"
    assert not prev.exists()
    assert not list(web.glob("dist-*"))


def test_сборка_без_index_считается_упавшей(web):
    dist, prev = web / "dist", web / "dist.prev"
    with pytest.raises(update.UpdateError):
        update.build(runner=fake_npm(writes_index=False), web=web, dist=dist, prev=prev)
    assert (dist / "index.html").read_text(encoding="utf-8") == "старая"


def test_второй_успех_вытесняет_прошлую_копию(web):
    dist, prev = web / "dist", web / "dist.prev"
    update.build(runner=fake_npm(), web=web, dist=dist, prev=prev)
    (dist / "index.html").write_text("вторая", encoding="utf-8")
    update.build(runner=fake_npm(), web=web, dist=dist, prev=prev)
    assert (prev / "index.html").read_text(encoding="utf-8") == "вторая"


def test_сборка_нужна_только_если_менялся_клиент_или_dist_нет(tmp_path):
    dist = tmp_path / "dist"
    assert update.needs_build([], dist) is True          # dist ещё нет
    dist.mkdir()
    assert update.needs_build(["README.md"], dist) is False
    assert update.needs_build(["apps/web/src/App.tsx"], dist) is True
    assert update.needs_build(["apps/web/package-lock.json"], dist) is True
