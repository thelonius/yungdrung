"""Модели запросов и ответов для задач (§2.1 спецификации среза 2). Владелец B1.

Три общие крошечные модели ниже импортируют также B2 и B3: `FieldError` —
элемент `errors[]` контракта, `FieldWarning` — мягкое предупреждение
(`soft_warnings`, ключ `warning`, не `error`), `OkResult` — ответ операции без
данных. `core/models.py` остаётся за лентой и отметками и не правится.
"""
from __future__ import annotations

from pydantic import BaseModel


class FieldError(BaseModel):
    field: str | None
    error: str


class FieldWarning(BaseModel):
    field: str
    warning: str


class OkResult(BaseModel):
    ok: bool = True
