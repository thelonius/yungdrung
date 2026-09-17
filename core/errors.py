"""Исключения ядра. Адаптеры переводят их сами: CLI — в код возврата 1 и текст
в stderr, HTTP — в 404/409/422 с телом `{"ok": false, "errors": [...]}`.

Раньше конфликт состояния поднимался `sys.exit(text)` прямо из команд, а
`server.py` ловил `SystemExit` в четырёх местах — и в пятом забыть было некому.
Типизированные исключения делают контракт свойством структуры: не поймал —
упало заметно, а не ушло оборванным соединением.
"""


class CoreError(Exception):
    """Базовый класс: всё, что ядро сообщает оболочке как отказ."""


class NotFound(CoreError):
    """Задачи или шага с таким адресом нет."""


class Ambiguous(CoreError):
    """Кусок названия подходит нескольким задачам (только для поиска по
    названию — по `task_id` неоднозначности не бывает)."""

    def __init__(self, candidates):
        self.candidates = list(candidates)
        super().__init__("подходит несколько: " + ", ".join(self.candidates))


class Conflict(CoreError):
    """Состояние уже не то, на которое рассчитывала операция: шаг закрыт,
    задача отменена, чужая запись выиграла гонку (issue #11)."""

    def __init__(self, message, actual_status=None):
        self.actual_status = actual_status
        super().__init__(message)


class Unmarkable(CoreError):
    """Шаг существует, но отметок не принимает: группа подшагов закрывается
    сама, когда закрыты её дети."""


class ValidationError(CoreError):
    """Структурная ошибка ввода: список `{"field": ..., "error": ...}`, чтобы
    форма подсветила все места сразу, а бот собрал понятную фразу
    (`CONTRACT.md`). `field` может быть None — ошибка про запрос целиком."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(e["error"] for e in self.errors))

    @classmethod
    def single(cls, field, error):
        return cls([{"field": field, "error": error}])
