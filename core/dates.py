"""Разбор человеческой даты с подписью для формы и быстрый ввод одной строкой.

Сам разбор — `domain.ru_dates`; здесь он оборачивается в ответ контракта:
подпись для человека и признак «в прошлом». Раньше подпись собирал
`server.py._parse_date` — то есть HTTP-слой, и CLI её не видел вовсе.
"""
from datetime import date, datetime

from core.models import ExtractResult, WhenResult
from domain.ru_dates import as_date, extract_when, parse_date_input, weekday_name

НЕ_ПОНЯЛ = "Дату не понял. Можно: 18.08 · 15 марта · завтра · +3 · пн · полдесятого · через час"


def describe(parsed, today: date) -> tuple[str, bool]:
    """Подпись к разобранной дате: «сегодня», «завтра», иначе число и сколько
    дней до него; время добавляется, если было.

    День недели называется всегда. Вопрос «а какой это день» человек задаёт
    себе на каждом контроле: попасть им в субботу или на день, когда и так
    некуда, видно только по названию. Считается здесь, а не в браузере, —
    оболочка не пересказывает данные ядра своими словами (CONTRACT.md).
    """
    день = as_date(parsed)
    дни = (день - today).days
    имя = {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get(дни)
    части = [имя or f"{день:%d.%m.%Y}", weekday_name(день)]
    if isinstance(parsed, datetime):
        части.append(f"в {parsed:%H:%M}")
    if имя is None:
        части.append(f"через {дни} дн." if дни > 0 else f"{-дни} дн. назад")
    return ", ".join(части), дни < 0


def parse_when(text, today: date, now: datetime | None = None) -> WhenResult:
    """`now` нужен пресету «через час»: он считается от настоящего момента, и
    только у окна контроля он есть. Без `now` фраза остаётся нераспознанной,
    как и любой другой непонятный текст."""
    try:
        parsed = parse_date_input(text, today, now=now)
    except (ValueError, TypeError):
        return WhenResult(ok=False, error=НЕ_ПОНЯЛ)
    if parsed is None:
        return WhenResult(ok=True)
    подпись, прошло = describe(parsed, today)
    return WhenResult(ok=True, date=parsed.isoformat(), label=подпись, past=прошло)


def extract(text, today: date, now: datetime | None = None) -> ExtractResult:
    title, moment, span = extract_when(text, today, now=now)
    if moment is None:
        return ExtractResult(title=title)
    подпись, прошло = describe(moment, today)
    return ExtractResult(title=title, date=moment.isoformat(), label=подпись,
                         past=прошло, span=span)
