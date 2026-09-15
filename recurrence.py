"""Шим совместимости: модуль переехал в `domain/recurrence.py` (REFACTOR.md,
срез 2, Р11). `engine.py`, `templates.py` и тесты зовут его по старому адресу;
шим удаляется в срезе 5 вместе с `engine.py`.

Не `from domain.recurrence import *`: звёздочка копирует значения, и тогда
`monkeypatch.setattr(recurrence, "MAX_PERIODS", …)` в тестах менял бы копию,
а не константу, которую читают функции, и приватные `_normalized` не доехали
бы. Подмена в `sys.modules` отдаёт под старым именем тот же объект модуля.
"""
import sys

import domain.recurrence as _moved

sys.modules[__name__] = _moved
