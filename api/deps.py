"""Зависимости маршрутов: контекст ядра и «сейчас».

Путь к стору пока берётся из `engine.VAULT`, а не читается заново: легаси-
маршруты зовут `engine.cmd_*`, и два разных представления о том, где лежит
стор, дали бы два стора в одном процессе. Тесты подменяют `engine.VAULT`
через monkeypatch — и v1, и легаси видят подмену. В срезе 5 вместе с
`engine.py` уйдёт и эта зависимость, останется конфигурация приложения.
"""
from datetime import date, datetime

import engine
from core.context import Context
from domain.ru_dates import parse_date_input


def ctx() -> Context:
    return Context(engine.VAULT)


def moment(now: str | None = None) -> datetime:
    """Момент «сейчас». Параметр запроса `now` (ISO) — для проверок и разбора
    воспроизводимых сценариев, тот же смысл, что у `--today` в CLI."""
    if now:
        return parse_date_input(now, date.today())
    return datetime.now()
