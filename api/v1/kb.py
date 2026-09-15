"""База знаний в форме (§1.2 спецификации среза 2, Р5): типизированные
обёртки над `engine.cmd_kb_*` до переезда базы знаний в `core` (срез 3).
Владелец B1.

`engine` здесь импортируется намеренно — исключение из общего правила
«`core` не импортирует `engine`»: этот файл не в `core`, а в `api`, и он и
есть тот самый нетипизированный маршрут, «видимый в коде по импорту шима»
(REFACTOR.md). Модели гипотез объявлены прямо здесь, а не в
`core/models_tasks.py`: они оборачивают `engine`, а не `core`, и в срезе 3
переедут вместе с базой знаний целиком.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

import engine
from api.deps import moment
from core.errors import NotFound
from core.models_tasks import FieldError, OkResult

router = APIRouter(prefix="/api/v1", tags=["kb"])


class Hypothesis(BaseModel):
    """Форма из `kb.py:373-383` (`_hypothesis`). `extra="allow"`, чтобы поля,
    о которых этот файл не знает, не терялись при обратной пересылке —
    `reject`/`confirm` шлют гипотезу назад ровно такой, какой её показал `scan`."""

    model_config = ConfigDict(extra="allow")

    entry_id: int | str
    title: str
    via: str | None = None
    source: str | None = None
    matched: str
    offset_start: int
    offset_end: int
    confirmed: bool = False


class KbScanIn(BaseModel):
    text: str
    task_id: int | None = None


class KbScanResult(BaseModel):
    hypotheses: list[Hypothesis]
    confirmed: list[Hypothesis]
    kb_broken: list[str]


class KbRejectIn(BaseModel):
    mention: Hypothesis
    mute: bool = False


class KbConfirmIn(BaseModel):
    mentions: list[Hypothesis]


class KbConfirmResult(BaseModel):
    ok: bool
    links: list[dict]
    errors: list[FieldError]


def _task_owner(task_id: int) -> tuple[str, str]:
    """`task_id` → (source_type, source_id): владелец ссылки базы знаний по
    сей день адресуется названием, не id (Р3) — `engine.cmd_kb_*` ничего
    другого не понимают."""
    task = engine.get_store().task_by_id(task_id)
    if task is None:
        raise NotFound(f"нет задачи с id {task_id}")
    return "task", task["path"].stem


def _confirmed_to_hypothesis(rec: dict, titles: dict) -> dict:
    """Подтверждённая ссылка из `kb.LinkStore` несёт `kb_entry_id`, а не
    `entry_id`/`title` гипотезы (`kb.link_record`) — это запись о решённом
    факте, не о догадке, и полей гипотезы в ней часть не было никогда.
    Название подставляем по `kb_entry_id` из уже собранного индекса; остальные
    поля идут как есть — `Hypothesis(extra="allow")` их не теряет."""
    return {**rec, "entry_id": rec.get("kb_entry_id"),
            "title": titles.get(rec.get("kb_entry_id"), ""), "confirmed": True}


@router.post("/kb/scan", response_model=KbScanResult)
def scan(body: KbScanIn, now: datetime = Depends(moment)):
    source_type = source_id = None
    if body.task_id is not None:
        source_type, source_id = _task_owner(body.task_id)
    raw = engine.cmd_kb_scan(
        SimpleNamespace(text=body.text, source_type=source_type, source_id=source_id),
        now.date())
    titles = {e["id"]: e["title"] for e in engine.load_kb_entries()}
    return KbScanResult(
        hypotheses=raw["hypotheses"],
        confirmed=[Hypothesis(**_confirmed_to_hypothesis(r, titles))
                  for r in raw["confirmed"]],
        kb_broken=raw["kb_broken"])


@router.post("/kb/reject", response_model=OkResult)
def reject(body: KbRejectIn, now: datetime = Depends(moment)):
    engine.cmd_kb_reject(
        SimpleNamespace(mention=body.mention.model_dump(), mute=body.mute), now.date())
    return OkResult()


@router.post("/tasks/{task_id}/kb-confirm", response_model=KbConfirmResult)
def confirm(task_id: int, body: KbConfirmIn, now: datetime = Depends(moment)):
    source_type, source_id = _task_owner(task_id)
    raw = engine.cmd_kb_confirm(
        SimpleNamespace(source_type=source_type, source_id=source_id,
                        mentions=[m.model_dump() for m in body.mentions]),
        now.date())
    return KbConfirmResult(ok=raw["ok"], links=raw["links"], errors=raw["errors"])
